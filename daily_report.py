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
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors

from helpers import date_de  # classify necháme v helpers, ale kritičnost řešíme tady

# ================== KONFIGURACE / ENV ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")  # např. "Kaufland Monitoring <reports@…>"
EMAIL_TO = os.getenv("EMAIL_TO")
CC = os.getenv("CC")
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "10"))

FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

# ================== POMOCNÉ FUNKCE ==================


def _now_tz():
    """'Teď' v nastavené timezone (bez řešení letního času – pro filtr 24h to stačí)."""
    # jednoduše vezmeme UTC a posuneme podle offsetu, aby to bylo stabilní
    # (přesný timezone handling by byl přes pytz/zoneinfo, ale nechceme tahat další lib).
    return datetime.utcnow()


def is_recent(entry, hours=30):
    """Vrátí True, pokud je článek mladší než `hours` hodin."""
    if not hasattr(entry, "published_parsed") or entry.published_parsed is None:
        return True  # raději nic nevyhazovat, když chybí datum
    published = datetime(*entry.published_parsed[:6])
    delta = _now_tz() - published
    return delta <= timedelta(hours=hours)


def thematics_for_item(title, summary, source_host):
    """
    Určí:
      - topic: 'Qualität & Rückruf' / 'Hygiene & Filialbetrieb' / 'Sonstiges'
      - is_critical: bool
      - is_international: bool
    podle jednoduchých, ale rozumných heuristik.
    """

    text = f"{title} {summary}".lower()

    # Silná negativní slova
    strong_negative = [
        "rückruf",
        "skandal",
        "boykott",
        "krise",
        "shitstorm",
        "ekel",
        "gammelfleisch",
        "hygienemangel",
        "hygienemängel",
        "vergiftung",
        "gefährlich",
        "lebensgefährlich",
        "ermittlungen",
        "anzeige",
        "verklagt",
        "strafe",
        "abmahnung",
    ]

    hygiene_words = [
        "hygiene",
        "hygieneskandal",
        "hygienemangel",
        "hygienemängel",
        "schimmel",
        "schmutz",
        "gammel",
        "verunreinigt",
    ]

    quality_words = [
        "rückruf",
        "produktwarnung",
        "produktrueckruf",
        "verzehren",
        "verderb",
        "mindesthaltbarkeitsdatum",
    ]

    # Slova, která naznačují spíš expanzi / otevírání (blokují "kritisch")
    expansion_words = [
        "eröffnung",
        "neueröffnung",
        "neueröffnung",
        "öffnet",
        "öffnet mehrere neue filialen",
        "neue filiale",
        "neue filialen",
        "neuer markt",
        "eröffnet neu",
        "modernisiert",
        "modernisierung",
        "umbau",
        "sanierung",
    ]

    has_strong_neg = any(w in text for w in strong_negative + hygiene_words)
    has_expansion = any(w in text for w in expansion_words)

    # Kritické pouze, pokud je opravdu negativní tón a současně to není čistá expanze/otevření
    is_critical = bool(has_strong_neg and not has_expansion)

    # Témata
    if any(w in text for w in quality_words):
        topic = "Qualität & Rückruf"
    elif any(w in text for w in hygiene_words) or "filiale" in text or "markt" in text:
        topic = "Hygiene & Filialbetrieb"
    else:
        topic = "Sonstiges"

    # International / virální – zjednodušeně: pokud nejsme .de nebo se zmiňuje zahraničí
    international_keywords = [
        "österreich",
        "polen",
        "tschechien",
        "tschech",
        "rumänien",
        "bulgarien",
        "slowakei",
        "kroatien",
        "international",
    ]
    is_international = (not source_host.endswith(".de")) or any(
        kw in text for kw in international_keywords
    )

    return {
        "topic": topic,
        "is_critical": is_critical,
        "is_international": is_international,
    }


def fetch_news():
    seen = set()
    items = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            if not is_recent(e):
                continue

            link = e.link
            if link in seen:
                continue
            seen.add(link)

            title = e.title
            desc = BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text()

            # Zdroj / host
            host = re.sub(r"^https?://", "", link).split("/")[0]

            meta = thematics_for_item(title, desc, host)

            # Skóre pro řazení – základ: kritické a silně negativní nahoru
            score = 0
            if meta["is_critical"]:
                score += 3
            if meta["is_international"]:
                score += 1
            # preferujeme serióznější domény vs. čisté aggregation (news.google.com)
            if host != "news.google.com":
                score += 1

            items.append(
                {
                    "title": title,
                    "url": link,
                    "summary": (desc[:260] + "…") if len(desc) > 260 else desc,
                    "source": host,
                    "score": score,
                    **meta,
                }
            )

    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def bucket_by_topic(items):
    buckets = {
        "Qualität & Rückruf": [],
        "Hygiene & Filialbetrieb": [],
        "Sonstiges": [],
    }
    for it in items:
        buckets.setdefault(it["topic"], []).append(it)
    # odstranit prázdné
    return {k: v for k, v in buckets.items() if v}


# ================== PDF (MAGAZINE STYL) ==================


