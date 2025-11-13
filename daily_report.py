import os, json, feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from html import escape

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm

from helpers import date_de, classify, send_via_resend

# ===== Config from env =====
TIMEZONE   = os.getenv('TIMEZONE', 'Europe/Berlin')
RECIPIENT  = os.getenv('RECIPIENT')
SENDER     = os.getenv('SENDER_EMAIL')
APP_PWD    = os.getenv('SMTP_APP_PASSWORD')
CC         = os.getenv('CC')
BCC        = os.getenv('BCC')
MAX_TOP    = int(os.getenv('MAX_TOP', '10'))
REGIONS_JSON = os.getenv('REGIONS_JSON', '{}')   # pro budoucí využití

# ===== News feeds =====
FEEDS = [
    "https://news.google.com/rss/search?q=Kaufland+Deutschland&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Filiale&hl=de&gl=DE&ceid=DE:de",
    "https://news.google.com/rss/search?q=Kaufland+Skandal+OR+R%C3%BCckruf+OR+Boykott&hl=de&gl=DE&ceid=DE:de",
]

# ---------- helpers ----------

def fetch_news():
    """Stáhne a seřadí zprávy podle skóre z classify()."""
    seen, items = set(), []
    for url in FEEDS:
        d = feedparser.parse(url)
        for e in d.entries:
            link = e.link
            if link in seen:
                continue
            seen.add(link)
            title = e.title
            desc = BeautifulSoup(getattr(e, 'summary', ''), 'html.parser').get_text()
            host, typ, score = classify(link, title)
            items.append({
                'title': title,
                'url': link,
                'summary': (desc[:260] + '…') if len(desc) > 260 else desc,
                'source': host,
                'type': typ,
                'score': score,
                'why': 'relevant' if score >= 4 else 'beobachten'
            })
    items.sort(key=lambda x: x['score'], reverse=True)
    return items


def build_pdf(filename, items, intl, reviews):
    """Jednoduchý tabulkový PDF export."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        filename, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=16*mm, bottomMargin=16*mm
    )
    story = []
    story.append(Paragraph(f"Kaufland Full Report – {date_de(TIMEZONE)}", styles['Title']))
    story.append(Spacer(1, 8))

    data = [["Titel", "Quelle", "Typ", "Kurzfassung", "Link"]]
    for i in items:
        data.append([i['title'], i['source'], i['type'], i['summary'], i['url']])
    for x in intl:
        data.append([x['title'], x['source'], 'international', x['summary'], x['url']])

    tbl = Table(data, colWidths=[60*mm, 30*mm, 25*mm, 55*mm, 30*mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), '#E60000'),
        ('TEXTCOLOR',  (0,0), (-1,0), 'white'),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,0), 10),
        ('GRID',       (0,0), (-1,-1), 0.25, 'grey'),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
        ('FONTSIZE',   (0,1), (-1,-1), 9),
    ]))
    story.append(tbl)
    doc.build(story)


def render_template_safe(template_str: str, **fields) -> str:
    """
    Bezpečně nahradí placeholdery v HTML šabloně, která obsahuje i CSS s { }.
    Všechny { } se nejprve escapnou na {{ }}, poté se naše klíče vrátí zpět.
    """
    escaped = template_str.replace('{', '{{').replace('}', '}}')
    for key in fields.keys():
        escaped = escaped.replace('{{' + key + '}}', '{' + key + '}')
    return escaped.format(**fields)


# ---------- main ----------

def main():
    items = fetch_news()
    top = items[:MAX_TOP]
    intl = [it for it in items if not it.get('source','').endswith('.de')][:5]
    reviews = []  # místo pro SerpAPI/Google Reviews (volitelně)

    # --- připrav emailové bloky ---
    template_str = open('email_template.html', 'r', encoding='utf-8').read()

    executive_summary_html = """
    <p><strong>Insight:</strong> Kurátorsky vybrané Top-Schlagzeilen (1–10); další zmínky níže.</p>
    <p><strong>Implikation:</strong> Přehled v jednom e-mailu; regionální rozdíly lze rychle dohledat.</p>
    <p><strong>Aktion:</strong> Sledujeme Google Reviews a posíláme ⚠️ alerty při anomáliích.</p>
    """.strip()

    top_items_html = [
        f'''<li class="item">
              <div class="rank">{i+1}</div>
              <div>
                <a href="{it['url']}">{escape(it['title'])}</a>
                <div class="meta">{escape(it.get('source',''))}{' · ' + escape(it.get('when','')) if it.get('when') else ''}{' · Grund: ' + escape(it.get('why','')) if it.get('why') else ''}</div>
              </div>
            </li>'''
        for i, it in enumerate(top)
    ]

    review_rows = [
        f'''<tr>
              <td>{escape(r.get('region','–'))} – {escape(r.get('store','–'))}</td>
              <td>{r.get('avg','–')}</td>
              <td class="{ 'pos' if r.get('delta',0)>=0 else 'neg' }">{(r.get('delta') if r.get('delta') is not None else '–')}</td>
              <td>{r.get('count_24h','–')}</td>
              <td>{escape(r.get('flag','–'))}</td>
            </tr>'''
        for r in (reviews or [])
    ] or ["""<tr><td colspan="5" class="muted">Keine auffälligen 24h-Veränderungen (Pilotmodus). Aktivierbar via SerpAPI.</td></tr>"""]

    urgent_block_html = ""   # později můžeš napojit na urgent_watcher.py
    rumors_block_html = ""   # volitelná sekce pro virální/bulvární zmínky

    international_items_html = [
        f'''<li class="item">
              <div class="rank">•</div>
              <div>
                <a href="{it['url']}">{escape(it['title'])}</a>
                <div class="meta">{escape(it.get('source',''))}{' · ' + escape(it.get('type','')) if it.get('type') else ''}</div>
              </div>
            </li>'''
        for it in (intl or [])
    ]

    # --- bezpečné složení výsledného HTML ---
    html = render_template_safe(
        template_str,
        date_str=date_de(TIMEZONE),
        tz=TIMEZONE,
        recipient=RECIPIENT,
        executive_summary_html=executive_summary_html,
        top_count=len(top),
        top_headlines_html="\n".join(top_items_html),
        reviews_table_rows_html="\n".join(review_rows),
        reviews_note="Δ = Veränderung der Ø-Bewertung in den letzten 24 Stunden.",
        urgent_block_html=urgent_block_html,
        rumors_block_html=rumors_block_html,
        international_html="\n".join(international_items_html)
    )

    # --- PDF + e-mail ---
   pdf_name = f"full_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    build_pdf(pdf_name, items, intl, reviews)

    subject = f"📰 Kaufland Media & Review Briefing | {date_de(TIMEZONE)}"

    # Poslat e-mail přes Resend (HTML + PDF jako příloha)
    send_via_resend(
        RECIPIENT,
        subject,
        html,
        attachment_path=pdf_name,
    )
if __name__ == '__main__':
    main()
