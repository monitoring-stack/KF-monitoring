import os
import re
import json
import base64
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime, timedelta

import feedparser
from bs4 import BeautifulSoup
from html import escape

from reportlab.lib import colors
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
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
CC = os.getenv("CC")
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "10"))
# jak staré články bereme (hodiny)
MAX_AGE_HOURS = int(os.getenv("MAX_AGE_HOURS", "36"))

FEEDS = [
    # můžeš libovolně rozšířit o další RSS
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

# ================== NEWS FETCH ==================


def _entry_datetime(entry):
    """
    Vrátí datetime publikace, pokud je v RSS k dispozici.
    """
    dt_struct = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if not dt_struct:
        return None
    return datetime(*dt_struct[:6])


def fetch_news():
    """
    Stáhne a zkombinuje články z FEEDS:
    - deduplikace podle URL
    - filtr na posledních MAX_AGE_HOURS
    - obohacení o score, topic, flag kritičnosti, virálnost
    - spočítání 'pickup_count' (kolik podobných titulů běží)
    """
    seen = set()
    items = []
    now = datetime.utcnow()
    max_age = timedelta(hours=MAX_AGE_HOURS)

    raw_entries = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            raw_entries.append(e)

    # filtr na čas
    for e in raw_entries:
        link = e.link
        if link in seen:
            continue

        pub_dt = _entry_datetime(e)
        if pub_dt is not None and (now - pub_dt) > max_age:
            # starší než povolený interval
            continue

        seen.add(link)

        title = e.title
        desc = BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text()

        host, medium_type, score, is_critical, topic, is_international = classify(
            link, title, desc
        )

        when_str = ""
        if pub_dt:
            when_str = pub_dt.strftime("%d.%m. %H:%M")

        items.append(
            {
                "title": title,
                "url": link,
                "summary": (desc[:260] + "…") if len(desc) > 260 else desc,
                "source": host,
                "medium_type": medium_type,
                "score": score,
                "is_critical": is_critical,
                "topic": topic,
                "is_international": is_international,
                "when": when_str,
            }
        )

    # pickup_count – kolikrát se „podobný“ titulek objevil
    def norm_title(t):
        return re.sub(r"\W+", " ", t).lower().strip()

    keys = [norm_title(i["title"]) for i in items]
    counts = Counter(keys)
    for it, key in zip(items, keys):
        it["pickup_count"] = counts[key]

    # seřadit podle score (nejdřív nejdůležitější)
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


# ================== PDF REPORT ==================


def build_pdf(filename, items):
    """
    Vytvoří „magazine style“ PDF:
    - shrnutí nahoře
    - sekce podle topic, seřazené podle nejvyššího score v tématu
    - články v rámci sekce podle score
    - titulky jsou klikací odkazy
    """
    styles = getSampleStyleSheet()

    title_style = styles["Title"]

    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        textColor=colors.HexColor("#E60000"),
        spaceAfter=8,
        spaceBefore=12,
    )

    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=13,
        spaceAfter=2,
    )

    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.grey,
        spaceAfter=6,
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

    # HLAVNÍ TITULEK
    story.append(
        Paragraph(
            f"DE Monitoring – privat | {date_de(TIMEZONE)}",
            title_style,
        )
    )
    story.append(Spacer(1, 10))

    # SHRNUTÍ
    total = len(items)
    critical = sum(1 for i in items if i.get("is_critical"))
    international = sum(1 for i in items if i.get("is_international"))

    topic_counts = {}
    for it in items:
        topic = it.get("topic", "Sonstiges")
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    topic_summary = ", ".join(
        f"{t} ({n})"
        for t, n in sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)
    )

    intro_txt = (
        f"Insgesamt {total} Artikel im Auswertungszeitraum. "
        f"{critical} davon als kritisch eingestuft, "
        f"{international} virale / internationale Erwähnungen. "
        f"Schwerpunktthemen: {topic_summary}."
    )
    story.append(Paragraph(intro_txt, body))
    story.append(Spacer(1, 12))

    # ROZDĚLENÍ PODLE TÉMAT
    grouped = {}
    for it in items:
        topic = it.get("topic", "Sonstiges")
        grouped.setdefault(topic, []).append(it)

    def max_score(topic_name):
        return max(x.get("score", 0) for x in grouped[topic_name])

    topic_order = sorted(grouped.keys(), key=max_score, reverse=True)

    for topic in topic_order:
        story.append(Paragraph(topic, h1))
        story.append(Spacer(1, 4))

        for it in sorted(grouped[topic], key=lambda x: x.get("score", 0), reverse=True):
            title = escape(it.get("title", ""))
            url = it.get("url", "")
            source = it.get("source", "")
            is_critical = bool(it.get("is_critical"))
            is_international = bool(it.get("is_international"))
            pickup = it.get("pickup_count", 1)

            # Klikací titulek
            if url:
                title_para = Paragraph(f'<link href="{url}">{title}</link>', body)
            else:
                title_para = Paragraph(title, body)

            story.append(title_para)

            meta_bits = []
            if source:
                meta_bits.append(source)
            if pickup > 1:
                meta_bits.append(f"{pickup} Quellen")
            if is_critical:
                meta_bits.append("■ Kritisch")
            if is_international:
                meta_bits.append("● Virale / internationale Erwähnung")

            if meta_bits:
                story.append(Paragraph(" · ".join(meta_bits), meta_style))

            story.append(Spacer(1, 4))

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
        print("No items for today.")
        return

    top_items = items[:MAX_TOP]

    # Načti HTML šablonu
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # === Executive summary ===
    total = len(items)
    critical = sum(1 for i in items if i.get("is_critical"))
    intl_count = sum(1 for i in items if i.get("is_international"))

    topic_counts = {}
    for it in items:
        topic = it.get("topic", "Sonstiges")
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    # top 2 témata
    top_topics = sorted(
        topic_counts.items(), key=lambda kv: kv[1], reverse=True
    )[:2]
    topic_str = ", ".join(f"{t} ({n})" for t, n in top_topics)

    executive_summary_html = f"""
<p>Heute wurden insgesamt <strong>{total}</strong> relevante Erwähnungen zu Kaufland erfasst.</p>
<p>Davon sind <strong>{critical}</strong> als potenziell kritisch (Rückruf, Skandal, Boykott, Krise) eingestuft.
Zusätzlich gibt es <strong>{intl_count}</strong> virale / internationale Erwähnungen.</p>
<p>Thematisch dominieren heute: <strong>{topic_str}</strong>. Vollständige Liste inkl. thematischer Einordnung und aller Quellen im angehängten PDF.</p>
""".strip()

    # === Top Schlagzeilen ===
    top_items_html = []
    for i, it in enumerate(top_items, start=1):
        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("when"):
            meta_parts.append(escape(it["when"]))
        if it.get("pickup_count", 1) > 1:
            meta_parts.append(f"{it['pickup_count']} Quellen")
        if it.get("is_critical"):
            meta_parts.append("⚠️ Kritisch")
        if it.get("is_international"):
            meta_parts.append("🌍 Virale Erwähnung")

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

    # === International blok pro e-mail ===
    international_items_html = []
    for it in items:
        if not it.get("is_international"):
            continue

        meta_parts = []
        if it.get("source"):
            meta_parts.append(escape(it["source"]))
        if it.get("when"):
            meta_parts.append(escape(it["when"]))
        if it.get("pickup_count", 1) > 1:
            meta_parts.append(f"{it['pickup_count']} Quellen")

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

    if international_items_html:
        international_block_html = f"""
<div class="card">
  <div class="card-header">
    <h2>🌍 Internationale / virale Erwähnungen</h2>
  </div>
  <div class="card-body">
    <ol class="items">
      {'\n'.join(international_items_html)}
    </ol>
  </div>
</div>
""".strip()
    else:
        international_block_html = ""

    # === Google Reviews – placeholder, dokud nebudeme tahat ostrá data ===
    review_rows = [
        """<tr><td colspan="5" class="muted">
Noch keine auffälligen Veränderungen in den vorliegenden Google-Reviews-Daten (Pilotmodus).
</td></tr>"""
    ]
    reviews_note = "Δ = Veränderung der Ø-Bewertung in den letzten 24 Stunden."

    # === Dosazení do šablony ===
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
        "{international_block_html}": international_block_html,
    }

    for key, val in replacements.items():
        html = html.replace(key, val)

    # === PDF + odeslání ===
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, items)

    subject = f"📰 Kaufland Media & Review Briefing – Deutschland | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
