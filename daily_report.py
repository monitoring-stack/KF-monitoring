import os
import json
import base64
import urllib.request
import urllib.error
from html import escape
from datetime import datetime

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
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce (Stefan)
CC = os.getenv("CC")
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "10"))

# volitelný vstup pro Medium variantu Google Reviews:
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
    """Načte články z Google News, vyčistí summary a seřadí podle score (desc)."""
    seen = set()
    items = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)

            title = e.title or ""
            desc = BeautifulSoup(getattr(e, "summary", "") or "", "html.parser").get_text()
            host, typ, score = classify(link, title)

            # filtrujeme na Kaufland
            text = f"{title} {desc}".lower()
            if "kaufland" not in text:
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

    # seřadit podle score (nejdřív nejdůležitější)
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


# ================== KLASIFIKACE DE vs INTERNATIONAL ==================

DE_KEYWORDS = [
    "deutschland",
    "bundesweit",
    "berlin",
    "hamburg",
    "münchen",
    "köln",
    "frankfurt",
    "stuttgart",
    "leipzig",
    "nürnberg",
    "kaufland deutschland",
]


def is_de_article(item):
    """Hrubá heuristika, zda jde o DE článek."""
    host = item.get("source", "").lower()
    title = item.get("title", "").lower()
    summary = item.get("summary", "").lower()

    if host.endswith(".de"):
        return True

    text = f"{title} {summary}"
    for kw in DE_KEYWORDS:
        if kw in text:
            return True

    return False


