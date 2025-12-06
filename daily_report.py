import os
import re
import json
import base64
import time
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

from helpers import date_de, classify

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

# Resend
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
CC = os.getenv("CC")
BCC = os.getenv("BCC")

# Počet zpráv v mailu
MAX_TOP = int(os.getenv("MAX_TOP", "3"))

# Google News feedy – jen Kaufland & DE
FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

# Témata – jednoduché keywordy
TOPIC_KEYWORDS = {
    "Qualität & Rückruf": [
        "rückruf",
        "produktwarnung",
        "gesundheitsgefahr",
        "verzehr",
        "lebensgefährlich",
    ],
    "Hygiene & Filialbetrieb": [
        "hygieneskandal",
        "hygiene-mangel",
        "schimmel",
        "mäusekot",
        "mäuse-kot",
        "filiale",
        "markt",
        "öffnet",
        "neueröffnung",
        "wieder offen",
        "modernisiert",
    ],
    "Preis & Angebot": [
        "preis",
        "rabatt",
        "angebot",
        "billiger",
        "teurer",
        "discount",
        "sonderangebot",
        "aktionen",
        "aktionswoche",
    ],
}

# Klíčová slova pro kritičnost
CRITICAL_KEYWORDS = [
    "rückruf",
    "skandal",
    "boykott",
    "datenschutz",
    "krise",
    "vergiftung",
    "gesundheitsgefahr",
    "gesundheitliche folgen",
    "ekel-skandal",
    "ekelskandal",
    "gammelfleisch",
]

# Slova, která indikují „spíše pozitivní reopening“, i když se zmiňuje skandál
DE_ESCALATION_WORDS = ["öffnet", "eröffnet", "wieder offen", "modernisiert", "relaunch"]


# ================== POMOCNÉ FUNKCE ==================


def is_recent(entry, max_age_days: int = 2) -> bool:
    """
    Vrátí True, pokud je článek mladší než max_age_days.
    Pokud Google feed nedá datum, necháme článek raději projít.
    """
    t = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if not t:
        return True
    dt = datetime.fromtimestamp(time.mktime(t))
    return dt >= datetime.now() - timedelta(days=max_age_days)


def classify_topic(text: str) -> str:
    text_l = text.lower()
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in text_l:
                return topic
    return "Sonstiges"


def is_critical(text: str) -> bool:
    """
    Kritické = obsahuje kritická slova,
    ALE když je to spíš pozitivní reopening po skandálu, neshodíme to jako „kritické“.
    """
    t = text.lower()

    # Reopening po skandálu – zmírnit
    if "skandal" in t and "hygiene" in t and any(w in t for w in DE_ESCALATION_WORDS):
        return False

    return any(kw in t for kw in CRITICAL_KEYWORDS)


# ================== NEWS FETCH ==================


def fetch_news():
    seen = set()
    items = []

    for url in FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)

            # filtr na max 1–2 dny staré články
            if not is_recent(e, max_age_days=2):
                continue

            title = e.title
            raw_summary = getattr(e, "summary", "") or getattr(e, "description", "")
            desc = BeautifulSoup(raw_summary, "html.parser").get_text(" ", strip=True)

            host, src_type, base_score = classify(link, title)

            text_for_topic = f"{title} {desc}"
            topic = classify_topic(text_for_topic)
            critical = is_critical(text_for_topic)

            score = base_score
            if critical:
                score += 3  # kritické zprávy posuneme nahoru

            is_intl = not host.endswith(".de")
            published = getattr(e, "published", None)

            items.append(
                {
                    "title": title,
                    "summary": desc,
                    "url": link,
                    "source": host,
                    "source_type": src_type,
                    "topic": topic,
                    "critical": critical,
                    "score": score,
                    "is_international": is_intl,
                    "published": published,
                }
            )

    # seřadit jen podle score, NIC nevyhazujeme
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def build_stats(items):
    total = len(items)
    critical = sum(1 for i in items if i["critical"])
    intl = sum(1 for i in items if i["is_international"])
    topic_counts = {}
    for it in items:
        topic_counts[it["topic"]] = topic_counts.get(it["topic"], 0) + 1
    sorted_topics = sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "total": total,
        "critical": critical,
        "international": intl,
        "topic_counts": sorted_topics,
    }


