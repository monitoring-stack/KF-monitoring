

import os
import json
import base64
import urllib.request
import urllib.error
from html import escape
from datetime import datetime, timedelta, timezone

import feedparser
from bs4 import BeautifulSoup

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm

from helpers import date_de, classify

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")          # z GitHub Secrets, např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce (Stefan)
CC = os.getenv("CC")
BCC = os.getenv("BCC")

# v mailu chceme 15 hlavních článků
MAX_TOP = int(os.getenv("MAX_TOP", "15"))

# volitelný vstup pro Google Reviews (medium varianta)
# REVIEWS_JSON = JSON pole objektů:
# [{ "region": "...", "store": "...", "avg": 4.2, "delta": -0.1, "count_24h": 12, "flag": "negativer Trend" }, ...]
REVIEWS_JSON = os.getenv("REVIEWS_JSON", "[]")

FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]


# ================== NEWS FETCH ==================





def fetch_news():
    """
    Načte články z Google News, vyčistí summary, odfiltruje jen zmínky Kauflandu
    a seřadí podle score (desc).

    NAVÍC:
    - bere jen články z posledních 24 hodin (podle published/updated v RSS)
    """
    seen = set()
    items = []

    # časový řez – posledních 24 h
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=1)

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)

            # datum publikace/aktualizace z RSS (pokud chybí, bereme článek jako "čerstvý")
            pub_struct = getattr(e, "published_parsed", None) or getattr(
                e, "updated_parsed", None
            )
            if pub_struct is not None:
                pub_dt = datetime(
                    pub_struct.tm_year,
                    pub_struct.tm_mon,
                    pub_struct.tm_mday,
                    pub_struct.tm_hour,
                    pub_struct.tm_min,
                    pub_struct.tm_sec,
                    tzinfo=timezone.utc,
                )
                if pub_dt < cutoff:
                    # starší než 24 h → přeskočit
                    continue

            title = e.title or ""
            desc_html = getattr(e, "summary", "") or ""
            desc = BeautifulSoup(desc_html, "html.parser").get_text()
            host, typ, score = classify(link, title)

            text = f"{title} {desc}".lower()
            if "kaufland" not in text:
                # ignorujeme články, které se Kauflandu netýkají
                continue

            items.append(
                {
                    "title": title.strip(),
                    "url": link,
                    "summary": desc.strip(),
                    "source": host,
                    "type": typ,
                    "score": score,
                    "why": "relevant" if score >= 4 else "beobachten",
                 }
            )

    # seřadit podle score (nejdůležitější nahoře)
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


# ================== GOOGLE REVIEWS (MIN + MEDIUM) ==================


def get_google_reviews_data():
    """
    MIN varianta:
      - pokud REVIEWS_JSON není vyplněné → vrátí prázdný list → zobrazí se vysvětlující řádek.

    MEDIUM varianta:
      - pokud REVIEWS_JSON obsahuje JSON seznam objektů:
        {region, store, avg, delta, count_24h, flag}
        → seřadí podle 'priority' a vrátí TOP 5.
    """
    try:
        raw = REVIEWS_JSON.strip()
        if not raw or raw == "[]":
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    def prio(r):
        delta = abs(r.get("delta") or 0.0)
        count = r.get("count_24h") or 0
        # jednoduché skóre: velká změna ratingu + hodně nových recenzí
        return delta * 10 + count

    data_sorted = sorted(data, key=prio, reverse=True)
    return data_sorted[:5]


# ================== TOPIC KLASIFIKACE PRO PDF ==================

TOPIC_KEYWORDS = {
    "Rückruf / Sicherheit": [
        "rückruf",
        "warnung",
        "produktsicherheit",
        "gefährdung",
        "gesundheitsgefahr",
        "verunreinigung",
    ],
    "Hygiene / Qualität": [
        "hygiene",
        "verschmutzt",
        "schimmel",
        "ekel",
        "verdorben",
        "qualität",
        "abgelaufen",
        "abgelaufene",
    ],
    "Preise / Aktionen": [
        "preis",
        "preise",
        "rabatt",
        "rabatte",
        "angebot",
        "angebote",
        "aktion",
        "aktionen",
        "billig",
        "teuer",
        "inflation",
        "günstig",
    ],
    "Filialen / Expansion": [
        "neue filiale",
        "filiale eröffnet",
        "eröffnung",
        "neuer markt",
        "umbau",
        "standort",
        "expansion",
        "verkaufsfläche",
    ],
    "Personal / Arbeitsbedingungen": [
        "mitarbeiter",
        "arbeitnehmer",
        "streik",
        "tarif",
        "gehalt",
        "lohn",
        "arbeitsbedingungen",
        "personal",
        "team",
    ],
    "Reputation / Medien": [
        "shitstorm",
        "boykott",
        "kritik",
        "skandal",
        "image",
        "pr-kampagne",
        "werbung",
        "kampagne",
        "social media",
        "tiktok",
        "instagram",
    ],
}


def topic_of(item):
    """
    Přiřadí článek do jednoho z témat na základě klíčových slov
    v titulku + shrnutí. Pokud nic nepasuje, vrátí 'Sonstiges'.
    """
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                return topic
    return "Sonstiges"


# ================== PDF – MAGAZINE LAYOUT ==================


def shorten_url(url: str, max_len: int = 60) -> str:
    """Zkrácená URL pro zobrazení v PDF."""
    if not url:
        return ""
    u = url.replace("https://", "").replace("http://", "")
    if len(u) <= max_len:
        return u
    return u[: max_len - 1] + "…"


