import os
import json
import base64
import urllib.request
import urllib.error
from html import escape
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm

from helpers import date_de

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@gmail.com>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce (Stefan)
CC = os.getenv("CC")
BCC = os.getenv("BCC")

# WEEKLY_REVIEWS_JSON = data pro VŠECHNY filiálky za 7 dní
# očekává se seznam objektů:
# {
#   "region": "NRW",
#   "store": "Kaufland Köln-Mülheim",
#   "avg_week": 3.6,
#   "avg_prev_week": 3.9,
#   "count_week": 42,
#   "count_prev_week": 30
# }
WEEKLY_REVIEWS_JSON = os.getenv("WEEKLY_REVIEWS_JSON", "[]")

MIN_WEEKLY_COUNT = int(os.getenv("MIN_WEEKLY_COUNT", "5"))       # min. nových recenzí za týden
MIN_WEEKLY_DELTA = float(os.getenv("MIN_WEEKLY_DELTA", "0.2"))   # min. změna ratingu, aby to bylo zajímavé
MAX_WEEKLY_ROWS = int(os.getenv("MAX_WEEKLY_ROWS", "10"))        # max. řádků v každé tabulce


# ================== DATA Z WEEKLY_REVIEWS_JSON ==================


def load_weekly_reviews():
    """
    Načte WEEKLY_REVIEWS_JSON a vrátí list dictů:
      { region, store, avg_now, avg_prev, delta_avg, count_now, count_prev, delta_count }
    Používá se pro všechny další výpočty.
    """
    try:
        raw = WEEKLY_REVIEWS_JSON.strip()
        if not raw or raw == "[]":
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    rows = []
    for r in data:
        try:
            region = r.get("region", "–")
            store = r.get("store", "–")
            avg_now = float(r.get("avg_week", 0.0))
            avg_prev = float(r.get("avg_prev_week", avg_now))
            count_now = int(r.get("count_week", 0))
            count_prev = int(r.get("count_prev_week", 0))
        except Exception:
            # nekorektní záznam, přeskočit
            continue

        delta_avg = avg_now - avg_prev
        delta_count = count_now - count_prev

        rows.append(
            {
                "region": region,
                "store": store,
                "avg_now": round(avg_now, 2),
                "avg_prev": round(avg_prev, 2),
                "delta_avg": round(delta_avg, 2),
                "count_now": count_now,
                "count_prev": count_prev,
                "delta_count": delta_count,
            }
        )

    return rows


def split_views(rows):
    """
    Z jednoho seznamu udělá tři pohledy:
      - top_neg: největší pokles Ø ratingu
      - top_pos: největší nárůst Ø ratingu
      - top_vol: nejvíce nových recenzí
    Všude se filtruje podle MIN_WEEKLY_COUNT / MIN_WEEKLY_DELTA.
    """
    neg = [
        r
        for r in rows
        if r["count_now"] >= MIN_WEEKLY_COUNT and r["delta_avg"] <= -MIN_WEEKLY_DELTA
    ]
    pos = [
        r
        for r in rows
        if r["count_now"] >= MIN_WEEKLY_COUNT and r["delta_avg"] >= MIN_WEEKLY_DELTA
    ]
    vol = [r for r in rows if r["count_now"] >= MIN_WEEKLY_COUNT]

    neg_sorted = sorted(neg, key=lambda x: x["delta_avg"])[:MAX_WEEKLY_ROWS]
    pos_sorted = sorted(pos, key=lambda x: x["delta_avg"], reverse=True)[:MAX_WEEKLY_ROWS]
    vol_sorted = sorted(vol, key=lambda x: x["count_now"], reverse=True)[:MAX_WEEKLY_ROWS]

    return neg_sorted, pos_sorted, vol_sorted


# ================== PDF – WEEKLY REVIEW REPORT ==================


