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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

from helpers import date_de, classify

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce
CC = os.getenv("CC")                          # např. "a@b.de,c@d.de"
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


def build_pdf(filename, items, intl, reviews):
    """
    Čitelnější PDF:
    - nadpis + datum
    - Top 10 Schlagzeilen jako bloky (title + meta + summary + URL)
    - další zmínky jako bullet list
    - „Virale Erwähnungen (DE)“ jako samostatná sekce
    """
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h2 = styles["Heading2"]
    h3 = styles["Heading3"]
    normal = styles["Normal"]

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
    story.append(Paragraph(date_de(TIMEZONE), normal))
    story.append(Spacer(1, 12))

    # ========== 1) Top Schlagzeilen ==========
    story.append(Paragraph("Top Schlagzeilen (DE)", h2))
    story.append(Spacer(1, 6))

    top_items = items[:10]

    for idx, it in enumerate(top_items):
        story.append(Paragraph(f"{idx+1}. {it.get('title','')}", h3))

        meta_parts = []
        if it.get("source"):
            meta_parts.append(it["source"])
        if it.get("type"):
            meta_parts.append(it["type"])
        if it.get("why"):
            meta_parts.append(f"Grund: {it['why']}")
        meta = " · ".join(meta_parts)
        if meta:
            story.append(Paragraph(meta, normal))

        if it.get("summary"):
            story.append(Paragraph(it["summary"], normal))

        if it.get("url"):
            story.append(Paragraph(it["url"], normal))

        story.append(Spacer(1, 10))

    # ========== 2) Weitere Erwähnungen ==========
    others = items[10:50]
    if others:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Weitere Erwähnungen (Überblick)", h2))
        story.append(Spacer(1, 6))

        for it in others:
            title = it.get("title", "")
            src = it.get("source", "")
            line = f"• {title} ({src})"
            story.append(Paragraph(line, normal))

        story.append(Spacer(1, 10))

    # ========== 3) Virale Erwähnungen (DE) ==========
    if intl:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Virale Erwähnungen (DE)", h2))
        story.append(Spacer(1, 6))

        for it in intl:
            title = it.get("title", "")
            src = it.get("source", "")
            line = f"• {title} ({src})"
            story.append(Paragraph(line, normal))

        story.append(Spacer(1, 10))

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

    # Top položky pro e-mail (MAX_TOP) + „virální“ výběr pro sekci Virale Erwähnungen
    top = items[:MAX_TOP]

    viral_items = [
        it
        for it in items
        if it.get("score", 0) >= 4 and it.get("type") in ("Boulevard", "neutral/spekulativ")
    ][:10]

    # TODO: skutečná data z reviews – zatím prázdné
    reviews = []

    # --- Načti HTML šablonu ---
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # === 1) Executive summary (DE) ===
    executive_summary_html = """
<p><strong>Insight:</strong> Kuratierte Top-Schlagzeilen (1–10) für Deutschland, weitere Erwähnungen unten.</p>
<p><strong>Implikation:</strong> Relevante Themen aus Presse &amp; Online-Medien auf einen Blick; regionale Unterschiede sind schnell erkennbar.</p>
<p><strong>Aktion:</strong> Google Reviews &amp; kritische Erwähnungen werden laufend beobachtet. Bei Auffälligkeiten können gezielte Maßnahmen eingeleitet werden.</p>
""".strip()

    # === 2) Top headlines (pro HTML) ===
    top_items_html = []
    for i, it in enumerate(top):
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
  <div class="rank">{i+1}</div>
  <div>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta}</div>
  </div>
</li>""".strip()
        )

    # === 3) Reviews tabulka – pilot text ===
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
Pilotmodus: Noch keine automatisierten Store-Analysen aktiv.
Geplant: Anzeige der Filialen mit den meisten neuen ⭐ positiven / ⚠️ negativen Reviews in den letzten 24 Stunden.
</td></tr>"""
        ]

    reviews_note = (
        "Δ = geplante Veränderung der Ø-Bewertung in den letzten 24 Stunden "
        "(sobald Store-Daten aktiv sind)."
    )

    # === 4) Urgent & Boulevard bloky – zatím prázdné ===
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

    # === 5) Virale Erwähnungen (DE) pro HTML ===
    international_items_html = []
    for it in (viral_items or []):
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

    # === 6) Dosazení do HTML šablony (ruční replace) ===
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
        "{rumors_block_html}": rumors_block_html,
        "{international_html}": "\n".join(international_items_html),
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # === PDF + odeslání ===
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, items, viral_items, reviews)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
