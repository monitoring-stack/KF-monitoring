import os
import json
import base64
from datetime import datetime
from html import escape
from collections import Counter

import feedparser
from bs4 import BeautifulSoup

from reportlab.lib.pagesizes import A4
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


# ================== POMOCNÉ FUNKCE ==================


def classify_article(title: str, summary: str, link: str):
    """
    Navazuje na helpers.classify – počítáme s tím, že může vracet 3 nebo 4 hodnoty.
    My si vezmeme první 3 + zbytek ignorujeme.
    """
    host, src_type, base_score, *rest = classify(link, title)

    text = f"{title} {summary}".lower()

    # Kritické – velmi úzké, aby se tam nedostala každá "po skandálu opět otevřeno"
    crit_keywords = [
        "rückruf",
        "nicht essen",
        "nicht verzehren",
        "gesundheitsgefahr",
        "lebensgefährlich",
        "vergiftung",
        "salmonell",
        "warnung",
        "gesundheitsschädlich",
    ]
    is_critical = any(kw in text for kw in crit_keywords)

    # Tematická kategorie – jednoduchá, ale srozumitelná
    category = "Sonstiges"
    if "rückruf" in text or "qualität" in text or "mangel" in text:
        category = "Qualität & Rückruf"
    elif any(
        kw in text
        for kw in [
            "hygiene",
            "hygieneskandal",
            "filiale",
            "markt",
            "öffnung",
            "eröffnung",
            "umbau",
            "modernisiert",
        ]
    ):
        category = "Hygiene & Filialbetrieb"
    elif any(
        kw in text for kw in ["preise", "inflation", "rabatt", "angebot", "prospekt"]
    ):
        category = "Preis & Angebot"

    # Mezinárodní / virální – velmi hrubě podle domény
    is_international = not host.endswith(".de")

    return host, src_type, base_score, {
        "category": category,
        "critical": is_critical,
        "international": is_international,
    }


def fetch_news():
    """Stáhne všechny články z RSS, odstraní duplicity, nic zbytečně nefiltruje."""
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
            desc_html = getattr(e, "summary", "")
            desc = BeautifulSoup(desc_html, "html.parser").get_text()
            if len(desc) > 260:
                desc = desc[:260] + "…"

            pub = getattr(e, "published", "")
            host, src_type, base_score, meta = classify_article(title, desc, link)

            items.append(
                {
                    "title": title,
                    "summary": desc,
                    "url": link,
                    "source": host,
                    "src_type": src_type,
                    "score": base_score,
                    "category": meta["category"],
                    "is_critical": meta["critical"],
                    "is_international": meta["international"],
                    "date": pub,
                }
            )

    # Seřadit podle skóre (nejdůležitější nahoře)
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def bucket_by_category(items):
    buckets = {}
    for it in items:
        cat = it.get("category", "Sonstiges")
        buckets.setdefault(cat, []).append(it)

    # Preferovaný pořadník kategorií
    order = ["Qualität & Rückruf", "Hygiene & Filialbetrieb", "Preis & Angebot", "Sonstiges"]
    sorted_buckets = []

    for cat in order:
        if cat in buckets:
            sorted_buckets.append((cat, buckets[cat]))
    # případné další kategorie za tím
    for cat, lst in buckets.items():
        if cat not in order:
            sorted_buckets.append((cat, lst))

    return sorted_buckets


# ================== PDF ==================


def build_pdf(filename, items):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    story = []

    title = f"DE Monitoring – privat | {date_de(TIMEZONE)}"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 8))

    total = len(items)
    crit = sum(1 for i in items if i["is_critical"])
    intl = sum(1 for i in items if i["is_international"])

    intro = (
        f"Insgesamt {total} Artikel im Auswertungszeitraum. "
        f"{crit} davon als kritisch eingestuft, "
        f"{intl} virale / internationale Erwähnungen."
    )
    story.append(Paragraph(intro, styles["Normal"]))
    story.append(Spacer(1, 12))

    # Skupiny dle kategorií
    for cat, lst in bucket_by_category(items):
        story.append(Paragraph(cat, styles["Heading1"]))
        story.append(Spacer(1, 6))

        for it in lst:
            # Titulek
            line_title = f"{escape(it['title'])}"
            story.append(Paragraph(line_title, styles["Heading3"]))

            # Meta + štítky
            badges = []
            if it["is_critical"]:
                badges.append("■ Kritisch")
            if it["is_international"]:
                badges.append("● Virale / internationale Erwähnung")

            meta_parts = [escape(it["source"])]
            if it.get("date"):
                meta_parts.append(escape(it["date"]))
            if badges:
                meta_parts.append(" / ".join(badges))

            meta_line = " · ".join(meta_parts)
            story.append(Paragraph(meta_line, styles["Normal"]))

            # Krátký klikací odkaz – jen hlavní URL, ne celé RSS
            link_html = f'<a href="{it["url"]}">Link</a>'
            story.append(Paragraph(link_html, styles["Normal"]))

            story.append(Spacer(1, 6))

    doc.build(story)