def build_weekly_pdf(filename, all_rows, neg_rows, pos_rows, vol_rows):
    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    h2 = styles["Heading2"]
    p_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
    )
    small = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=8.5,
        textColor="grey",
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
    story.append(
        Paragraph("Kaufland – Weekly Google Reviews Report (Deutschland)", title_style)
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph(date_de(TIMEZONE), small))
    story.append(Spacer(1, 8))

    # Celkový počet nových recenzí za týden (všechny filiálky dohromady)
    total_reviews = sum(r["count_now"] for r in all_rows)
    story.append(
        Paragraph(
            f"<strong>Neue Reviews (Woche gesamt):</strong> {total_reviews}",
            p_style,
        )
    )
    story.append(Spacer(1, 14))

    # Stručný úvod
    story.append(
        Paragraph(
            "Wöchentliche Auswertung der Google-Reviews aller Filialen – Fokus auf Veränderungen in Bewertung und Anzahl der Reviews.",
            p_style,
        )
    )
    story.append(Spacer(1, 12))

    def section(title, rows, icon):
        story.append(Paragraph(f"{icon} {title}", h2))
        story.append(Spacer(1, 6))
        if not rows:
            story.append(
                Paragraph(
                    "Keine auffälligen Veränderungen für diese Kategorie in dieser Woche.",
                    small,
                )
            )
            story.append(Spacer(1, 10))
            return

        for r in rows:
            region_store = f"{escape(r['region'])} – {escape(r['store'])}"
            txt = (
                f"<strong>{region_store}</strong><br/>"
                f"Ø Bewertung: {r['avg_prev']} → {r['avg_now']} "
                f"({('+' if r['delta_avg'] > 0 else '')}{r['delta_avg']})<br/>"
                f"Reviews: {r['count_prev']} → {r['count_now']} "
                f"({('+' if r['delta_count'] > 0 else '')}{r['delta_count']})"
            )
            story.append(Paragraph(txt, p_style))
            story.append(Spacer(1, 8))

        story.append(Spacer(1, 14))

    # 3 sekce
    section("Größter Rückgang der Ø-Bewertung", neg_rows, "🔻")
    section("Größter Anstieg der Ø-Bewertung", pos_rows, "🔺")
    section("Filialen mit den meisten neuen Reviews", vol_rows, "📈")

    # poznámka
    story.append(
        Paragraph(
            f"Gefiltert nach Filialen mit ≥ {MIN_WEEKLY_COUNT} neuen Reviews pro Woche "
            f"oder ≥ {MIN_WEEKLY_DELTA} Veränderung der Ø-Bewertung.",
            small,
        )
    )

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
    # načtení všech weekly dat (všechny filiálky)
    rows = load_weekly_reviews()
    neg_rows, pos_rows, vol_rows = split_views(rows)

    # celkový počet nových recenzí za všechny filiálky
    total_new = sum(r["count_now"] for r in rows)

    # HTML šablona pro weekly report
    with open("weekly_template.html", "r", encoding="utf-8") as f:
        tpl = f.read()

    # executive summary (DE)
    neg_count = len(neg_rows)
    pos_count = len(pos_rows)
    vol_count = len(vol_rows)

    summary_html = f"""
<p><strong>Insight:</strong> In der letzten Woche wurden insgesamt <strong>{total_new}</strong> neue Google-Reviews in allen deutschen Kaufland-Filialen erfasst. 
Davon zeigen <strong>{neg_count}</strong> Filialen einen signifikanten Rückgang und <strong>{pos_count}</strong> eine deutliche Verbesserung der Ø-Bewertung.</p>

<p><strong>Implikation:</strong> Die Übersicht zeigt sowohl negative Ausreißer als auch positive Entwicklungen sowie Filialen mit ungewöhnlich vielen neuen Reviews.</p>

<p><strong>Aktion:</strong> Fokus auf Filialen aus der Kategorie „Größter Rückgang der Ø-Bewertung“ – Weitergabe an das jeweilige Regionalmanagement empfohlen.</p>
""".strip()

    # tabulky do HTML
    def build_table_rows(table_rows):
        if not table_rows:
            return (
                '<tr><td colspan="6" class="muted">'
                "Keine Filialen mit auffälligen Veränderungen in dieser Kategorie."
                "</td></tr>"
            )
        out = []
        for r in table_rows:
            sign_avg = "+" if r["delta_avg"] > 0 else ""
            sign_cnt = "+" if r["delta_count"] > 0 else ""
            out.append(
                f"""
<tr>
  <td>{escape(r['region'])}</td>
  <td>{escape(r['store'])}</td>
  <td>{r['avg_prev']}</td>
  <td>{r['avg_now']}</td>
  <td>{sign_avg}{r['delta_avg']}</td>
  <td>{r['count_prev']} → {r['count_now']} ({sign_cnt}{r['delta_count']})</td>
</tr>""".strip()
            )
        return "\n".join(out)

    neg_rows_html = build_table_rows(neg_rows)
    pos_rows_html = build_table_rows(pos_rows)
    vol_rows_html = build_table_rows(vol_rows)

    html = tpl
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": EMAIL_TO or "",
        "{summary_html}": summary_html,
        "{neg_rows_html}": neg_rows_html,
        "{pos_rows_html}": pos_rows_html,
        "{vol_rows_html}": vol_rows_html,
        "{threshold_note}": (
            f"Gefiltert nach Filialen mit ≥ {MIN_WEEKLY_COUNT} neuen Reviews "
            f"oder ≥ {MIN_WEEKLY_DELTA} Veränderung der Ø-Bewertung pro Woche."
        ),
        "{total_new_reviews}": str(total_new),
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    pdf_name = f"DE_reviews_weekly_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_weekly_pdf(pdf_name, rows, neg_rows, pos_rows, vol_rows)

    subject = f"📊 Weekly Google Reviews | {total_new} neue Reviews | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
