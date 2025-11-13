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

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
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

    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def bucket_by_region(items, regions_json):
    """Zatím nepoužíváme v e-mailu, ale nechávám pro budoucí regionální rozpad."""
    try:
        regions = json.loads(regions_json) if regions_json else {}
    except Exception:
        regions = {}

    buckets = {k: [] for k in regions.keys()}
    buckets["Sonstiges"] = []

    for it in items:
        text = f"{it['title']} {it.get('summary','')} {it.get('url','')}".lower()
        assigned = False

        for region, keywords in regions.items():
            for kw in keywords:
                if kw.lower() in text:
                    buckets[region].append(it)
                    assigned = True
                    break
            if assigned:
                break

        if not assigned:
            buckets["Sonstiges"].append(it)

    return {k: v for k, v in buckets.items() if v}


# ================== PDF REPORT ==================


def build_pdf(filename, top_items, intl_items):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    header_style = styles["Heading2"]
    normal_style = styles["BodyText"]

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
    story.append(
        Paragraph(f"Kaufland Media & Review Briefing – {date_de(TIMEZONE)}", title_style)
    )
    story.append(Spacer(1, 10))

    # -------- Top Schlagzeilen (DE) --------
    story.append(Paragraph("Top Schlagzeilen (DE)", header_style))
    story.append(Spacer(1, 4))

    top_data = [["#", "Titel", "Quelle", "Link"]]
    for i, it in enumerate(top_items, start=1):
        link_par = Paragraph(
            f'<link href="{escape(it["url"])}">Link</link>', normal_style
        )
        top_data.append(
            [
                str(i),
                escape(it["title"]),
                escape(it.get("source", "")),
                link_par,
            ]
        )

    top_tbl = Table(top_data, colWidths=[10 * mm, 80 * mm, 35 * mm, 45 * mm])
    top_tbl.setStyle(
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

    story.append(top_tbl)
    story.append(Spacer(1, 12))

    # -------- International – Virale Erwähnungen --------
    if intl_items:
        story.append(Paragraph("International – Virale Erwähnungen", header_style))
        story.append(Spacer(1, 4))

        intl_data = [["#", "Titel", "Quelle", "Link"]]
        for i, it in enumerate(intl_items, start=1):
            link_par = Paragraph(
                f'<link href="{escape(it["url"])}">Link</link>', normal_style
            )
            intl_data.append(
                [
                    str(i),
                    escape(it["title"]),
                    escape(it.get("source", "")),
                    link_par,
                ]
            )

        intl_tbl = Table(intl_data, colWidths=[10 * mm, 80 * mm, 35 * mm, 45 * mm])
        intl_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), "#F2F2F2"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("GRID", (0, 0), (-1, -1), 0.25, "grey"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                ]
            )
        )

        story.append(intl_tbl)

    doc.build(story)


# ================== RESEND EMAIL ==================


def send_via_resend(subject, html, pdf_name):
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
    items = fetch_news()

    # Top Schlagzeilen (DE) – prostě TOP N podle score
    top = items[:MAX_TOP]

    # International – jiná doména než .de + nepatří do Top Schlagzeilen
    top_urls = {it["url"] for it in top}
    intl_candidates = [it for it in items if not it["source"].endswith(".de")]
    intl = [it for it in intl_candidates if it["url"] not in top_urls][:10]

    # TODO: napojit skutečná data z reviews, zatím prázdné
    reviews = []

    # --- Načti HTML šablonu ---
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # -------- Executive Summary (německy) --------
    executive_summary_html = """
<p><strong>Insight:</strong> Kuratierte Top-Schlagzeilen (1–10) aus Deutschland; weitere Erwähnungen unten.</p>
<p><strong>Implikation:</strong> Relevante Entwicklungen rund um Kaufland in einem kompakten Überblick.</p>
<p><strong>Aktion:</strong> Google Reviews und mediale Stimmung werden laufend beobachtet, Alerts bei Auffälligkeiten.</p>
""".strip()

    # -------- Top headlines HTML --------
    top_items_html = []
    for i, it in enumerate(top, start=1):
        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("when"):
            meta_parts.append(escape(it["when"]))
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

    # -------- Google Reviews tabulka --------
    review_rows = []
    # vezmeme jen případy, kde je něco „vidět“ – změna ratingu nebo nové recenze
    significant_reviews = [
        r for r in (reviews or []) if r.get("count_24h", 0) or r.get("delta")
    ]
    significant_reviews.sort(
        key=lambda r: (abs(r.get("delta") or 0), r.get("count_24h", 0)), reverse=True
    )
    top_reviews = significant_reviews[:5]

    for r in top_reviews:
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

    # -------- Urgent & Rumors bloky (zatím prázdné, ale připravené) --------
    urgent_list = []
    rumors = []

    if urgent_list:
        urgent_block_html = (
            "<div class='card'><div class='card-header'><h2>⚠️ Urgent Alerts</h2></div><div class='card-body'>"
            + "".join(
                [
                    f"<div class='alert'><a href='{u['url']}'>{escape(u['title'])}</a><div class='meta'>{escape(u.get('why',''))}</div></div>"
                    for u in urgent_list
                ]
            )
            + "</div></div>"
        )
    else:
        urgent_block_html = ""

    if rumors:
        rumors_block_html = (
            "<div class='card'><div class='card-header'><h2>🟨 Boulevard & Rumors</h2></div><div class='card-body'>"
            + "".join(
                [
                    f"<div class='rumor'><a href='{b['url']}'>{escape(b['title'])}</a><div class='meta'>Quelle: {escape(b.get('source',''))}{' · Risiko: ' + escape(b.get('risk','')) if b.get('risk') else ''}</div></div>"
                    for b in rumors
                ]
            )
            + "</div></div>"
        )
    else:
        rumors_block_html = ""

    # -------- International HTML (bez překryvu s Top, krátký popis) --------
    international_items_html = []
    for it in (intl or []):
        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("type"):
            meta_parts.append(escape(it["type"]))
        meta = " · ".join(meta_parts)

        summary = escape(it.get("summary", ""))[:240]

        international_items_html.append(
            f"""
<li class="item">
  <div class="rank">•</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
    <div class="meta meta-secondary">{summary}</div>
  </div>
</li>""".strip()
        )

    # -------- Dosazení do HTML šablony --------
    html = template_str
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": EMAIL_TO or "",
        "{executive_summary_html}": executive_summary_html,
        "{top_count}": str(len(top)),
        "{top_headlines_html}": "\n".join(top_items_html),
        "{reviews_table_rows_html}": "\n".join(review_rows),
        "{reviews_note}": reviews_note,
        "{urgent_block_html}": urgent_block_html,
        "{rumors_block_html}": "\n".join([rumors_block_html]),
        "{international_html}": "\n".join(international_items_html),
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # -------- PDF + odeslání --------
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, top, intl)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
