import os 
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from time import mktime
from html import escape as html_escape
from xml.sax.saxutils import escape as xml_escape

import feedparser
from bs4 import BeautifulSoup

from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors

from helpers import date_de, classify


# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")  # např. "Kaufland Monitoring <reports@...>"
EMAIL_TO = os.getenv("EMAIL_TO")      # hlavní příjemce (Stefan)
CC = os.getenv("CC")
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "15"))  # max 15 v mailu, zbytek do PDF

BRAND_RED = "#E60000"

# Google News RSS feedy – můžeš později doplnit / změnit
FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]


# ================== RESEND HELPER ==================

def send_via_resend(subject: str, html_body: str, pdf_name: str | None = None) -> None:
    """
    Odeslání e-mailu přes Resend. Pokud pdf_name není None, připojí se PDF jako příloha.
    """
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY env variable is missing.")
    if not EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM env variable is missing.")
    if not EMAIL_TO:
        raise RuntimeError("EMAIL_TO env variable is missing.")

    payload: dict = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html_body,
    }

    if CC:
        payload["cc"] = [x.strip() for x in CC.split(",") if x.strip()]
    if BCC:
        payload["bcc"] = [x.strip() for x in BCC.split(",") if x.strip()]

    if pdf_name:
        with open(pdf_name, "rb") as f:
            pdf_bytes = f.read()
        import base64
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        payload["attachments"] = [
            {
                "filename": os.path.basename(pdf_name),
                "content": pdf_b64,
                "contentType": "application/pdf",
            }
        ]

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


# ================== NEWS FETCH & PROCESSING ==================

def parse_datetime(entry) -> datetime | None:
    """
    Vrátí UTC datetime z feedparser entry, pokud je k dispozici.
    """
    dt_struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not dt_struct:
        return None
    return datetime.fromtimestamp(mktime(dt_struct), tz=timezone.utc)


def is_recent(dt: datetime | None, hours: int = 36) -> bool:
    """
    Vrátí True, pokud je článek novější než X hodin (default 36).
    Když čas chybí, necháme ho projít (raději víc než málo).
    """
    if dt is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt >= cutoff


def assign_topic(title: str, summary: str) -> str:
    """
    Jednoduché tematické clustrování podle klíčových slov.
    """
    text = f"{title} {summary}".lower()

    if re.search(r"preis|rabatt|angebot|aktion|prospekt|günstig|billig", text):
        return "Preise & Aktionen"

    if re.search(r"rückruf|qualit|verunreinig|schadstoff|gesundheit|mangelhaft", text):
        return "Qualität & Rückruf"

    if re.search(r"kunden|kundin|service|warteschlange|filialleiter|erlebnis", text):
        return "Service & Kundenerlebnis"

    if re.search(r"mitarbeiter|tarif|streik|lohn|gehalt|personal|arbeitsplatz", text):
        return "Mitarbeiter & HR & Streik"

    if re.search(r"nachhaltig|klima|co2|umwelt|plastik|bio|tierwohl|esg", text):
        return "Nachhaltigkeit & ESG"

    if re.search(r"aldi|lidl|rewe|edeka|penny|netto|discounter|wettbewerb|handel", text):
        return "Wettbewerb & Markt"

    if re.search(r"werbung|kampagne|spot|sponsoring|image|marke|branding|puky|hockey", text):
        return "Image & Kampagnen"

    return "Sonstiges"


def is_urgent(text: str) -> bool:
    return bool(re.search(
        r"rückruf|skandal|boykott|shitstorm|datenschutz|krise|ermittlungen|streik",
        text,
        re.IGNORECASE,
    ))


def is_viral(host: str, title: str, summary: str) -> bool:
    """
    'Virální' = buď mezinárodní/doména mimo .de, nebo typicky virální kontext (TikTok, Social, Meme…)
    """
    text = f"{title} {summary}".lower()
    if not host.endswith(".de"):
        return True
    if re.search(r"tiktok|viral|instagram|x\.com|twitter|trend", text):
        return True
    return False


def fetch_news():
    seen_urls = set()
    items = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = getattr(e, "link", "").strip()
            if not link:
                continue
            if link in seen_urls:
                continue

            dt = parse_datetime(e)
            if not is_recent(dt, hours=36):
                continue

            seen_urls.add(link)

            title = getattr(e, "title", "").strip()
            raw_summary = getattr(e, "summary", "")
            desc = BeautifulSoup(raw_summary, "html.parser").get_text().strip()

            host, typ, base_score = classify(link, title)

            text_for_score = f"{title} {desc}".lower()
            score = base_score

            if "kaufland" in text_for_score:
                score += 1
            if re.search(r"rückruf|skandal|boykott|krise", text_for_score):
                score += 2
            if typ == "seriös":
                score += 1

            topic = assign_topic(title, desc)
            urgent = is_urgent(text_for_score)
            viral = is_viral(host, title, desc)

            if urgent:
                score += 2
            if viral:
                score += 1

            summary_short = (desc[:320] + "…") if len(desc) > 320 else desc

            items.append({
                "title": title,
                "url": link,
                "summary": summary_short,
                "summary_full": desc,
                "source": host,
                "type": typ,
                "score": score,
                "topic": topic,
                "urgent": urgent,
                "viral": viral,
                "published": dt,
            })

    # Deduplikace podle (host + normalized title)
    dedup = {}
    for it in items:
        key = (it["source"], re.sub(r"\s+", " ", it["title"].lower()))
        if key not in dedup:
            dedup[key] = it
        else:
            # necháme ten s vyšším score
            if it["score"] > dedup[key]["score"]:
                dedup[key] = it

    items = list(dedup.values())
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


