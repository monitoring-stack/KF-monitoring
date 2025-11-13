import os, re, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import pytz

BRAND_RED = "#E60000"

BOULEVARD_DOMAINS = ["bild.de", "express.de", "tz.de", "promiflash.de"]
SERIOUS_HINTS     = ["handelsblatt", "lebensmittelzeitung", "faz.net", "sueddeutsche", "zeit.de", "tagesschau", "spiegel.de"]

KEYWORDS_URGENT = re.compile(r"Rückruf|Skandal|Boykott|Shitstorm|Datenschutz|Krise|Ermittlungen|Streik", re.I)


def date_de(tz_name="Europe/Berlin"):
    import locale
    try:
        locale.setlocale(locale.LC_TIME, 'de_DE.UTF-8')
    except Exception:
        pass
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).strftime("%A, %d. %B %Y")


def classify(url, title):
    host = re.sub(r"^https?://", "", url).split("/")[0]
    if any(d in host for d in BOULEVARD_DOMAINS):
        t = "Boulevard"
    elif any(d in host for d in SERIOUS_HINTS):
        t = "seriös"
    else:
        t = "neutral/spekulativ"
    score = (3 if t == "seriös" else 2 if t == "Boulevard" else 1)
    if re.search(r"Umsatz|Eröffnung|Rückruf|Skandal|Boykott|Krise|ESG|Invest", title, re.I):
        score += 2
    return host, t, score


def build_email(sender, recipient, subject, html_body, attachments=None, cc=None, bcc=None):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for att in attachments or []:
        part = MIMEBase('application', 'octet-stream')
        with open(att, 'rb') as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(att)}"')
        msg.attach(part)

    return msg


def send_via_gmail(msg, sender, app_password):
    """
    Bezpečné odeslání přes Gmail SMTP (SSL). Vyhazuje srozumitelné chyby,
    když chybí App Password nebo je špatné.
    """
    if not sender:
        raise RuntimeError("SENDER_EMAIL is empty/missing.")
    if not app_password:
        raise RuntimeError(
            "SMTP_APP_PASSWORD is empty/missing. "
            "Create a Google App Password (2-Step Verification ON) and paste the 16-char code (without spaces)."
        )

    # posbírej příjemce z To/Cc/Bcc
    to_addrs = []
    for header in ("To", "Cc", "Bcc"):
        v = msg.get(header)
        if v:
            to_addrs.extend([x.strip() for x in v.split(",") if x.strip()])
    if not to_addrs:
        raise RuntimeError("No recipients in To/Cc/Bcc.")

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx) as server:
            server.ehlo()
            server.login(sender, app_password)  # pokud je špatné/None → SMTPAuthenticationError/AttributeError
            server.sendmail(sender, to_addrs, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        # typicky kódy 534/535/534 – špatné App Password
        raise RuntimeError(
            f"SMTP auth failed for {sender}. "
            "Verify that 2-Step Verification is enabled and SMTP_APP_PASSWORD is the 16-char App Password (no spaces), "
            "and that SENDER_EMAIL matches the account you generated it for."
        ) from e
