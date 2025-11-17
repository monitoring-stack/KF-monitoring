import re
from datetime import datetime
import pytz
import locale

BRAND_RED = "#E60000"

BOULEVARD_DOMAINS = ["bild.de", "express.de", "tz.de", "promiflash.de"]
SERIOUS_HINTS = [
    "handelsblatt",
    "lebensmittelzeitung",
    "faz.net",
    "sueddeutsche",
    "zeit.de",
    "tagesschau",
    "spiegel.de",
]


def date_de(tz_name="Europe/Berlin"):
    """
    Vrátí dnešní datum v německém formátu, např. 'Donnerstag, 14. November 2025'.
    """
    try:
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except Exception:
        # fallback – když systém locale nemá
        pass

    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    return now.strftime("%A, %d. %B %Y")


def classify(url, title):
    """
    Z URL a titulku odhadne:
      - host (doména)
      - typ ('Boulevard', 'seriös', 'neutral/spekulativ')
      - score (číslo ~ relevanci / riziku)
    """
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()

    if any(d in host for d in BOULEVARD_DOMAINS):
        t = "Boulevard"
    elif any(d in host for d in SERIOUS_HINTS):
        t = "seriös"
    else:
        t = "neutral/spekulativ"

    # základní skóre podle typu média
    score = 3 if t == "seriös" else 2 if t == "Boulevard" else 1

    # klíčová slova zvyšující skóre
    if re.search(
        r"Umsatz|Eröffnung|Rückruf|Skandal|Boykott|Krise|ESG|Invest|Streik|Datenschutz",
        title,
        re.I,
    ):
        score += 2

    return host, t, score