# ================== EMAIL RENDER ==================

def render_email_html(top_items, stats) -> str:
    """
    Vygeneruje HTML těla e-mailu s Top N články.
    Zbytek článků jde do PDF.
    """
    date_str = date_de(TIMEZONE)

    total = stats["total"]
    urgent_count = stats["urgent"]
    viral_count = stats["viral"]
    by_topic = stats["by_topic"]

    # Executive summary (DE)
    exec_lines = [
        f"Heute wurden insgesamt <strong>{total}</strong> relevante Erwähnungen zu Kaufland erfasst.",
        f"Davon sind <strong>{urgent_count}</strong> als potenziell kritisch (Rückruf, Skandal, Boykott, Krise) eingestuft.",
    ]
    if viral_count > 0:
        exec_lines.append(f"Zusätzlich gibt es <strong>{viral_count}</strong> virale / internationale Erwähnungen.")

    # Tematické shrnutí (1–2 největší témata)
    if by_topic:
        sorted_topics = sorted(by_topic.items(), key=lambda x: x[1], reverse=True)
        top_topic_parts = [f"{t[0]} ({t[1]})" for t in sorted_topics[:3]]
        exec_lines.append("Thematisch dominieren heute: " + ", ".join(top_topic_parts) + ".")

    executive_summary_html = "".join(f"<p>{line}</p>" for line in exec_lines)

    # Top headlines list
    li_items = []
    for idx, it in enumerate(top_items, start=1):
        title = html_escape(it["title"])
        url = html_escape(it["url"])
        source = html_escape(it.get("source", ""))
        topic = html_escape(it.get("topic", ""))
        meta_parts = []

        if source:
            meta_parts.append(source)
        if topic:
            meta_parts.append(topic)
        if it.get("urgent"):
            meta_parts.append("⚠️ Kritisch")
        if it.get("viral"):
            meta_parts.append("🌍 Virale Erwähnung")

        meta = " · ".join(meta_parts)

        li_html = f"""
<li style="margin-bottom:10px;display:flex;align-items:flex-start;">
  <div style="width:24px;font-weight:bold;color:{BRAND_RED};">{idx}</div>
  <div style="flex:1;">
    <a href="{url}" style="font-weight:600;color:#111;text-decoration:none;">{title}</a>
    <div style="font-size:12px;color:#666;margin-top:2px;">{meta}</div>
  </div>
</li>
""".strip()
        li_items.append(li_html)

    top_headlines_html = "\n".join(li_items)

    html = f"""
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Kaufland Media Monitoring</title>
</head>
<body style="margin:0;padding:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;">
  <div style="max-width:800px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #eee;">
    <div style="background:{BRAND_RED};color:#ffffff;padding:16px 20px;">
      <h1 style="margin:0;font-size:20px;">Kaufland Media &amp; Review Briefing</h1>
      <p style="margin:4px 0 0 0;font-size:13px;opacity:0.9;">{html_escape(date_str)}</p>
    </div>

    <div style="padding:16px 20px 8px 20px;">
      <h2 style="margin:0 0 8px 0;font-size:16px;">Executive Summary</h2>
      {executive_summary_html}
      <p style="margin:8px 0 0 0;font-size:12px;color:#666;">
        Vollständige Liste inkl. thematischer Einordnung und aller Quellen im angehängten PDF.
      </p>
    </div>

    <div style="padding:8px 20px 16px 20px;">
      <h2 style="margin:0 0 8px 0;font-size:16px;">Top {len(top_items)} Schlagzeilen</h2>
      <ul style="list-style:none;padding:0;margin:8px 0 0 0;">
        {top_headlines_html}
      </ul>
    </div>
  </div>

  <div style="max-width:800px;margin:8px auto 0 auto;font-size:11px;color:#888;text-align:center;">
    <p style="margin:0 0 4px 0;">Interner Monitoring-Report. Bitte nicht weiterleiten.</p>
  </div>
</body>
</html>
""".strip()

    return html


# ================== PDF MAGAZINE RENDER ==================

