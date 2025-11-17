import os
import re
import json
import base64
import urllib.request
import urllib.error

import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from html import escape

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

from helpers import date_de, classify

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

# Resend (už máš nastaveno v GitHub Secrets)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce
CC = os.getenv("CC")
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "10"))
INCLUDE_REVIEWS = os.getenv("INCLUDE_REVIEWS", "false").lower() == "true"
PLACES_JSON = os.getenv("PLACES_JSON", "[]")
REGIONS_JSON = os.getenv("REGIONS_JSON", "{}")

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

            title = e.title
            desc = BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text()
            host, typ, score = classify(link, title)

            items.append(
                {
                    "title": title,
                    "url": link,
                    "summary": (desc[:260] + "…") if len(desc) > 260 else desc,
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

    # .de doména → DE (výchozí)
    if host.endswith(".de"):
        return True

    text = title + " " + summary
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


# ================== PDF REPORT ==================


def build_pdf(filename, all_items, reviews):
    """
    Vytvoří PDF:
    - tabulka článků (seřazená podle score – pořadí z all_items)
    - pod tím Linkverzeichnis s URL
    """
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    title_style = styles["Title"]

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    story = []

    # Titulek
    story.append(Paragraph(f"Kaufland Full Report – {date_de(TIMEZONE)}", title_style))
    story.append(Spacer(1, 8))

    # Tabulka článků – bez URL, jen index
    data = [["#", "Titel", "Quelle", "Typ", "Kurzfassung"]]

    for idx, item in enumerate(all_items, start=1):
        data.append(
            [
                str(idx),
                item["title"],
                item["source"],
                item["type"],
                item["summary"],
            ]
        )

    tbl = Table(
        data,
        colWidths=[10 * mm, 60 * mm, 25 * mm, 25 * mm, 70 * mm],
        repeatRows=1,
    )

    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), "#E60000"),
                ("TEXTCOLOR", (0, 0), (-1, 0), "white"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("GRID", (0, 0), (-1, -1), 0.25, "grey"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
            ]
        )
    )

    story.append(tbl)
    story.append(Spacer(1, 12))

    # Linkverzeichnis
    story.append(Paragraph("Links zu allen Artikeln", styles["Heading2"]))
    story.append(Spacer(1, 4))

    for idx, item in enumerate(all_items, start=1):
        url = item["url"]
        p = Paragraph(f"[{idx}] {url}", normal)
        story.append(p)

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

    # PDF → base64
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
    # 1) načtení a seřazení news
    items = fetch_news()

    # 2) rozdělení pro e-mail (bez duplicit URL)
    top_de, other_de, intl = split_for_email(items, MAX_TOP)

    # 3) data pro PDF – v pořadí podle score (items je už sorted)
    #    zároveň bez duplicit
    seen = set()
    all_for_pdf = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        all_for_pdf.append(it)

    # 4) reviews – zatím prázdné (pilotmodus)
    reviews = []

    # 5) načtení HTML šablony
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # === Executive summary (zatím ručně, DE text) ===
    executive_summary_html = """
<p><strong>Insight:</strong> Kuratierte Top-Schlagzeilen (1–10) für Deutschland; weitere Erwähnungen unten.</p>
<p><strong>Implikation:</strong> Schneller Überblick in einem E-Mail; regionale Unterschiede sind sofort erkennbar.</p>
<p><strong>Aktion:</strong> Google Reviews werden im Pilotmodus beobachtet; Alerts bei Auffälligkeiten folgen.</p>
""".strip()

    # === Top Schlagzeilen (DE) ===
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
  <div class="rank">{i}</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </div>
</li>""".strip()
        )

    # === Virale Erwähnungen (DE – zbytek) ===
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
  <div class="rank">•</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </div>
</li>""".strip()
        )

    # === International – Virale Erwähnungen ===
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
  <div class="rank">•</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </div>
</li>""".strip()
        )

    # === Google Reviews placeholder ===
    review_rows = []
    for r in (reviews or []):
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
Keine auffälligen 24h-Veränderungen (Pilotmodus). Aktivierbar via SerpAPI.
</td></tr>"""
        ]

    reviews_note = "Δ = Veränderung der Ø-Bewertung in den letzten 24 Stunden."

    # === Urgent / Boulevard bloky – zatím prázdné ===
    urgent_block_html = ""
    rumors_block_html = ""

    # === Dosazení do HTML šablony (bez .format, ruční replace) ===
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

    # === PDF + odeslání ===
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, all_for_pdf, reviews)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
