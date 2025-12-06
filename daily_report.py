import os
import re
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from html import escape

import feedparser
from bs4 import BeautifulSoup
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

from helpers import date_de, classify

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
CC = os.getenv("CC")
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "10"))

FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

# Témata – první shoda vyhrává
TOPIC_RULES = [
    ("Qualität & Rückruf", r"rückruf|lebensgefährlich|verzehren|qualität|verunreinigung"),
    ("Hygiene & Filialbetrieb", r"hygiene|hygieneskandal|schimmel|mäuse|kakerlaken|filiale|markt"),
    ("Reputationsrisiken", r"boykott|shitstorm|skandal|kritik|verbraucherzentrale|protest"),
    ("Preise & Wettbewerb", r"preis|discount|angebot|aktion|billig|teuer|aldi|lidl|rewe|edeka|wettbewerb"),
    ("Expansion & Standorte", r"neu(er)?öffnung|eröffnung|erweitert|umbau|modernisiert|standort"),
    ("Nachhaltigkeit & ESG", r"nachhaltig|klima|umwelt|co2|esg|tierwohl|bio"),
]

CRITICAL_KEYWORDS = re.compile(
    r"rückruf|skandal|boykott|shitstorm|krise|gesundheit|lebensgefährlich",
    re.I,
)

FOLLOWUP_PATTERNS = re.compile(
    r"""
    wiedereröffnung|
    wieder\s+geöffnet|
    öffnet\s+wieder|
    modernisiert|
    nach\s+.*skandal.*öffnet|
    problem\s+behoben|
    skandal\s+überstanden|
    entwarnung
    """,
    re.X | re.I,
)


# ================== POMOCNÉ FUNKCE ==================


def parse_pubdate(entry):
    """Vrátí datetime (UTC) nebo None."""
    dt_struct = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if not dt_struct:
        return None
    return datetime(*dt_struct[:6])


def is_recent(entry, hours=36):
    dt = parse_pubdate(entry)
    if not dt:
        # když nemáme datum, raději ponecháme
        return True
    now_utc = datetime.utcnow()
    return now_utc - dt <= timedelta(hours=hours)


def assign_topic(text: str) -> str:
    tl = text.lower()
    for topic, pattern in TOPIC_RULES:
        if re.search(pattern, tl, re.I):
            return topic
    return "Sonstiges"


def is_international(host: str, title: str) -> bool:
    host = (host or "").lower()
    if not host.endswith(".de"):
        return True
    if "österreich" in title.lower() or "polen" in title.lower():
        return True
    return False


def is_critical_topic(topic: str, text: str) -> bool:
    """
    Rozhoduje, jestli článek považujeme za „kritický“.

    Nově:
    - rozeznáváme follow-up / Entwarnung: „öffnet wieder“, „modernisiert“, „Wiedereröffnung“…
      → tyto články *nejsou* kritické, i když je v textu „Skandal“, atd.
    """
    tl = text.lower()

    # 1) Follow-up / Entwarnung – znovuotevření po skandálu, modernizace apod.
    if FOLLOWUP_PATTERNS.search(tl):
        return False

    # 2) Primárně rizikové kategorie – jen pokud to není follow-up (viz výše)
    if topic in ("Qualität & Rückruf", "Hygiene & Filialbetrieb", "Reputationsrisiken"):
        return True

    # 3) Fallback podle klíčových slov
    if CRITICAL_KEYWORDS.search(tl):
        return True

    return False


# ================== NEWS FETCH ==================


def fetch_news():
    seen = set()
    items = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)

            if not is_recent(e, hours=36):
                continue

            title = e.title
            desc = BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text()
            host, typ, base_score = classify(link, title)

            text_for_topic = f"{title} {desc}"
            topic = assign_topic(text_for_topic)

            critical = is_critical_topic(topic, text_for_topic)
            international = is_international(host, title)

            score = base_score
            if critical:
                score += 2
            if international:
                score += 1

            pub_dt = parse_pubdate(e)
            when_str = None
            if pub_dt:
                when_str = pub_dt.strftime("%d.%m. %H:%M")

            items.append(
                {
                    "title": title,
                    "url": link,
                    "summary": (desc[:260] + "…") if len(desc) > 260 else desc,
                    "source": host,
                    "type": typ,
                    "score": score,
                    "topic": topic,
                    "critical": critical,
                    "international": international,
                    "when": when_str,
                }
            )

    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def group_by_topic(items):
    buckets = {}
    for it in items:
        buckets.setdefault(it["topic"], []).append(it)

    grouped = []
    for topic, lst in buckets.items():
        lst_sorted = sorted(lst, key=lambda x: x["score"], reverse=True)
        max_score = lst_sorted[0]["score"] if lst_sorted else 0
        grouped.append((topic, lst_sorted, max_score))

    # Kategorie podle nejsilnějšího článku
    grouped.sort(key=lambda x: x[2], reverse=True)
    return grouped


# ================== PDF REPORT ==================