def build_pdf_magazine(filename, items):
    """
    Vytvoří „magazine style“ PDF:
      - rozdělení dle témat (Rückruf, Hygiene, Preise, Filialen, Personal, Reputation, Sonstiges)
      - uvnitř každé sekce články v pořadí podle score (items už jsou seřazené)
      - každý článek jako blok: #, titulek, meta, summary, link (klikací)
    """
    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    h2 = styles["Heading2"]
    h3 = styles["Heading3"]

    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=9,
        textColor="grey",
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
    )
    link_style = ParagraphStyle(
        "Link",
        parent=styles["Normal"],
        fontSize=8.5,
        textColor="blue",
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    story = []

    # Titulek + datum
    story.append(
        Paragraph("Kaufland Media & Review Briefing – Deutschland", title_style)
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph(date_de(TIMEZONE), meta_style))
    story.append(Spacer(1, 14))

    # Rozdělení podle témat – zachovává pořadí items (tedy score)
    buckets = {}
    for it in items:
        t = topic_of(it)
        buckets.setdefault(t, []).append(it)

    topics_order = [
        "Rückruf / Sicherheit",
        "Hygiene / Qualität",
        "Preise / Aktionen",
        "Filialen / Expansion",
        "Personal / Arbeitsbedingungen",
        "Reputation / Medien",
        "Sonstiges",
    ]

    global_idx = 1

    for topic in topics_order:
        topic_items = buckets.get(topic, [])
        if not topic_items:
            continue

        # Nadpis sekce
        story.append(Paragraph(topic, h2))
        story.append(Spacer(1, 8))

        for it in topic_items:
            # Nadpis článku
            story.append(
                Paragraph(f"{global_idx}. {escape(it['title'])}", h3)
            )

            # Meta informace
            meta_parts = []
            if it.get("source"):
                meta_parts.append(escape(it["source"]))
            if it.get("type"):
                meta_parts.append(escape(it["type"]))
            if it.get("why"):
                meta_parts.append("Grund: " + escape(it["why"]))
            if meta_parts:
                story.append(Paragraph(" · ".join(meta_parts), meta_style))

            # Shrnutí
            if it.get("summary"):
                story.append(Paragraph(escape(it["summary"]), body_style))

            # Klikací odkaz
            if it.get("url"):
                short = shorten_url(it["url"])
                story.append(
                    Paragraph(
                        f"<link href='{it['url']}' color='blue'>{escape(short)}</link>",
                        link_style,
                    )
                )

            story.append(Spacer(1, 10))
            global_idx += 1

        story.append(Spacer(1, 16))

    doc.build(story)


# ================== RESEND EMAIL ==================


def send_via_resend(subject, html, pdf_name):
    """Odešle e-mail přes Resend API s PDF přílohou."""
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
    # 1) Načtení a seřazení news
    items_raw = fetch_news()

    # 2) Dedup URL + pořadí podle score
    seen = set()
    all_items = []
    for it in items_raw:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        all_items.append(it)

    # Top 15 do mailu
    top_items = all_items[:MAX_TOP]

    # Google reviews data
    reviews = get_google_reviews_data()

    # 3) Načtení HTML šablony
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # === Executive summary (DE) ===
    executive_summary_html = """
<p><strong>Insight:</strong> 15 kuratierte, virale Kaufland-Erwähnungen pro Tag – nach internem Score geordnet, im PDF zusätzlich nach Themen gruppiert.</p>
<p><strong>Implikation:</strong> Schneller Überblick über Themen, Risiken und Chancen direkt im E-Mail; detaillierte thematische Übersicht im PDF.</p>
<p><strong>Aktion:</strong> Google Reviews sind im Pilotmodus angebunden; Filialdaten können über REVIEWS_JSON ergänzt und priorisiert werden.</p>
""".strip()

    # === Virale Erwähnungen – Top 15 do mailu ===
    top_items_html = []
    for i, it in enumerate(top_items, start=1):
        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("why"):
            meta_parts.append("Grund: " + escape(it["why"]))
        meta = " · ".join(meta_parts)

        top_items_html.append(
            f"""
<li class="item">
  <span class="rank">{i}.</span>
  <span>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </span>
</li>""".strip()
        )

    # === Google Reviews – tabulka ===
    review_rows = []
    for r in reviews:
        delta = r.get("delta")
        delta_class = "pos" if (delta is not None and delta >= 0) else "neg"
        review_rows.append(
            f"""
<tr>
  <td>{escape(r.get('region','–'))} – {escape(r.get('store','–'))}</td>
  <td>{r.get('avg','–')}</td>
  <td class="{delta_class}">{delta if delta is not None else '–'}</td>
  <td>{r.get('count_24h','–')}</td>
  <td>{escape(r.get('flag','–'))}</td>
</tr>""".strip()
        )

    if not review_rows:
        review_rows = [
            """<tr><td colspan="5" class="muted">
Noch keine Filial-spezifischen Daten hinterlegt (Pilotmodus).
Über REVIEWS_JSON können Filialen mit vielen neuen oder auffälligen Bewertungen eingebunden werden.
</td></tr>"""
        ]

    reviews_note = "Δ = Veränderung der Ø-Bewertung in den letzten 24 Stunden (sofern Daten vorliegen)."

    # === Urgent / Rumors – zatím prázdné (řeší urgent_watcher) ===
    urgent_block_html = ""
    rumors_block_html = ""

    # === Dosazení do HTML šablony ===
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
        "{urgent_block_html}": urgent_block_html,
        "{rumors_block_html}": rumors_block_html,
        # tyto placeholdery v aktuální šabloně nepoužíváš, ale replace je nevadí
        "{international_html}": "",
        "{other_de_html}": "",
    }

    for key, val in replacements.items():
        html = html.replace(key, val)

    # === PDF + odeslání ===
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf_magazine(pdf_name, all_items)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
