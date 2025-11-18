import os
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

import feedparser
from bs4 import BeautifulSoup

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

from helpers import date_de, classify


# ================== KONFIGURACE / ENV ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce
CC = os.getenv("CC")
BCC = os.getenv("BCC")

# kolik článků chceme maximálně použít pro scoring / PDF
MAX_TOP = int(os.getenv("MAX_TOP", "40"))

FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

# jednoduché tematické štítky
THEME_RULES = [
    ("Krise / Rückruf / Hygiene", r"Rückruf|Skandal|Hygiene|Ekel|Gammelfleisch|Krise|Lebensmittelwarnung"),
    ("Expansion & Eröffnungen", r"Eröffnung|eröffnet|Neueröffnung|Neueröffnung|Filiale|Center|kommt nach|Start in"),
    ("Sortiment & Produkte", r"Produkt|Sortiment|Eis|Marke|Eigenmarke|Preis|Angebot|Aktion"),
    ("Standort & Kommunales", r"Stadt|Rat|Bürger|Parkplatz|Verkehr|Bebauungsplan|Bauausschuss"),
    ("HR / Jobs / Arbeitsmarkt", r"Job|Jobs|Stellen|Mitarbeiter|Azubi|Ausbildung|Tarif|Streik|Personal"),
]


# ================== NEWS FETCH ==================


def fetch_news():
    """Stáhne články z Google News RSS a nechá jen posledních 24 h."""
    seen = set()
    items = []

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=1)

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)

            # filtr na posledních 24 hodin
            published_dt = None
            if getattr(e, "published_parsed", None):
                published_dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            if published_dt and published_dt < cutoff:
                continue

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

    # seřadíme podle score, nejdřív nejdůležitější
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:MAX_TOP]


# ================== TÉMATA / BUCKETY ==================


def tag_theme(item):
    """Přiřadí jednoduchý tematický štítek podle title+summary."""
    import re

    text = f"{item['title']} {item.get('summary', '')}"
    for theme, pattern in THEME_RULES:
        if re.search(pattern, text, re.I):
            return theme
    return "Sonstiges"


def bucket_by_theme(items):
    """Rozdělí články do tematických bloků a seřadí je podle score."""
    buckets = {}
    for it in items:
        theme = tag_theme(it)
        buckets.setdefault(theme, []).append(it)

    # uvnitř tématu seřadíme podle score
    for theme in buckets:
        buckets[theme].sort(key=lambda x: x["score"], reverse=True)

    # pořadí témat – podle nejlepšího score v daném tématu
    ordered = dict(
        sorted(
            buckets.items(),
            key=lambda kv: max(x["score"] for x in kv[1]),
            reverse=True,
        )
    )
    return ordered


# ================== PDF – MAGAZINE LAYOUT ==================


def build_pdf(filename, buckets):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h2 = styles["Heading2"]
    body = styles["BodyText"]

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,  # pokud budeš chtít na šířku: from reportlab.lib.pagesizes import landscape; pagesize=landscape(A4)
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    story = []

    story.append(Paragraph("Kaufland Media & Review Briefing – Deutschland", title_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(date_de(TIMEZONE), body))
    story.append(Spacer(1, 12))

    for theme, items in buckets.items():
        story.append(Spacer(1, 8))
        story.append(Paragraph(theme, h2))
        story.append(Spacer(1, 4))

        for idx, it in enumerate(items, 1):
            title_html = html_escape(it["title"])
            url_html = html_escape(it["url"])
            source = html_escape(it.get("source", ""))
            why = html_escape(it.get("why", ""))
            summary = html_escape(it.get("summary", ""))

            para = (
                f"<b>{idx}. {title_html}</b><br/>"
                f"{summary}<br/>"
                f"<font size=8>{source}"
                f"{' · Grund: ' + why if why else ''}</font><br/>"
                f"<font size=8><u><link href='{url_html}'>Artikel aufrufen</link></u></font>"
            )

            story.append(Paragraph(para, body))
            story.append(Spacer(1, 6))

    doc.build(story)


# ================== GOOGLE REVIEWS – VARIANTA A ==================


def build_reviews_block_html(reviews):
    """
    Varianta A – minimalistická:

    - Pokud nejsou žádné „podezřelé“ filiálky, vrátí jen šedou větu.
    - Jakmile budeme mít data, může `reviews` obsahovat list dictů:
      {region, store, avg, delta, count_24h, flag} a tady z toho uděláme <ul>…
    """
    if not reviews:
        return (
            "<p class='muted'>Noch keine auffälligen Veränderungen in den vorliegenden "
            "Google-Reviews-Daten (Pilotmodus oder stabile Lage).</p>"
        )

    rows = []
    for r in reviews:
        line = (
            f"<li><strong>{html_escape(r.get('region', '–'))} – "
            f"{html_escape(r.get('store', '–'))}</strong>: "
            f"Ø {r.get('avg', '–')} | Δ24h {r.get('delta', '–')} | "
            f"{r.get('count_24h', '–')} neue Reviews – "
            f"{html_escape(r.get('flag', ''))}</li>"
        )
        rows.append(line)
    return "<ul>" + "\n".join(rows) + "</ul>"


# ================== RESEND E-MAIL ==================


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
            {"filename": pdf_name, "content": pdf_b64},
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


# ================== MAIN ==================


def main():
    items = fetch_news()
    if not items:
        print("No news items found for last 24h.")
        return

    # seřazené články
    items_sorted = sorted(items, key=lambda x: x["score"], reverse=True)

    # Top 3 virální pro e-mail
    top_for_email = items_sorted[:3]

    # tematické bloky pro PDF
    buckets = bucket_by_theme(items_sorted)

    # Google Reviews – zatím prázdné (pilot)
    reviews = []

    # --- PDF ---
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, buckets)

    # --- e-mail HTML z šablony ---
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # Top-3 HTML
    top_items_html = []
    for idx, it in enumerate(top_for_email, 1):
        meta_parts = []
        if it.get("source"):
            meta_parts.append(html_escape(it["source"]))
        if it.get("why"):
            meta_parts.append("Grund: " + html_escape(it["why"]))
        meta = " · ".join(meta_parts)

        top_items_html.append(
            f"<li>"
            f"<a href='{html_escape(it['url'])}'>{html_escape(it['title'])}</a>"
            f"<div class='meta'>{meta}</div>"
            f"</li>"
        )

    reviews_block_html = build_reviews_block_html(reviews)

    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": html_escape(EMAIL_TO or ""),
        "{top_headlines_html}": "\n".join(top_items_html),
        "{reviews_block_html}": reviews_block_html,
    }

    html = template_str
    for key, val in replacements.items():
        html = html.replace(key, val)

    subject = f"📰 Kaufland Media & Review Briefing – Deutschland | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
