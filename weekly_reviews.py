import os
import re
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime
from html import escape as html_escape

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm

from helpers import date_de

# ================== KONFIGURACE ==================

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")          # např. "Kaufland Monitoring <kaufland.monitoring@resend.dev>"
EMAIL_TO = os.getenv("EMAIL_TO")              # hlavní příjemce (Stefan)
CC = os.getenv("CC")
BCC = os.getenv("BCC")

# WEEKLY_REVIEWS_JSON = data pro všechny filiálky (agregované metriky)
# [
#   {
#     "store": "Kaufland München-Sendling",
#     "region": "Süd",           # volitelné, může být přepsáno GeoJSONem
#     "new_7": 12,
#     "avg_7": 3.9,
#     "neg_7": 5,
#     "new_30": 34,
#     "avg_30": 4.1,
#     "neg_30": 9,
#     "new_90": 80,
#     "avg_90": 4.2,
#     "neg_90": 20
#   },
#   ...
# ]
WEEKLY_REVIEWS_JSON = os.getenv("WEEKLY_REVIEWS_JSON", "[]")

# GeoJSON se seznamem všech filiálek (uMap export)
STORES_GEOJSON_PATH = os.getenv(
    "STORES_GEOJSON_PATH",
    "kaufland_filialen_deutschland__stand_8_10_2025_.geojson"
)

# minimální prahy – můžeš kdykoli upravit
MIN_NEW_7 = int(os.getenv("MIN_NEW_7", "3"))          # min. nových reviews za 7 dní, aby filiálka vstoupila do hodnocení
MIN_NEW_30 = int(os.getenv("MIN_NEW_30", "5"))
MIN_NEW_90 = int(os.getenv("MIN_NEW_90", "10"))

# kolik řádků chceme ukázat v top seznamech
MAX_ROWS_TOP = int(os.getenv("MAX_ROWS_TOP", "7"))


# ================== POMOCNÉ FUNKCE ==================


def normalize_name(name: str) -> str:
    """
    Normalizace názvu filiálky pro matching mezi GeoJSON a WEEKLY_REVIEWS_JSON.
    """
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def load_store_regions_from_geojson():
    """
    Načte GeoJSON s filiálkami a vrátí mapu:
        normalized_store_name -> region (např. Bundesland nebo jiný region)
    Pokud region v datech není, zkusí ho odhadnout z description; jinak '–'.
    """
    mapping = {}
    path = STORES_GEOJSON_PATH

    try:
        with open(path, "r", encoding="utf-8") as f:
            gj = json.load(f)
    except Exception as e:
        print(f"[weekly_reviews] Konnte GeoJSON {path} nicht lesen: {e}")
        return mapping

    features = gj.get("features", [])
    for feat in features:
        props = feat.get("properties") or {}
        name = props.get("name") or props.get("Name") or ""
        if not name:
            continue

        # pokus o region z vlastností
        region = (
            props.get("region")
            or props.get("Region")
            or props.get("state")
            or props.get("State")
            or props.get("Bundesland")
            or props.get("bundesland")
            or props.get("addr:state")
            or props.get("addr:region")
        )

        if not region:
            desc = props.get("description") or props.get("Beschreibung") or ""
            parts = [p.strip() for p in desc.split(",") if p.strip()]
            if len(parts) >= 2:
                # např. "PLZ Stadt, Bundesland"
                region = parts[-1]
            else:
                region = "–"

        mapping[normalize_name(name)] = region

    print(f"[weekly_reviews] Geladene Filial-Regionen aus GeoJSON: {len(mapping)}")
    return mapping


# ================== DATOVÉ FUNKCE ==================


