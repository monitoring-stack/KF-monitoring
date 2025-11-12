
import os, re, json, feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil import tz
from jinja2 import Template
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from helpers import date_de, classify, build_email, send_via_gmail

TIMEZONE = os.getenv('TIMEZONE','Europe/Berlin')
RECIPIENT = os.getenv('RECIPIENT')
SENDER = os.getenv('SENDER_EMAIL')
APP_PWD = os.getenv('SMTP_APP_PASSWORD')
CC = os.getenv('CC')
BCC = os.getenv('BCC')
MAX_TOP = int(os.getenv('MAX_TOP','10'))
INCLUDE_REVIEWS = os.getenv('INCLUDE_REVIEWS','false').lower() == 'true'
SERPAPI_KEY = os.getenv('SERPAPI_KEY')
PLACES_JSON = os.getenv('PLACES_JSON','[]')
REGIONS_JSON = os.getenv('REGIONS_JSON','{}')

FEEDS = [
  "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
  "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
  "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

def fetch_news():
    seen = set()
    items = []
    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen: continue
            seen.add(link)
            title = e.title
            desc = BeautifulSoup(getattr(e,'summary',''), 'html.parser').get_text()
            host, typ, score = classify(link, title)
            items.append({
                'title': title,
                'url': link,
                'summary': (desc[:260] + '…') if len(desc)>260 else desc,
                'source': host,
                'type': typ,
                'score': score,
                'why': 'relevant' if score>=4 else 'beobachten'
            })
    items.sort(key=lambda x: x['score'], reverse=True)
    return items


def bucket_by_region(items, regions_json):
    try:
        regions = json.loads(regions_json) if regions_json else {}
    except Exception:
        regions = {}
    buckets = {k: [] for k in regions.keys()}
    buckets["Sonstiges"] = []
    for it in items:
        text = f"{it['title']} {it.get('summary','')} {it.get('url','')}".lower()
        assigned = False
        for region, keywords in regions.items():
            for kw in keywords:
                if kw.lower() in text:
                    buckets[region].append(it)
                    assigned = True
                    break
            if assigned: break
        if not assigned:
            buckets["Sonstiges"].append(it)
    # remove empty regions except Sonstiges if empty
    return {k:v for k,v in buckets.items() if v}


def build_pdf(filename, items, intl, reviews):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(filename, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm)
    story = []
    story.append(Paragraph(f"Kaufland Full Report – {date_de(TIMEZONE)}", styles['Title']))
    story.append(Spacer(1, 8))

    data = [["Titel","Quelle","Typ","Kurzfassung","Link"]]
    for i in items:
        data.append([i['title'], i['source'], i['type'], i['summary'], i['url']])
    for x in intl:
        data.append([x['title'], x['source'], 'international', x['summary'], x['url']])

    tbl = Table(data, colWidths=[60*mm, 30*mm, 25*mm, 55*mm, 30*mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0), '#E60000'),
        ('TEXTCOLOR',(0,0),(-1,0), 'white'),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTSIZE',(0,0),(-1,0),10),
        ('GRID',(0,0),(-1,-1), 0.25, 'grey'),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('FONTSIZE',(0,1),(-1,-1),9),
    ]))
    story.append(tbl)
    doc.build(story)

def main():
    items = fetch_news()
    top = items[:MAX_TOP]
    others = items[MAX_TOP:50]

    intl = [it for it in items if not it['source'].endswith('.de')][:5]

    reviews = []  # optional later via SerpAPI

    template_str = open('email_template.html','r',encoding='utf-8').read()
    from jinja2 import Template
    regional = bucket_by_region(others, REGIONS_JSON)
    html = Template(template_str).render(
        date_de=date_de(TIMEZONE),
        recipient=RECIPIENT,
        executive_summary=[
            "Top-Schlagzeilen kuratiert (1–10), weitere Erwähnungen unten.",
            "Google Reviews: Pilotbetrieb/optional aktivierbar.",
            "Sensations & Narratives separat gekennzeichnet."
        ],
        top_headlines=top,
        other_items=others,
        regional_buckets=regional,
        reviews=reviews,
        sensations=[
            "Boulevard-/virale Themen werden transparent gelistet (Kontext: Stimmung, nicht Faktentreue)."
        ],
        intl_items=intl,
        full_report_filename=f"full_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    )

    pdf_name = f"full_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, items, intl, reviews)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"
    msg = build_email(SENDER, RECIPIENT, subject, html, attachments=[pdf_name], cc=CC, bcc=BCC)
    send_via_gmail(msg, SENDER, APP_PWD)

if __name__ == '__main__':
    main()
