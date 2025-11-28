import os
import json
from datetime import datetime
from html import escape
import urllib.request
import urllib.error

# ============ ENV ============

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
WEEKLY_REVIEWS_JSON = os.getenv("WEEKLY_REVIEWS_JSON", "{}")


# ============ HELPER – RESEND ============

def send_via_resend(subject: str, html_body: str) -> None:
    """
    Odešle jednoduchý HTML e-mail přes Resend bez přílohy.
    """
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY env variable is missing.")
    if not EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM env variable is missing.")
    if not EMAIL_TO:
        raise RuntimeError("EMAIL_TO env variable is missing.")

    payload = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html_body,
    }

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


# ============ DATA PARSING ============

def load_weekly_data() -> dict:
    """
    Načte JSON ze secretu WEEKLY_REVIEWS_JSON.
    Bude fungovat i s jednodušší strukturou – jen obalíme do defaultů.
    """
    try:
        raw = WEEKLY_REVIEWS_JSON.strip()
        if not raw:
            return {}
        data = json.loads(raw)
    except Exception as e:
        print("Cannot parse WEEKLY_REVIEWS_JSON:", e)
        data = {}

    # Očekávaný tvar (ale volný):
    # {
    #   "generated_at": "...",
    #   "window_days": 7,
    #   "total_new_reviews": 123,
    #   "stores": [
    #       {
    #         "store_id": "DE-1234",
    #         "name": "Kaufland Berlin-Neukölln",
    #         "region": "Berlin",
    #         "new_reviews": 25,
    #         "new_negative": 4,
    #         "share_negative": 0.16,
    #         "delta_rating": -0.12,
    #         "avg_rating": 4.1,
    #       },
    #       ...
    #   ]
    # }

    if "stores" not in data:
        data["stores"] = []

    data.setdefault("window_days", 7)
    data.setdefault("total_new_reviews", sum(s.get("new_reviews", 0) for s in data["stores"]))

    return data


# ============ HTML RENDERING ============

def render_email_html(data: dict) -> str:
    stores = data.get("stores", [])
    window_days = data.get("window_days", 7)
    total_new = data.get("total_new_reviews", 0)

    # Top podle různých kritérií
    top_by_new = sorted(stores, key=lambda s: s.get("new_reviews", 0), reverse=True)[:5]
    top_by_delta = sorted(stores, key=lambda s: s.get("delta_rating", 0.0), reverse=True)[:5]
    top_by_neg_share = sorted(
        stores,
        key=lambda s: s.get("share_negative", 0.0),
        reverse=True
    )[:5]

    def fmt_store_row(s: dict) -> str:
        name = escape(s.get("name", "Unbekannte Filiale"))
        region = escape(s.get("region", ""))
        new_reviews = s.get("new_reviews", 0)
        new_negative = s.get("new_negative", 0)
        share_negative = s.get("share_negative", 0.0)
        avg_rating = s.get("avg_rating", 0.0)
        delta = s.get("delta_rating", 0.0)

        share_pct = f"{share_negative*100:.1f} %" if share_negative is not None else "–"
        delta_str = f"{delta:+.2f}" if delta is not None else "–"

        return f"""
<tr>
  <td><strong>{name}</strong><br><span style="color:#666;">{region}</span></td>
  <td style="text-align:right;">{new_reviews}</td>
  <td style="text-align:right;">{new_negative}</td>
  <td style="text-align:right;">{share_pct}</td>
  <td style="text-align:right;">{avg_rating:.2f}</td>
  <td style="text-align:right;">{delta_str}</td>
</tr>
""".strip()

    def block_table(title: str, items: list) -> str:
        if not items:
            return f"""
<h3 style="margin:24px 0 8px 0;">{escape(title)}</h3>
<p style="color:#666;">Keine auffälligen Filialen in diesem Segment.</p>
""".strip()

        rows = "\n".join(fmt_store_row(s) for s in items)
        return f"""
<h3 style="margin:24px 0 8px 0;">{escape(title)}</h3>
<table width="100%" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px;">
  <thead>
    <tr style="background:#f2f2f2;">
      <th align="left">Filiale / Region</th>
      <th align="right">Neue Reviews</th>
      <th align="right">davon negativ</th>
      <th align="right">Anteil negativ</th>
      <th align="right">Ø Bewertung</th>
      <th align="right">Δ Ø Bewertung</th>
    </tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>
""".strip()

    html = f"""
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <title>Weekly Google Reviews – Kaufland</title>
</head>
<body style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#111;line-height:1.5;">
  <h1 style="color:#E60000;font-size:22px;margin-bottom:4px;">Kaufland – Weekly Google Reviews</h1>
  <p style="margin:0 0 4px 0;">Zeitraum: letzte {window_days} Tage</p>
  <p style="margin:0 0 16px 0;">Gesamtzahl neuer Reviews (alle Filialen): <strong>{total_new}</strong></p>

  <h2 style="font-size:16px;margin-top:16px;">Executive Summary</h2>
  <ul>
    <li>Fokus: Filialen mit auffälligem Anstieg an neuen Reviews, höherem Anteil negativer Bewertungen oder deutlicher Veränderung der Ø-Bewertung.</li>
    <li>Die Tabellen unten zeigen jeweils die Top 5 Ausreißer nach unterschiedlichen Kriterien.</li>
  </ul>

  {block_table("Top-Filialen nach Anzahl neuer Reviews", top_by_new)}

  {block_table("Größte Verbesserung der Ø-Bewertung", top_by_delta)}

  {block_table("Höchster Anteil negativer Reviews", top_by_neg_share)}

  <p style="margin-top:24px;color:#666;font-size:12px;">
    Hinweis: Auswertung basiert auf Google Reviews-Daten aller Filialen; Schwellenwerte und Logik sind im Pilotmodus und können angepasst werden.
  </p>
</body>
</html>
""".strip()

    return html


# ============ MAIN ============

def main():
    data = load_weekly_data()
    html = render_email_html(data)
    subject = "📝 Kaufland – Weekly Google Reviews"

    send_via_resend(subject, html)


if __name__ == "__main__":
    main()