def load_weekly_rows():
    """
    Načte WEEKLY_REVIEWS_JSON a doplní region dle GeoJSON (pokud je k dispozici).
    """
    # 1) regiony z GeoJSON
    geo_regions = load_store_regions_from_geojson()

    # 2) reviews JSON
    try:
        raw = (WEEKLY_REVIEWS_JSON or "").strip()
        if not raw or raw == "[]":
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
    except Exception as e:
        print(f"[weekly_reviews] WEEKLY_REVIEWS_JSON parse error: {e}")
        return []

    rows = []
    for r in data:
        try:
            store = r.get("store") or r.get("name") or "–"
            # region – priorita: JSON -> GeoJSON -> '–'
            region_from_json = r.get("region")
            norm_name = normalize_name(store)
            region_from_geo = geo_regions.get(norm_name)
            region = region_from_json or region_from_geo or "–"

            new_7 = int(r.get("new_7", 0))
            avg_7 = float(r.get("avg_7", 0.0)) if new_7 > 0 else 0.0
            neg_7 = int(r.get("neg_7", 0))

            new_30 = int(r.get("new_30", 0))
            avg_30 = float(r.get("avg_30", 0.0)) if new_30 > 0 else 0.0
            neg_30 = int(r.get("neg_30", 0))

            new_90 = int(r.get("new_90", 0))
            avg_90 = float(r.get("avg_90", 0.0)) if new_90 > 0 else 0.0
            neg_90 = int(r.get("neg_90", 0))
        except Exception:
            # špatně strukturovaný záznam – přeskočíme
            continue

        neg_share_7 = (neg_7 / new_7) if new_7 > 0 else 0.0
        neg_share_30 = (neg_30 / new_30) if new_30 > 0 else 0.0
        neg_share_90 = (neg_90 / new_90) if new_90 > 0 else 0.0

        rows.append(
            {
                "region": region,
                "store": store,
                "new_7": new_7,
                "avg_7": round(avg_7, 2),
                "neg_7": neg_7,
                "neg_share_7": round(neg_share_7, 3),
                "new_30": new_30,
                "avg_30": round(avg_30, 2),
                "neg_30": neg_30,
                "neg_share_30": round(neg_share_30, 3),
                "new_90": new_90,
                "avg_90": round(avg_90, 2),
                "neg_90": neg_90,
                "neg_share_90": round(neg_share_90, 3),
            }
        )
    return rows


def compute_risk_scores(rows):
    """
    Spočítá jednoduché risk-score 0–100 pro každou filiálku.
    Skóre je kombinace:
      - podílu negativních recenzí (7 dní)
      - poklesu průměru (7 vs 30/90 dní)
      - nárůstu počtu recenzí (7 dní vs. průměr všech)
    """
    if not rows:
        return []

    avg_new7_all = sum(r["new_7"] for r in rows) / max(len(rows), 1)

    scored = []
    for r in rows:
        new_7 = r["new_7"]
        avg_7 = r["avg_7"]
        avg_30 = r["avg_30"]
        avg_90 = r["avg_90"]
        neg_share_7 = r["neg_share_7"]

        # 1) negativní podíl – max 40 bodů
        neg_component = 0.0
        if new_7 >= MIN_NEW_7:
            if neg_share_7 <= 0.2:
                neg_component = 0.0
            elif neg_share_7 >= 0.5:
                neg_component = 40.0
            else:
                neg_component = 40.0 * (neg_share_7 - 0.2) / 0.3

        # 2) pokles ratingu – max 40 bodů
        drop_component = 0.0
        ref_avg = avg_30 if avg_30 > 0 else avg_90
        if ref_avg > 0 and avg_7 > 0 and new_7 >= MIN_NEW_7:
            delta = ref_avg - avg_7  # kladné = zhoršení
            if delta <= 0:
                drop_component = 0.0
            elif delta >= 0.7:
                drop_component = 40.0
            else:
                drop_component = 40.0 * (delta / 0.7)

        # 3) nárůst objemu recenzí – max 20 bodů
        volume_component = 0.0
        if avg_new7_all > 0 and new_7 >= MIN_NEW_7:
            rel = new_7 / avg_new7_all
            if rel <= 1.0:
                volume_component = 0.0
            elif rel >= 3.0:
                volume_component = 20.0
            else:
                volume_component = 20.0 * (rel - 1.0) / 2.0

        risk = neg_component + drop_component + volume_component
        if risk > 100.0:
            risk = 100.0

        out = dict(r)
        out["risk_score"] = round(risk, 1)
        scored.append(out)

    return scored


def aggregate_totals(rows):
    """
    Celkové souhrny za Německo.
    """
    total_new_7 = sum(r["new_7"] for r in rows)
    total_neg_7 = sum(r["neg_7"] for r in rows)
    neg_share_7 = (total_neg_7 / total_new_7) if total_new_7 > 0 else 0.0

    total_new_30 = sum(r["new_30"] for r in rows)
    total_neg_30 = sum(r["neg_30"] for r in rows)
    neg_share_30 = (total_neg_30 / total_new_30) if total_new_30 > 0 else 0.0

    def weighted_avg(field_avg, field_n):
        total_weighted = sum(
            r[field_avg] * r[field_n] for r in rows if r[field_n] > 0
        )
        total_n = sum(r[field_n] for r in rows)
        if total_n == 0:
            return 0.0
        return total_weighted / total_n

    avg7_weighted = weighted_avg("avg_7", "new_7")
    avg30_weighted = weighted_avg("avg_30", "new_30")
    avg90_weighted = weighted_avg("avg_90", "new_90")

    return {
        "total_new_7": total_new_7,
        "total_neg_7": total_neg_7,
        "neg_share_7": round(neg_share_7, 3),
        "total_new_30": total_new_30,
        "total_neg_30": total_neg_30,
        "neg_share_30": round(neg_share_30, 3),
        "avg7_weighted": round(avg7_weighted, 2),
        "avg30_weighted": round(avg30_weighted, 2),
        "avg90_weighted": round(avg90_weighted, 2),
    }


