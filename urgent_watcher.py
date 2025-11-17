import os
import json
import urllib.request
import urllib.error
from html import escape

import feedparser
from bs4 import BeautifulSoup

from helpers import date_de, classify

TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
CC = os.getenv("CC")
BCC = os.getenv("BCC")

FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott+OR+Shitstorm+OR+Krise&hl=de&gl=DE&ceid=DE:de",
]

URGENT_KEYWORDS = [
    "rückruf",
    "skandal",
    "boykott",
    "shitstorm",
    "datenschutz",
    "krise",
    "ermittlungen",
    "streik",
]


def send_via_resend(subject, html):
    if not RESEND_API_KEY or not EMAIL_FROM or not EMAIL_TO:
        print("Resend env vars missing, skipping urgent alert.")
        return

    payload = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": subject,
        "html": html,
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
            print("Resend urgent response:", resp.status, body)
    except urllib.error.HTTPError as e:
        print("Resend urgent HTTP error:", e.code, e.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print("Resend urgent connection error:", e.reason)


def is_urgent(item):
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    return any(kw in text for kw in URGENT_KEYWORDS)


def fetch_urgent():
    seen = set()
    urgent_items = []

    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)

            title = e.title or ""
            desc = BeautifulSoup(getattr(e, "summary", "") or "", "html.parser").get_text()
            host, typ, score = classify(link, title)

            item = {
                "title": title.strip(),
                "url": link,
                "summary": desc.strip(),
                "source": host,
                "type": typ,
                "score": score,
            }

            if is_urgent(item) or score >= 5:
                urgent_items.append(item)

    return urgent_items


def main():
    urgent_items = fetch_urgent()
    if not urgent_items:
        print("No urgent Kaufland items today.")
        return

    # vezmeme max 5 nejvyšších
    urgent_items.sort(key=lambda x: x["score"], reverse=True)
    urgent_items = urgent_items[:5]

    lines = []
    lines.append("<h2>⚠️ Kaufland Urgent Monitoring</h2>")
    lines.append(f"<p>{escape(date_de(TIMEZONE))}</p>")
    lines.append("<ul>")

    for it in urgent_items:
        meta = []
        if it.get("source"):
            meta.append(escape(it["source"]))
        if it.get("type"):
            meta.append(escape(it["type"]))
        meta_str = " · ".join(meta)

        lines.append("<li>")
        lines.append(f"<strong><a href='{it['url']}'>{escape(it['title'])}</a></strong><br>")
        if meta_str:
            lines.append(f"<span style='color:#666;font-size:12px;'>{meta_str}</span><br>")
        if it.get("summary"):
            lines.append(f"<span style='font-size:13px;'>{escape(it['summary'])}</span>")
        lines.append("</li>")

    lines.append("</ul>")

    html = "\n".join(lines)
    subject = "⚠️ Kaufland Monitoring Alert"

    send_via_resend(subject, html)


if __name__ == "__main__":
    main()
