import os
import re
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
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce
CC = os.getenv("CC")                          # např. "a@b.de,c@d.de"
BCC = os.getenv("BCC")

MAX_TOP = int(os.getenv("MAX_TOP", "10"))

FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]


# ================== POMOCNÉ FUNKCE ==================


def is_german_host(host: str) -> bool:
    """Rozliší DE vs. ostatní (pro International sekci)."""
    host = (host or "").lower()
    return host.endswith(".de") or host.endswith(".de/")


def fetch_news():
    """Stáhne news z Google News feedů, odfiltruje duplicity a nerelevantní položky."""
    seen_links = set()
    items = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen_links:
                continue
            seen_links.add(link)

            title = e.title or ""
            # Vynutíme přítomnost "Kaufland" v titulku / shrnutí,
            # aby se do seznamu nedostávaly články o jiných značkách.
            summary_raw = getattr(e, "summary", "") or ""
            plain_summary = BeautifulSoup(summary_raw, "html.parser").get_text()

            text_for_filter = f"{title} {plain_summary}"
            if "kaufland" not in text_for_filter.lower():
                continue

            host, typ, score = classify(link, title)

            items.append(
                {
                    "title": title.strip(),
                    "url": link,
                    "summary": (plain_summary[:260] + "…")
                    if len(plain_summary) > 260
                    else plain_summary,
                    "source": host,
                    "type": typ,
                    "score": score,
                    "why": "relevant" if score >= 4 else "beobachten",
                }
            )

    # Seřadit dle našeho skóre (proxy za relevanci / "čtenost")
    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def split_de_intl(items):
    """Rozdělí položky na DE a International podle domény."""
    de_items = []
    intl_items = []
    for it in items:
        host = it.get("source", "")
        if is_german_host(host):
            de_items.append(it)
        else:
            intl_items.append(it)

    return de_items, intl_items


# -------------------- GOOGLE REVIEWS (STUB) --------------------


def get_google_reviews_data():
    """
    Zatím žádné přímé napojení na Google Reviews (bez dalšího API / registrací).

    Návrh budoucího stavu:
      - napojení interních dat nebo služby typu SerpAPI,
      - pro každou sledovanou filii:
          * Ø hodnocení,
          * změna za 24 h (Δ),
          * počet nových recenzí,
          * flag 'auffällig' (výrazný pokles / nárůst, hodně nových negativních recenzí...).

    Tato funkce je připravená, aby později stačilo vrátit list dictů.
    Aktuálně vrací prázdný seznam => email zobrazí vysvětlující placeholder.
    """
    return []  # TODO: napojit na reálná data


# ================== PDF REPORT ==================