def split_for_email(items_sorted, max_top):
    """
    Z items seřazených podle score udělá tři seznamy bez duplicit URL:
    - top_de: max_top nejlepších německých článků
    - other_de: ostatní DE články
    - intl: international články
    """
    top_de = []
    other_de = []
    intl = []
    seen_urls = set()

    for it in items_sorted:
        url = it["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)

        if is_de_article(it):
            if len(top_de) < max_top:
                top_de.append(it)
            else:
                other_de.append(it)
        else:
            intl.append(it)

    return top_de, other_de, intl


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


# ================== PDF – MAGAZINE LAYOUT ==================


def shorten_url(url: str, max_len: int = 50) -> str:
    """Zkrácená URL pro zobrazení v PDF."""
    if not url:
        return ""
    # odřízneme protokol
    u = url.replace("https://", "").replace("http://", "")
    if len(u) <= max_len:
        return u
    return u[: max_len - 1] + "…"


def build_pdf_magazine(filename, top_de, other_de, intl):
    """
    Vytvoří „magazine style“ PDF:
      - žádná velká tabulka
      - sekce:
        * Top Schlagzeilen (DE)
        * Virale Erwähnungen (DE)
        * International – Virale Erwähnungen
      - každý článek jako blok: #, titulek, meta, summary, link
    """
    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    h2 = styles["Heading2"]
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize = 9,
        textColor = "grey",
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize = 10,
        leading = 13,
    )
    link_style = ParagraphStyle(
        "Link",
        parent=styles["Normal"],
        fontSize = 8.5,
        textColor = "blue",
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
    story.append(Paragraph("Kaufland Media & Review Briefing – Deutschland", title_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(date_de(TIMEZONE), meta_style))
    story.append(Spacer(1, 14))

    # --- Top Schlagzeilen (DE) ---
    if top_de:
        story.append(Paragraph("Top Schlagzeilen (DE)", h2))
        story.append(
            Paragraph(
                "Priorisiert nach internem Relevanz-/Risiko-Score (nicht echte Reichweite).",
                meta_style,
            )
        )
        story.append(Spacer(1, 8))

        for idx, it in enumerate(top_de, start=1):
            story.append(
                Paragraph(f"{idx}. {escape(it['title'])}", styles["Heading3"])
            )

            meta_parts = []
            if it.get("source"):
                meta_parts.append(escape(it["source"]))
            if it.get("type"):
                meta_parts.append(escape(it["type"]))
            if it.get("why"):
                meta_parts.append("Grund: " + escape(it["why"]))
            if meta_parts:
                story.append(Paragraph(" · ".join(meta_parts), meta_style))

            if it.get("summary"):
                story.append(Paragraph(escape(it["summary"]), body_style))

            if it.get("url"):
                short = shorten_url(it["url"])
                story.append(
                    Paragraph(
                        f"<link href='{it['url']}' color='blue'>{escape(short)}</link>",
                        link_style,
                    )
                )

            story.append(Spacer(1, 10))

    # --- Virale Erwähnungen (DE) ---
    if other_de:
        story.append(Spacer(1, 16))
        story.append(Paragraph("Virale Erwähnungen (DE)", h2))
        story.append(Spacer(1, 6))

        for it in other_de:
            title = escape(it["title"])
            src = escape(it.get("source", ""))
            line = f"• {title} ({src})"
            story.append(Paragraph(line, body_style))

        story.append(Spacer(1, 10))

    # --- International – Virale Erwähnungen ---
    if intl:
        story.append(Spacer(1, 16))
        story.append(Paragraph("International – Virale Erwähnungen", h2))
        story.append(Spacer(1, 6))

        for it in intl:
            title = escape(it["title"])
            src = escape(it.get("source", ""))
            line = f"• {title} ({src})"
            story.append(Paragraph(line, body_style))

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
    # News
    items = fetch_news()
    top_de, other_de, intl = split_for_email(items, MAX_TOP)

    # Google Reviews data (min/medium)
    reviews = get_google_reviews_data()

    # HTML šablona
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # Executive summary (DE)
    executive_summary_html = """
<p><strong>Insight:</strong> Kuratierte Top-Schlagzeilen (1–10) für Deutschland; weitere Erwähnungen und internationale Hinweise unten.</p>
<p><strong>Implikation:</strong> Schneller Überblick über Themen, Risiken und regionale Besonderheiten in einem täglichen Briefing.</p>
<p><strong>Aktion:</strong> Google Reviews sind im Pilotmodus angebunden; mit REVIEWS_JSON können Filialen mit auffälligen Trends hervorgehoben werden.</p>
""".strip()

    # Top Schlagzeilen – HTML
    top_items_html = []
    for i, it in enumerate(top_de, start=1):
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

    # Virale Erwähnungen (DE) – HTML
    other_de_html = []
    for it in other_de:
        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("type"):
            meta_parts.append(escape(it["type"]))
        meta = " · ".join(meta_parts)

        other_de_html.append(
            f"""
<li class="item">
  <span class="rank">•</span>
  <span>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </span>
</li>""".strip()
        )

    # International – HTML
    international_items_html = []
    for it in intl:
        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("type"):
            meta_parts.append(escape(it["type"]))
        meta = " · ".join(meta_parts)

        international_items_html.append(
            f"""
<li class="item">
  <span class="rank">•</span>
  <span>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </span>
</li>""".strip()
        )

    if not international_items_html:
        international_items_html.append(
            "<li class='item'><span>Heute keine relevanten internationalen Erwähnungen.</span></li>"
        )

    # Google Reviews – tabulka HTML
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

    # Urgent / Rumors – zatím prázdné
    urgent_block_html = ""
    rumors_block_html = ""

    # Replace do šablony
    html = template_str
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": EMAIL_TO or "",
        "{executive_summary_html}": executive_summary_html,
        "{top_count}": str(len(top_de)),
        "{top_headlines_html}": "\n".join(top_items_html),
        "{other_de_html}": "\n".join(other_de_html),
        "{reviews_table_rows_html}": "\n".join(review_rows),
        "{reviews_note}": reviews_note,
        "{urgent_block_html}": urgent_block_html,
        "{rumors_block_html}": rumors_block_html,
        "{international_html}": "\n".join(international_items_html),
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # PDF + odeslání
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf_magazine(pdf_name, top_de, other_de, intl)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