# ================== EMAIL (HTML) ==================


def build_email_html(items):
    stats = build_stats(items)
    date_str = date_de(TIMEZONE)

    top_items = items[:MAX_TOP]

    # Executive summary
    exec_lines = []
    exec_lines.append(
        f"Heute wurden insgesamt <strong>{stats['total']}</strong> relevante Erwähnungen zu Kaufland erfasst."
    )
    exec_lines.append(
        f"Davon sind <strong>{stats['critical']}</strong> als potentiell kritisch "
        "(Rückruf, Skandal, Boykott, Krise) eingestuft. Zusätzlich gibt es "
        f"<strong>{stats['international']}</strong> virale / internationale Erwähnungen."
    )
    if stats["topic_counts"]:
        topic_str = ", ".join(
            f"{escape(t[0])} ({t[1]})" for t in stats["topic_counts"][:3]
        )
        exec_lines.append(
            "Thematisch dominieren heute: "
            + topic_str
            + ". Vollständige Liste inkl. thematischer Einordnung und aller Quellen im angehängten PDF."
        )

    exec_html = "".join(f"<p>{line}</p>" for line in exec_lines)

    # Top Schlagzeilen
    top_rows = []
    for idx, it in enumerate(top_items, start=1):
        meta_bits = [escape(it["source"])]
        if it.get("published"):
            meta_bits.append(escape(it["published"]))
        if it["critical"]:
            meta_bits.append("⚠️ Kritisch")
        if it["is_international"]:
            meta_bits.append("🌍 Virale / internationale Erwähnung")
        else:
            meta_bits.append("● Virale Erwähnung")
        meta_html = " · ".join(meta_bits)

        top_rows.append(
            f"""
<tr class="row">
  <td class="rank">{idx}</td>
  <td>
    <a href="{it['url']}">{escape(it['title'])}</a>
    <div class="meta">{meta_html}</div>
  </td>
</tr>
""".strip()
        )

    top_table_html = "\n".join(top_rows)

    topic_labels = [t[0] for t in stats["topic_counts"][:3]]
    topic_summary_html = (
        ", ".join(escape(t) for t in topic_labels) if topic_labels else "–"
    )

    # !!! žádné f-stringy, žádné .format – jen naše [[PLACEHOLDER]] !!!
    html_template = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Kaufland Media & Review Briefing – Deutschland</title>
  <style>
    body {{
      font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
      margin:0;
      padding:0;
      background:#f5f5f5;
    }}
    .wrapper {{
      max-width: 900px;
      margin: 0 auto;
      background:#ffffff;
    }}
    .header {{
      background:#E60000;
      color:#ffffff;
      padding:24px 32px;
    }}
    .header h1 {{
      margin:0;
      font-size:28px;
      font-weight:700;
    }}
    .header .sub {{
      margin-top:8px;
      font-size:14px;
    }}
    .content {{
      padding:24px 32px 32px 32px;
      font-size:15px;
      color:#222222;
    }}
    h2 {{
      font-size:20px;
      margin:24px 0 12px;
      color:#111111;
    }}
    .top-table {{
      width:100%;
      border-collapse:collapse;
      margin-top:8px;
    }}
    .top-table td {{
      padding:8px 4px;
      vertical-align:top;
      border-top:1px solid #eeeeee;
    }}
    .top-table .rank {{
      width:28px;
      font-weight:700;
      text-align:right;
      padding-right:10px;
      color:#E60000;
    }}
    .meta {{
      font-size:12px;
      color:#666666;
      margin-top:2px;
    }}
    a {{
      color:#0050b3;
      text-decoration:none;
    }}
    a:hover {{
      text-decoration:underline;
    }}
    .footer {{
      font-size:12px;
      color:#777777;
      margin-top:24px;
      border-top:1px solid #eeeeee;
      padding-top:12px;
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <h1>Kaufland Media &amp; Review Briefing – Deutschland</h1>
      <div class="sub">Datum: [[DATE_STR]] · Zeitzone: [[TIMEZONE]]</div>
    </div>
    <div class="content">
      <h2>Executive Summary</h2>
      [[EXEC_SUMMARY_HTML]]

      <h2>Top [[TOP_COUNT]] Schlagzeilen</h2>
      <table class="top-table">
        <tbody>
          [[TOP_TABLE_ROWS]]
        </tbody>
      </table>

      <div class="footer">
        Thematische Übersicht inkl. aller Links im angehängten PDF.<br>
        Schwerpunkt-Themen heute: [[TOPIC_SUMMARY]].<br>
        Dieser Bericht ist automatisch generiert. Für Rückfragen direkt antworten.
      </div>
    </div>
  </div>
</body>
</html>
""".strip()

    html = (
        html_template.replace("[[DATE_STR]]", escape(date_str))
        .replace("[[TIMEZONE]]", escape(TIMEZONE))
        .replace("[[EXEC_SUMMARY_HTML]]", exec_html)
        .replace("[[TOP_COUNT]]", str(len(top_items)))
        .replace("[[TOP_TABLE_ROWS]]", top_table_html)
        .replace("[[TOPIC_SUMMARY]]", topic_summary_html)
    )

    return html, stats


# ================== PDF REPORT ==================


def build_pdf(filename, items, stats):
    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    heading = styles["Heading1"]
    heading.fontSize = 20
    heading.leading = 24

    # A4 na šířku
    doc = SimpleDocTemplate(
        filename,
        pagesize=landscape(A4),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    story = []

    date_str = date_de(TIMEZONE)
    intro_text = (
        f"Insgesamt {stats['total']} Artikel im Auswertungszeitraum. "
        f"{stats['critical']} davon als kritisch eingestuft, "
        f"{stats['international']} virale / internationale Erwähnungen. "
    )
    if stats["topic_counts"]:
        topic_str = ", ".join(f"{t} ({c})" for t, c in stats["topic_counts"][:3])
        intro_text += f"Schwerpunktthemen: {topic_str}."
    story.append(Paragraph(f"DE Monitoring – privat | {date_str}", heading))
    story.append(Spacer(1, 12))
    story.append(Paragraph(intro_text, normal))
    story.append(Spacer(1, 18))

    # Rozdělit články do témat
    topic_buckets = {}
    for it in items:
        topic_buckets.setdefault(it["topic"], []).append(it)

    # Témata řadíme podle max score uvnitř
    sorted_topics = sorted(
        topic_buckets.items(),
        key=lambda kv: max(x["score"] for x in kv[1]),
        reverse=True,
    )

    link_style = ParagraphStyle(
        "Link",
        parent=normal,
        textColor="#0050b3",
        underline=True,
    )

    for topic, topic_items in sorted_topics:
        story.append(Paragraph(topic, styles["Heading2"]))
        story.append(Spacer(1, 6))

        topic_items_sorted = sorted(
            topic_items, key=lambda x: x["score"], reverse=True
        )

        for it in topic_items_sorted:
            meta_bits = [it["source"]]
            if it["critical"]:
                meta_bits.append("■ Kritisch")
            if it["is_international"]:
                meta_bits.append("● Virale / internationale Erwähnung")
            meta_text = " · ".join(meta_bits)

            story.append(Paragraph(escape(it["title"]), normal))
            story.append(Paragraph(meta_text, normal))
            story.append(
                Paragraph(
                    f'<link href="{it["url"]}">{escape(it["url"])}</link>',
                    link_style,
                )
            )
            story.append(Spacer(1, 8))

        story.append(Spacer(1, 12))

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
        "attachments": [{"filename": pdf_name, "content": pdf_b64}],
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
        print("No news items found for today – nothing to send.")
        return

    html, stats = build_email_html(items)

    date_iso = datetime.now().strftime("%Y-%m-%d")
    pdf_name = f"DE_monitoring_privat_{date_iso}.pdf"
    build_pdf(pdf_name, items, stats)

    subject = f"📰 Kaufland Media & Review Briefing – {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
