
import os, feedparser
from helpers import build_email, send_via_gmail, KEYWORDS_URGENT

SENDER = os.getenv('SENDER_EMAIL')
APP_PWD = os.getenv('SMTP_APP_PASSWORD')
RECIPIENT = os.getenv('RECIPIENT')

FEEDS = [
  "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
  "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

def check_and_alert():
  lines = []
  seen = set()
  for url in FEEDS:
    d = feedparser.parse(url)
    for e in d.entries[:10]:
      if e.link in seen: continue
      seen.add(e.link)
      if KEYWORDS_URGENT.search(getattr(e,'title','')):
        lines.append(f"• {e.title}\n{e.link}")
  if not lines:
    return
  body = "Kurzinfo:\n\n" + "\n\n".join(lines) + "\n\n(Automatischer Alarm)"
  msg = build_email(SENDER, RECIPIENT, "⚠️ Monitoring Kaufland erwähnt", f"<pre>{body}</pre>")
  send_via_gmail(msg, SENDER, APP_PWD)

if __name__ == '__main__':
  check_and_alert()
