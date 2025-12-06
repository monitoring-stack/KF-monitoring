import os
import re
from datetime import datetime
import pytz

BRAND_RED = "#E60000"

# Domény pro rychlou kategorizaci
BOULEVARD_DOMAINS = [
    "bild.de", "express.de", "tz.de", "promiflash.de",
]
SERIOUS_HINTS = [
    "handelsblatt", "lebensmittelzeitung", "faz.net",
    "sueddeutsche", "zeit.de", "tagesschau", "spiegel.de",
]

# Klíčová slova pro „kritické“ články
CRITICAL_KEYWORDS = re.compile(
    r"(rückruf|skandal|boykott|shitstorm|krise|ermittlung|ermittlungen|"
    r"streik|vergiftung|giftig|lebensgefährlich|hygienemangel)",
    re.I,
)

# Pozitivní / provozní slova, která „zjemní“ hodnocení
POSITIVE_OVERRIDE = re.compile(
    r"(eröffnung|öffnet|neu(eröffnung)?|modernisiert|renoviert|"
    r"feier|jubel|rekord|auszeichnung|preis)",
    re.I,
)


def date_de(tz_name: str = "Europe/Berlin") -> str:
    """
    Vrátí aktuální datum v DE formátu, včetně názvu dne.
    """
    import locale
    try:
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except Exception:
        # na některých systémech locale není, nevadí – použije anglické názvy
        pass

    tz = pytz.timezone(tz_name)
    return datetime.now(tz).strftime("%A, %d. %B %Y")


def classify(url: str, title: str, summary: str = ""):
    """
    Klasifikace článku:
    - vrací (host, typ, score, is_critical, topic, is_international)

    score:
    - základ 1–3 podle typu média (seriös / Boulevard / neutral)
    - +2 za obsahová klíčová slova (Rückruf, Skandal, Invest, Eröffnung…)
    """

    host = re.sub(r"^https?://", "", url).split("/")[0].lower()

    # typ média
    if any(d in host for d in BOULEVARD_DOMAINS):
        medium_type = "Boulevard"
        base_score = 2
    elif any(d in host for d in SERIOUS_HINTS):
        medium_type = "seriös"
        base_score = 3
    else:
        medium_type = "neutral/spekulativ"
        base_score = 1

    text = f"{title} {summary}".lower()

    # obsahová posila score
    if re.search(r"(umsatz|eröffnung|rückruf|skandal|boykott|krise|esg|invest)",
                 text, re.I):
        base_score += 2

    # kritičnost – ale s ohledem na pozitivní override
    has_critical = bool(CRITICAL_KEYWORDS.search(text))
    has_positive = bool(POSITIVE_OVERRIDE.search(text))

    # Pokud je článek „provozní / pozitivní“ (otevírá, modernizuje),
    # tak ho jako kritický NEoznačíme, i když je tam např. „Flaute“.
    is_critical = has_critical and not has_positive

    # základní topic – hodně jednoduché, ale přehledné
    topic = "Sonstiges"
    if re.search(r"rückruf|qualität|lebensgefährlich|produkt",
                 text, re.I):
        topic = "Qualität & Rückruf"
    elif re.search(r"hygiene|filiale|markt|öffnung|öffnen|eröffnung|umbau|modernisiert",
                   text, re.I):
        topic = "Hygiene & Filialbetrieb"
    elif re.search(r"preis(e)?|angebote|rabatt|billig|teuer",
                   text, re.I):
        topic = "Preis & Wettbewerb"

    # „mezinárodní / virální“ – cokoliv, co není .de doména
    is_international = not host.endswith(".de")

    return host, medium_type, base_score, is_critical, topic, is_international