def build_pdf(filename, items):
    topics = bucket_by_topic(items)

    doc = SimpleDocTemplate(
        filename,
        pagesize=landscape(A4),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Category",
            parent=styles["Heading1"],
            fontSize=20,
            textColor=colors.HexColor("#E60000"),
            spaceBefore=12,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ArticleTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Meta",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.grey,
            spaceAfter=4,
        )
    )

    story = []

    # Header
    story.append(
        Paragraph(
            f"DE Monitoring – privat | {date_de(TIMEZONE)}",
            styles["Title"],
        )
    )
    story.append(Spacer(1, 6))

    total = len(items)
    critical_count = sum(1 for i in items if i["is_critical"])
    international_count = sum(1 for i in items if i["is_international"])

    # Shrnutí pod titulkem
    focus_topics = ", ".join(
        f"{topic} ({len(arts)})" for topic, arts in topics.items()
    )
    summary_line = (
        f"Insgesamt {total} Artikel im Auswertungszeitraum. "
        f"{critical_count} davon als kritisch eingestuft, "
        f"{international_count} virale / internationale Erwähnungen. "
        f"Schwerpunktthemen: {focus_topics}."
    )
    story.append(Paragraph(summary_line, styles["Normal"]))
    story.append(Spacer(1, 12))

    # Kategorie
    for topic, arts in topics.items():
        story.append(Paragraph(topic, styles["Category"]))
        story.append(Spacer(1, 4))

        for it in arts:
            badges = []
            if it["is_critical"]:
                badges.append("■ Kritisch")
            if it["is_international"]:
                badges.append("● Virale / internationale Erwähnung")

            meta_line = f"{it['source']}"
            if badges:
                meta_line += " · " + " · ".join(badges)

            story.append(
                Paragraph(
                    escape(it["title"]),
                    styles["ArticleTitle"],
                )
            )
            story.append(Paragraph(escape(meta_line), styles["Meta"]))
            if it.get("summary"):
                story.append(
                    Paragraph(escape(it["summary"]), styles["Normal"])
                )
            story.append(Spacer(1, 6))

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
                "filename": pdf_name,
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

    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            print("Resend response:", resp.status, body)
    except urllib.error.HTTPError as e:
        print("Resend HTTP error:", e.code, e.read().decode("utf-8"))
        raise
    except urllib.error.URLError as e:
        print("Resend connection error:", e.reason)
        raise


# ================== MAIN ==================


def main():
    items = fetch_news()

    if not items:
        print("No items fetched – nothing to send.")
        return

    # Top N pro e-mail
    top_items = items[:5]

    # --- Načti HTML šablonu ---
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    total = len(items)
    critical_count = sum(1 for i in items if i["is_critical"])
    international_count = sum(1 for i in items if i["is_international"])

    # Dominantní témata podle počtu článků
    topic_counts = {}
    for it in items:
        topic_counts[it["topic"]] = topic_counts.get(it["topic"], 0) + 1
    sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
    topics_str = ", ".join(f"{t} ({c})" for t, c in sorted_topics)

    executive_summary_html = f"""
<p>Heute wurden insgesamt <strong>{total}</strong> relevante Erwähnungen zu Kaufland erfasst.</p>
<p>Davon sind <strong>{critical_count}</strong> als potentiell kritisch (Rückruf, Skandal, Boykott, Krise) eingestuft.
Zusätzlich gibt es <strong>{international_count}</strong> virale / internationale Erwähnungen.</p>
<p>Thematisch dominieren heute: <strong>{escape(topics_str)}</strong>. Vollständige Liste inkl. thematischer Einordnung
und aller Quellen im angehängten PDF.</p>
""".strip()

    # Top 3 Schlagzeilen HTML
    top_items_html = []
    for idx, it in enumerate(top_items, start=1):
        badges = []
        if it["is_critical"]:
            badges.append("⚠ Kritisch")
        if it["is_international"]:
            badges.append("🌍 Virale Erwähnung")

        meta_parts = [escape(it["source"])]
        if badges:
            meta_parts.append(" · ".join(badges))
        meta_html = " · ".join(meta_parts)

        top_items_html.append(
            f"""
<li class="item">
  <div class="rank">{idx}</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta_html}</div>
  </div>
</li>""".strip()
        )

    # International / virální seznam pro e-mail (max 10, mimo Top 3)
    international_items_html = []
    remaining = [it for it in items if it not in top_items and it["is_international"]]
    for it in remaining[:10]:
        badges = []
        if it["is_critical"]:
            badges.append("⚠ Kritisch")
        badges.append("🌍 Virale Erwähnung")
        meta_html = escape(it["source"])
        if badges:
            meta_html += " · " + " · ".join(badges)
        international_items_html.append(
            f"""
<li class="item">
  <div class="rank">•</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta_html}</div>
  </div>
</li>""".strip()
        )

    # Placeholdery v šabloně nahradíme simple replace (ne .format, aby se nebilo s { } v CSS)
    html = template_str
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": EMAIL_TO or "",
        "{executive_summary_html}": executive_summary_html,
        "{top_count}": str(len(top_items)),
        "{top_headlines_html}": "\n".join(top_items_html),
        "{international_html}": "\n".join(international_items_html),
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # PDF + odeslání
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, items)

    subject = f"📰 Kaufland Media & Review Briefing – Deutschland | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