def build_pdf(filename, de_top, de_rest, intl_items):
    """
    Vytvoří čitelný PDF report:
      1) Top Schlagzeilen (DE)
      2) Weitere Erwähnungen (DE)
      3) International – Virale Erwähnungen
    """

    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    section_style = styles["Heading2"]

    article_title_style = ParagraphStyle(
        "ArticleTitle",
        parent=styles["Heading4"],
        fontSize=11,
        leading=14,
    )

    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=8.5,
        textColor="grey",
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=12,
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

    # Titulek
    story.append(
        Paragraph(f"Kaufland Media & Review Briefing – {date_de(TIMEZONE)}", title_style)
    )
    story.append(Spacer(1, 10))

    # ------------------- 1) TOP SCHLAGZEILEN (DE) -------------------
    if de_top:
        story.append(Paragraph("Top Schlagzeilen – Deutschland", section_style))
        story.append(
            Paragraph(
                "Priorisiert nach Relevanz/ Risiko (interner Score, nicht echte Reichweite).",
                meta_style,
            )
        )
        story.append(Spacer(1, 6))

        for idx, it in enumerate(de_top, start=1):
            story.append(
                Paragraph(f"{idx}. {escape(it['title'])}", article_title_style)
            )
            meta_parts = [escape(it.get("source", ""))]
            if it.get("type"):
                meta_parts.append(escape(it["type"]))
            if it.get("why"):
                meta_parts.append("Grund: " + escape(it["why"]))
            story.append(Paragraph(" · ".join(meta_parts), meta_style))

            if it.get("summary"):
                story.append(Paragraph(escape(it["summary"]), body_style))

            if it.get("url"):
                story.append(
                    Paragraph(
                        f"<link href='{it['url']}' color='blue'>{escape(it['url'])}</link>",
                        link_style,
                    )
                )

            story.append(Spacer(1, 8))

        story.append(Spacer(1, 10))

    # ------------------- 2) WEITERE ERWÄHNUNGEN (DE) -------------------
    if de_rest:
        story.append(Paragraph("Weitere Erwähnungen – Deutschland", section_style))
        story.append(
            Paragraph(
                "Ausgewählte weitere Kaufland-Artikel mit geringerer Priorität.",
                meta_style,
            )
        )
        story.append(Spacer(1, 6))

        for idx, it in enumerate(de_rest, start=1):
            story.append(
                Paragraph(f"{idx}. {escape(it['title'])}", article_title_style)
            )
            meta_parts = [escape(it.get("source", ""))]
            if it.get("type"):
                meta_parts.append(escape(it["type"]))
            story.append(Paragraph(" · ".join(meta_parts), meta_style))

            if it.get("summary"):
                story.append(Paragraph(escape(it["summary"]), body_style))

            if it.get("url"):
                story.append(
                    Paragraph(
                        f"<link href='{it['url']}' color='blue'>{escape(it['url'])}</link>",
                        link_style,
                    )
                )

            story.append(Spacer(1, 6))

        story.append(Spacer(1, 10))

    # ------------------- 3) INTERNATIONAL -------------------
    if intl_items:
        story.append(Paragraph("International – virale Erwähnungen", section_style))
        story.append(
            Paragraph(
                "Ausgewählte internationale / nicht-deutsche Quellen zu Kaufland, "
                "z.B. globale Branchen-Trends oder überregionale Berichterstattung.",
                meta_style,
            )
        )
        story.append(Spacer(1, 6))

        for idx, it in enumerate(intl_items, start=1):
            story.append(
                Paragraph(f"{idx}. {escape(it['title'])}", article_title_style)
            )
            meta_parts = [escape(it.get("source", ""))]
            if it.get("type"):
                meta_parts.append(escape(it["type"]))
            story.append(Paragraph(" · ".join(meta_parts), meta_style))

            if it.get("summary"):
                story.append(Paragraph(escape(it["summary"]), body_style))

            if it.get("url"):
                story.append(
                    Paragraph(
                        f"<link href='{it['url']}' color='blue'>{escape(it['url'])}</link>",
                        link_style,
                    )
                )

            story.append(Spacer(1, 6))

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
    # 1) NEWS
    all_items = fetch_news()
    de_items, intl_items = split_de_intl(all_items)

    de_top = de_items[:MAX_TOP]
    de_rest = de_items[MAX_TOP:]

    # 2) Google Reviews (zatím stub)
    reviews = get_google_reviews_data()

    # 3) Načti HTML šablonu emailu
    with open("email_template.html", "r", encoding="utf-8") as f:
        template_str = f.read()

    # === 1) Executive summary (DE) ===
    executive_summary_html = """
<p><strong>Insight:</strong> Kuratierte Top-Schlagzeilen (1–10) mit weiteren Erwähnungen und internationalen Hinweisen.</p>
<p><strong>Implikation:</strong> Schneller Überblick über Relevanz, Risiko und regionale Unterschiede in einem täglichen Briefing.</p>
<p><strong>Aktion:</strong> Google Reviews & Social Listening werden schrittweise angebunden; Alerts bei auffälligen Entwicklungen.</p>
""".strip()

    # === 2) Top headlines (DE) – HTML pro e-mail ===
    top_items_html = []
    for i, it in enumerate(de_top, start=1):
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

    # === 3) Google Reviews tabulka (zatím vysvětlující placeholder) ===
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
Aktuell noch kein automatisiertes Google-Reviews-Monitoring angebunden.
Empfehlung (nächster Ausbauschritt): Anzeige der Filialen mit den meisten neuen Bewertungen (24h),
stärkster Veränderung der Ø-Bewertung sowie auffälligen Häufungen negativer/positiver Reviews.
</td></tr>"""
        ]

    reviews_note = "Δ = Veränderung der Ø-Bewertung in den letzten 24 Stunden."

    # === 4) International seznam ===
    international_items_html = []
    for it in (intl_items or []):
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

    if not international_items_html:
        international_items_html.append(
            "<li class='item'><div>Heute keine relevanten internationalen Erwähnungen.</div></li>"
        )

    # === 5) Dosazení do HTML šablony (bez .format, bezpečné replace) ===
    html = template_str
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": EMAIL_TO or "",
        "{executive_summary_html}": executive_summary_html,
        "{top_count}": str(len(de_top)),
        "{top_headlines_html}": "\n".join(top_items_html),
        "{reviews_table_rows_html}": "\n".join(review_rows),
        "{reviews_note}": reviews_note,
        "{urgent_block_html}": "",  # budoucí blok pro echte Alerts
        "{rumors_block_html}": "",  # budoucí blok pro Boulevard / Spekulation
        "{international_html}": "\n".join(international_items_html),
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # === 6) PDF + odeslání ===
    pdf_name = f"DE_monitoring_privat_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, de_top, de_rest, intl_items)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