def build_pdf(filename: str, items):
    """
    Vytvoří PDF ve stylu „magazine digest“ – A4 na šířku, rozdělené dle témat.
    Každý článek má klikací titulek, meta řádek a krátké shrnutí.
    """
    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    title_style.fontSize = 20
    title_style.textColor = colors.HexColor(BRAND_RED)

    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor(BRAND_RED),
        spaceAfter=4,
        spaceBefore=10,
    )

    article_title_style = ParagraphStyle(
        "ArticleTitle",
        parent=styles["Normal"],
        fontSize=11,
        leading=13,
        textColor=colors.HexColor("#000000"),
        spaceAfter=1,
    )

    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#666666"),
        spaceAfter=2,
    )

    summary_style = ParagraphStyle(
        "Summary",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#111111"),
        spaceAfter=5,
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=landscape(A4),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )

    story = []

    # Header
    story.append(Paragraph(f"DE Monitoring – privat | {date_de(TIMEZONE)}", title_style))
    story.append(Spacer(1, 6 * mm))

    # Editor notes – jednoduché shrnutí
    total = len(items)
    urgent_count = sum(1 for it in items if it["urgent"])
    viral_count = sum(1 for it in items if it["viral"])

    topics_count: dict[str, int] = {}
    for it in items:
        topics_count[it["topic"]] = topics_count.get(it["topic"], 0) + 1

    topics_sorted = sorted(topics_count.items(), key=lambda x: x[1], reverse=True)
    top_topics_text = ", ".join(f"{t[0]} ({t[1]})" for t in topics_sorted[:3])

    editor_text = (
        f"Insgesamt {total} Artikel im Auswertungszeitraum. "
        f"{urgent_count} davon als kritisch eingestuft, "
        f"{viral_count} virale / internationale Erwähnungen. "
    )
    if top_topics_text:
        editor_text += f"Schwerpunktthemen: {top_topics_text}."

    story.append(Paragraph(xml_escape(editor_text), summary_style))
    story.append(Spacer(1, 4 * mm))

    # Rozdělení podle témat
    topics_order = [
        "Preise & Aktionen",
        "Qualität & Rückruf",
        "Service & Kundenerlebnis",
        "Mitarbeiter & HR & Streik",
        "Nachhaltigkeit & ESG",
        "Wettbewerb & Markt",
        "Image & Kampagnen",
        "Sonstiges",
    ]

    items_by_topic: dict[str, list] = {t: [] for t in topics_order}
    for it in items:
        topic = it["topic"]
        if topic not in items_by_topic:
            items_by_topic[topic] = []
        items_by_topic[topic].append(it)

    # Pro každý topic – sekce
    for topic in topics_order:
        topic_items = items_by_topic.get(topic) or []
        if not topic_items:
            continue

        story.append(Paragraph(xml_escape(topic), section_title_style))
        story.append(Spacer(1, 2 * mm))

        for it in topic_items:
            title = it["title"]
            url = it["url"]
            source = it.get("source", "")
            meta_parts = []

            if source:
                meta_parts.append(source)
            if it.get("urgent"):
                meta_parts.append("⚠️ Kritisch")
            if it.get("viral"):
                meta_parts.append("🌍 Virale Erwähnung")

            meta = " · ".join(meta_parts)

            # "Logo" média jako textový label v závorkách
            host_label = f"[{source}]" if source else ""

            link_html = f'<a href="{xml_escape(url)}">{xml_escape(title)}</a> {xml_escape(host_label)}'
            story.append(Paragraph(link_html, article_title_style))

            if meta:
                story.append(Paragraph(xml_escape(meta), meta_style))

            summary = it.get("summary_full") or it.get("summary") or ""
            story.append(Paragraph(xml_escape(summary), summary_style))

        # trochu místa mezi sekcemi
        story.append(Spacer(1, 4 * mm))

    doc.build(story)


# ================== MAIN ==================

def compute_stats(items):
    stats = {
        "total": len(items),
        "urgent": sum(1 for it in items if it["urgent"]),
        "viral": sum(1 for it in items if it["viral"]),
        "by_topic": {},
    }
    for it in items:
        t = it["topic"]
        stats["by_topic"][t] = stats["by_topic"].get(t, 0) + 1
    return stats


def main():
    items = fetch_news()
    if not items:
        print("No news items found for this run.")
        # Můžeme poslat i prázdný report s poznámkou, ale zatím jen log.
        return

    stats = compute_stats(items)

    # Top N do mailu, zbytek do PDF (ale PDF obsahuje i top N)
    top_items = items[:MAX_TOP]

    html_body = render_email_html(top_items, stats)

    # PDF: všechny články
    today_str = datetime.now().strftime("%Y-%m-%d")
    pdf_name = f"DE_monitoring_privat_{today_str}.pdf"
    build_pdf(pdf_name, items)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html_body, pdf_name)


if __name__ == "__main__":
    main()