def build_pdf(filename, grouped_items, meta):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h1 = styles["Heading1"]
    normal = styles["Normal"]

    doc = SimpleDocTemplate(
        filename,
        pagesize=landscape(A4),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    story = []

    # Hlavní titulek
    story.append(
        Paragraph(
            f"DE Monitoring – privat | {meta['date_str']}",
            title_style,
        )
    )
    story.append(Spacer(1, 6))

    intro_text = (
        f"Insgesamt {meta['total_count']} Artikel im Auswertungszeitraum. "
        f"{meta['critical_count']} davon als kritisch eingestuft, "
        f"{meta['international_count']} virale / internationale Erwähnungen. "
        f"Schwerpunktthemen: {meta['theme_summary']}."
    )
    story.append(Paragraph(intro_text, normal))
    story.append(Spacer(1, 10))

    # Témata
    for topic, lst, _max_score in grouped_items:
        story.append(Paragraph(topic, h1))
        story.append(Spacer(1, 4))

        for it in lst:
            flags = []
            if it["critical"]:
                flags.append("■ Kritisch")
            if it["international"]:
                flags.append("● Virale Erwähnung")
            flags_str = " · ".join(flags) if flags else ""

            header = f"{escape(it['title'])} [{escape(it['source'])}]"
            if flags_str:
                header += f" · {flags_str}"

            story.append(Paragraph(header, normal))
            if it.get("summary"):
                story.append(Paragraph(escape(it["summary"]), normal))
            story.append(Spacer(1, 2))

        story.append(Spacer(1, 8))

    doc.build(story)


# ================== RESEND EMAIL ==================


def send_via_resend(subject, html, pdf_name):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY env variable is missing.")
    if not EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM env variable is missing.")
    if not EMAIL_TO:
        raise RuntimeError("EMAIL_TO env variable is missing.")

    with open(pdf_name, "rb") as f:
        pdf_bytes = f.read()
    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    payload = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html,
        "attachments": [
            {
                "filename": os.path.basename(pdf_name),
                "content": pdf_b64,
            }
        ],
    }

    if CC:
        payload["cc"] = [x.strip() for x in CC.split(",") if x.strip()]
    if BCC:
        payload["bcc"] = [x.strip() for x in BCC.split(",") if x.strip()]

    data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data_bytes,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        print("Resend response:", resp.status, body)


# ================== EMAIL RENDERING ==================


def build_email_html(items, grouped_topics):
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    total_count = len(items)
    critical_count = sum(1 for it in items if it["critical"])
    international_count = sum(1 for it in items if it["international"])

    # Top 3 Schlagzeilen
    top_items = items[: min(MAX_TOP, len(items), 3)]
    top_items_html = []
    for i, it in enumerate(top_items, start=1):
        badges = []
        if it["critical"]:
            badges.append("⚠️ Kritisch")
        if it["international"]:
            badges.append("🌍 Virale Erwähnung")
        badges_str = " · ".join(badges) if badges else ""

        meta_parts = [escape(it.get("source", ""))]
        if it.get("when"):
            meta_parts.append(it["when"])
        if badges_str:
            meta_parts.append(badges_str)
        meta = " · ".join(meta_parts)

        top_items_html.append(
            f"""
<li class="item">
  <div class="rank">{i}</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </div>
</li>""".strip()
        )

    # Tematický souhrn (1–2 největší témata)
    theme_counts = [(topic, len(lst)) for topic, lst, _ in grouped_topics]
    theme_counts.sort(key=lambda x: x[1], reverse=True)
    top_themes = [f"{t} ({c})" for t, c in theme_counts[:2]]
    theme_summary = ", ".join(top_themes) if top_themes else "–"

    executive_summary_html = f"""
<p>Heute wurden insgesamt <strong>{total_count}</strong> relevante Erwähnungen zu Kaufland erfasst.</p>
<p>Davon sind <strong>{critical_count}</strong> als potentiell kritisch (Rückruf, Skandal, Boykott, Krise) eingestuft.
Zusätzlich gibt es <strong>{international_count}</strong> virale / internationale Erwähnungen.</p>
<p>Thematisch dominieren heute: <strong>{theme_summary}</strong>.
Vollständige Liste inkl. thematischer Einordnung und aller Quellen im angehängten PDF.</p>
""".strip()

    # Google Reviews – zatím jen statický placeholder
    review_rows = [
        """<tr><td colspan="5" class="muted">
Noch keine auffälligen Veränderungen in den vorliegenden Google-Reviews-Daten (Pilotmodus oder stabile Lage).
</td></tr>"""
    ]
    reviews_note = "Gefiltert nach Filialen mit ≥ 3 neuen Reviews oder ≥ 0.2 Veränderung der Ø-Bewertung (24h)."

    html = template_str
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": EMAIL_TO or "",
        "{executive_summary_html}": executive_summary_html,
        "{top_count}": str(len(top_items)),
        "{top_headlines_html}": "\n".join(top_items_html),
        "{reviews_table_rows_html}": "\n".join(review_rows),
        "{reviews_note}": reviews_note,
        "{urgent_block_html}": "",
        "{rumors_block_html}": "",
        "{international_html}": "",  # international je teď rozpuštěné v hlavním seznamu
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    return html, theme_summary, total_count, critical_count, international_count


# ================== MAIN ==================


def main():
    items = fetch_news()
    if not items:
        print("No items fetched.")
        return

    grouped = group_by_topic(items)

    html, theme_summary, total_count, critical_count, international_count = (
        build_email_html(items, grouped)
    )

    meta = {
        "date_str": date_de(TIMEZONE),
        "theme_summary": theme_summary,
        "total_count": total_count,
        "critical_count": critical_count,
        "international_count": international_count,
    }

    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, grouped, meta)

    subject = f"📰 Kaufland Media & Review Briefing – Deutschland | {meta['date_str']}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