def aggregate_by_region(rows):
    """
    Vytvoří agregace podle regionu.
    """
    regions = {}
    for r in rows:
        region = r["region"] or "–"
        bucket = regions.setdefault(
            region,
            {
                "region": region,
                "total_new_7": 0,
                "total_neg_7": 0,
                "total_new_30": 0,
                "total_neg_30": 0,
            },
        )
        bucket["total_new_7"] += r["new_7"]
        bucket["total_neg_7"] += r["neg_7"]
        bucket["total_new_30"] += r["new_30"]
        bucket["total_neg_30"] += r["neg_30"]

    for region, aggr in regions.items():
        total_new_7 = aggr["total_new_7"]
        total_neg_7 = aggr["total_neg_7"]
        total_new_30 = aggr["total_new_30"]
        total_neg_30 = aggr["total_neg_30"]

        aggr["neg_share_7"] = round(
            (total_neg_7 / total_new_7) if total_new_7 > 0 else 0.0, 3
        )
        aggr["neg_share_30"] = round(
            (total_neg_30 / total_new_30) if total_new_30 > 0 else 0.0, 3
        )

    result = sorted(
        regions.values(),
        key=lambda x: x["total_new_7"],
        reverse=True,
    )
    return result


# ================== PDF VÝSTUP ==================


def build_weekly_pdf(filename, rows, top_neg, top_pos, by_region):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h2 = styles["Heading2"]
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontSize=10,
        leading=13,
    )
    small = ParagraphStyle(
        "Small",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
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

    story.append(Paragraph("Kaufland – Weekly Google Reviews (Deutschland)", title_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(date_de(TIMEZONE), small))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Regionale Übersicht (7 Tage)", h2))
    story.append(Spacer(1, 4))
    if not by_region:
        story.append(
            Paragraph(
                "Noch keine aggregierten Daten nach Regionen vorhanden.",
                small,
            )
        )
    else:
        for r in by_region:
            txt = (
                f"<strong>{html_escape(r['region'])}</strong>: "
                f"{r['total_new_7']} neue Reviews (7 Tage), "
                f"{r['total_neg_7']} davon negativ "
                f"({round((r['neg_share_7'] * 100), 1)} %)."
            )
            story.append(Paragraph(txt, body))
            story.append(Spacer(1, 3))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Top Problemfilialen (höchstes Risiko)", h2))
    story.append(Spacer(1, 4))
    if not top_neg:
        story.append(
            Paragraph("Keine Filialen mit auffälligem Risiko in dieser Woche.", small)
        )
    else:
        for r in top_neg:
            txt = (
                f"<strong>{html_escape(r['store'])}</strong> "
                f"({html_escape(r['region'])}) – "
                f"Risk-Score: {r['risk_score']}<br/>"
                f"Neue Reviews (7 Tage): {r['new_7']} "
                f"(Ø {r['avg_7']}, "
                f"{round(r['neg_share_7']*100,1)} % negativ)<br/>"
                f"30 Tage: {r['new_30']} Reviews, Ø {r['avg_30']} · "
                f"90 Tage: Ø {r['avg_90']}"
            )
            story.append(Paragraph(txt, body))
            story.append(Spacer(1, 5))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Positive Ausreißer (Verbesserung / viele 5★)", h2))
    story.append(Spacer(1, 4))
    if not top_pos:
        story.append(
            Paragraph("Keine auffälligen positiven Ausreißer in dieser Woche.", small)
        )
    else:
        for r in top_pos:
            txt = (
                f"<strong>{html_escape(r['store'])}</strong> "
                f"({html_escape(r['region'])}) – "
                f"Neue Reviews (7 Tage): {r['new_7']} "
                f"(Ø {r['avg_7']}, "
                f"{round(r['neg_share_7']*100,1)} % negativ)<br/>"
                f"30 Tage: {r['new_30']} Reviews, Ø {r['avg_30']} · "
                f"90 Tage: Ø {r['avg_90']}"
            )
            story.append(Paragraph(txt, body))
            story.append(Spacer(1, 5))

    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            "Hinweis: Risk-Score (0–100) basiert auf Anteil negativer Reviews, "
            "Veränderung der Durchschnittsbewertung (7 vs. 30/90 Tage) und relativer Anzahl neuer Reviews.",
            small,
        )
    )

    doc.build(story)


