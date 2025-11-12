# Kaufland Monitoring (Railway + Gmail SMTP)

## Co to dělá
- Každý den v **07:25 CET** spustí `daily_report.py`,
  - stáhne zprávy (Google News RSS),
  - vybere **Top Headlines (1–10)**,
  - vygeneruje **HTML e-mail (DE)** + přiloží **PDF Full Report**,
  - odešle na `RECIPIENT` přes **Gmail SMTP (App Password)`.
- Každých 10 min běží `urgent_watcher.py` a při nalezení klíčových slov pošle **⚠️ Monitoring Kaufland erwähnt**.

> Pozn.: Railway cron běží v **UTC**. Zápis `25 6 * * *` ≈ **07:25 CET** (zimní čas). V létě (CEST) změň na `25 5 * * *`.

## Rychlé nasazení
1) Vytvoř **App Password** pro účet `kaufland.monitoring@gmail.com`:
   - Zapni 2FA → Security → App Passwords → vytvoř nové (Mail, Other: "Railway").
2) Na Railway založ nový projekt → **Deploy from GitHub** nebo nahraj tento ZIP.
3) V **Variables** nastav:
   - `SENDER_EMAIL` = `kaufland.monitoring@gmail.com`
   - `SMTP_APP_PASSWORD` = *(App Password z kroku 1)*
   - `RECIPIENT` = `stefan.hoppe@kaufland.de`
   - `TIMEZONE` = `Europe/Berlin`
   - `MAX_TOP` = `10`
   - (volitelné) `CC`, `BCC`
   - (volitelné) `INCLUDE_REVIEWS` = `true` + `SERPAPI_KEY` + `PLACES_JSON`
4) V Settings → **Cron** zkontroluj plány (jsou v `railway.json`).
5) **Deploy** a v Console spusť `python daily_report.py` pro test.

## Úpravy šablony
- `email_template.html` – barvy, sekce, texty (Jinja2).

## Rozšíření
- Přidej další RSS/zdroje do `FEEDS`.
- Aktivuj Google Reviews přes SerpAPI (`INCLUDE_REVIEWS=true`).
- Přidej regionální tagování podle klíčových slov (např. "Heilbronn", "Bayern").