# ================== EMAIL (HTML) ==================


def build_email_html(items):
    date_str = date_de(TIMEZONE)
    total = len(items)
    critical_count = sum(1 for x in items if x["is_critical"])
    international_count = sum(1 for x in items if x["is_international"])

    theme_counts = Counter(x["category"] for x in items)
    themes_str = ", ".join(f"{k} ({v})" for k, v in theme_counts.items())

    # --- Executive Summary ---
    executive_summary_html = f"""
    Heute wurden insgesamt <b>{total}</b> relevante Erwähnungen zu Kaufland erfasst.<br>
    Davon sind <b>{critical_count}</b> als potentiell kritisch (Rückruf, Qualität, Gesundheitsrisiken) eingestuft.<br>
    Zusätzlich gibt es <b>{international_count}</b> virale / internationale Erwähnungen.<br><br>
    Thematisch dominieren heute: <b>{escape(themes_str)}</b>.
    """.strip()

    # --- Top Headlines ---
    top_n = min(MAX_TOP, len(items))
    top_items = sorted(items, key=lambda x: x["score"], reverse=True)[:top_n]

    top_lines = []
    for idx, it in enumerate(top_items, start=1):
        badges = []
        if it["is_critical"]:
            badges.append("⚠ Kritisch")
        if it["is_international"]:
            badges.append("🌍 Virale Erwähnung")
        badge_str = " · ".join(badges)

        meta_parts = [escape(it["source"])]
        if it.get("date"):
            meta_parts.append(escape(it["date"]))
        if badge_str:
            meta_parts.append(badge_str)
        meta = " · ".join(meta_parts)

        line = (
            f'<p>{idx}. '
            f'<a href="{it["url"]}">{escape(it["title"])}</a><br>'
            f'<span style="font-size:12px;color:#555;">{meta}</span></p>'
        )
        top_lines.append(line)

    top_block_html = "\n".join(top_lines)

    # --- International / Viral Section ---
    intl_items = [x for x in items if x["is_international"]]
    intl_lines = []
    for it in intl_items[:5]:
        meta_parts = [escape(it["source"])]
        if it.get("date"):
            meta_parts.append(escape(it["date"]))
        if it["is_critical"]:
            meta_parts.append("⚠ Kritisch")
        meta = " · ".join(meta_parts)

        intl_lines.append(
            f'<li><a href="{it["url"]}">{escape(it["title"])}</a>'
            f'<br><span style="font-size:12px;color:#555;">{meta}</span></li>'
        )

    if intl_lines:
        international_block_html = (
            "<h3>Internationale / virale Erwähnungen</h3><ul>"
            + "\n".join(intl_lines)
            + "</ul>"
        )
    else:
        international_block_html = ""

    # --- Načteme HTML šablonu a nahradíme placeholdery ---
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    replacements = {
        "{date_str}": date_str,
        "{tz}": TIMEZONE,
        "{total_count}": str(total),
        "{critical_count}": str(critical_count),
        "{international_count}": str(international_count),
        "{themes_str}": themes_str,
        "{executive_summary_html}": executive_summary_html,
        "{top_headlines_html}": top_block_html,
        "{international_block_html}": international_block_html,
        "{top_count}": str(top_n),
    }

    html = template_str
    for key, val in replacements.items():
        html = html.replace(key, val)

    return html


# ================== RESEND ==================


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

    import urllib.request
    import urllib.error

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
        print("No items found for today – email not sent.")
        return

    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, items)

    html = build_email_html(items)
    subject = f"Kaufland Media & Review Briefing – Deutschland | {date_de(TIMEZONE)}"

    print("Sending email to", EMAIL_TO)
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