# ================== RESEND HELPER ==================


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
    rows_raw = load_weekly_rows()
    if not rows_raw:
        print("WEEKLY_REVIEWS_JSON is empty or invalid – no weekly report generated.")
        return

    rows = compute_risk_scores(rows_raw)
    totals = aggregate_totals(rows)
    by_region = aggregate_by_region(rows)

    top_neg = [
        r
        for r in rows
        if r["new_7"] >= MIN_NEW_7 and r["risk_score"] >= 30.0
    ]
    top_neg = sorted(top_neg, key=lambda x: x["risk_score"], reverse=True)[
        :MAX_ROWS_TOP
    ]

    top_pos = [
        r
        for r in rows
        if r["new_7"] >= MIN_NEW_7 and r["neg_share_7"] <= 0.15 and r["avg_7"] >= 4.2
    ]
    top_pos = sorted(
        top_pos,
        key=lambda x: (x["avg_7"], x["new_7"]),
        reverse=True,
    )[:MAX_ROWS_TOP]

    with open("weekly_template.html", "r", encoding="utf-8") as f:
        tpl = f.read()

    total_new_7 = totals["total_new_7"]
    total_neg_7 = totals["total_neg_7"]
    neg_share_7_pct = round(totals["neg_share_7"] * 100, 1)
    avg7 = totals["avg7_weighted"]
    avg30 = totals["avg30_weighted"]
    avg90 = totals["avg90_weighted"]

    summary_html = f"""
<p><strong>Insight:</strong> In der letzten Woche wurden insgesamt <strong>{total_new_7}</strong> neue Google-Reviews über alle deutschen Kaufland-Filialen erfasst. 
Davon waren <strong>{total_neg_7}</strong> negativ (1–2★), was einem Anteil von <strong>{neg_share_7_pct} %</strong> entspricht.</p>

<p><strong>Implikation:</strong> Die Ø-Bewertung der neuen Reviews liegt aktuell bei <strong>{avg7}</strong> (30 Tage: {avg30}, 90 Tage: {avg90}). 
Filialen mit auffälliger Verschlechterung oder ungewöhnlich vielen neuen Reviews sind unten hervorgehoben.</p>

<p><strong>Aktion:</strong> Fokus auf Filialen in der Liste „Top Problemfilialen (höchstes Risiko)“ – Weitergabe an das jeweilige Regionalmanagement empfohlen.</p>
""".strip()

    def table_rows_for_html(table_rows):
        if not table_rows:
            return (
                '<tr><td colspan="7" class="muted">'
                "Keine Filialen mit erhöhtem Risiko in dieser Kategorie."
                "</td></tr>"
            )
        out = []
        for r in table_rows:
            neg_pct = round(r["neg_share_7"] * 100, 1)
            out.append(
                f"""
<tr>
  <td>{html_escape(r['region'])}</td>
  <td>{html_escape(r['store'])}</td>
  <td>{r['new_7']}</td>
  <td>{r['avg_7']}</td>
  <td>{neg_pct} %</td>
  <td>{r['risk_score']}</td>
  <td>{r['new_30']} / {r['new_90']}</td>
</tr>""".strip()
            )
        return "\n".join(out)

    neg_rows_html = table_rows_for_html(top_neg)
    pos_rows_html = table_rows_for_html(top_pos)

    if not by_region:
        region_rows_html = (
            '<tr><td colspan="4" class="muted">Keine regionalen Daten vorhanden.</td></tr>'
        )
    else:
        reg_rows = []
        for r in by_region:
            neg_pct = (
                f"{round(r['neg_share_7']*100,1)} %"
                if r["total_new_7"] > 0
                else "–"
            )
            reg_rows.append(
                f"""
<tr>
  <td>{html_escape(r['region'])}</td>
  <td>{r['total_new_7']}</td>
  <td>{r['total_neg_7']}</td>
  <td>{neg_pct}</td>
</tr>""".strip()
            )
        region_rows_html = "\n".join(reg_rows)

    html = tpl
    replacements = {
        "{date_str}": date_de(TIMEZONE),
        "{tz}": TIMEZONE,
        "{recipient}": html_escape(EMAIL_TO or ""),
        "{summary_html}": summary_html,
        "{neg_rows_html}": neg_rows_html,
        "{pos_rows_html}": pos_rows_html,
        "{region_rows_html}": region_rows_html,
        "{total_new_reviews}": str(total_new_7),
        "{threshold_note}": (
            f"Risk-Score (0–100) basiert auf Anteil negativer Reviews, Veränderung der Durchschnittsbewertung "
            f"(7 vs. 30/90 Tage) und relativer Anzahl neuer Reviews pro Filiale. "
            f"Gefiltert nach Filialen mit mindestens {MIN_NEW_7} neuen Reviews (7 Tage)."
        ),
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    pdf_name = f"DE_reviews_weekly_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_weekly_pdf(pdf_name, rows, top_neg, top_pos, by_region)

    subject = f"📊 Weekly Google Reviews – Deutschland | {total_new_7} neue Reviews | {date_de(TIMEZONE)}"
    send_via_resend(subject, html, pdf_name)


if __name__ == "__main__":
    main()
