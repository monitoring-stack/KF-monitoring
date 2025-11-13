import os
import re
import base64
from datetime import datetime

import pytz
import requests

BRAND_RED = "#E60000"

BOULEVARD_DOMAINS = [
    "bild.de",
    "express.de",
    "tz.de",
    "promiflash.de",
]

SERIOUS_HINTS = [
    "handelsblatt",
    "lebensmittelzeitung",
    "faz.net",
    "sueddeutsche",
    "zeit.de",
    "tagesschau",
    "spiegel.de",
]

KEYWORDS_URGENT = re.compile(
    r"Rückruf|Skandal|Boykott|Shitstorm|Datenschutz|Krise|Ermittlungen|Streik",
    re.I,
)


def date_de(tz_name="Europe/Berlin"):
    """Datum v DE formátu, např. Montag, 13. November 2025."""
    import locale

    try:
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except Exception:
        # fallback – když na serveru není de_DE locale
        pass

    tz = pytz.timezone(tz_name)
    return datetime.now(tz).strftime("%A, %d. %B %Y")


def classify(url: str, title: str):
    """Vrátí (host, typ, score). Typ ~ seriozní / bulvár / neutrální."""
    host = re.sub(r"^https?://", "", url).split("/")[0]

    if any(d in host for d in BOULEVARD_DOMAINS):
        t = "Boulevard"
    elif any(d in host for d in SERIOUS_HINTS):
        t = "seriös"
    else:
        t = "neutral/spekulativ"

    score = 3 if t == "seriös" else 2 if t == "Boulevard" else 1

    if re.search(
        r"Umsatz|Eröffnung|Rückruf|Skandal|Boykott|Krise|ESG|Invest",
        title,
        re.I,
    ):
        score += 2

    return host, t, score


def send_via_resend(
    recipient: str,
    subject: str,
    html_body: str,
    attachment_path: str | None = None,
):
    """Pošle e-mail přes Resend API (HTML + volitelný PDF attachment)."""
    api_key = os.getenv("RESEND_API_KEY")
    sender = os.getenv("EMAIL_FROM") or "kaufland@resend.dev"

    if not api_key:
        raise RuntimeError("RESEND_API_KEY není nastaven (GitHub secret).")
    if not recipient:
        raise RuntimeError("RECIPIENT není nastaven.")

    data: dict = {
        "from": sender,
        "to": [recipient],
        "subject": subject,
        "html": html_body,
    }

    if attachment_path:
        with open(attachment_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("ascii")
        data["attachments"] = [
            {
                "filename": os.path.basename(attachment_path),
                "content": content_b64,
            }
        ]

    resp = requests.post(
        "https://api.resend.com/emails",
        json=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if resp.status_code >= 300:
        raise RuntimeError(
            f"Resend error {resp.status_code}: {resp.text}"
        )

    print("✅ E-mail odeslán přes Resend")
