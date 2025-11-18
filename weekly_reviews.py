<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8" />
<title>Kaufland Weekly Google Reviews Report</title>
<style>
  body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    margin: 0;
    padding: 0;
  }
  .container {
    max-width: 900px;
    margin: 0 auto;
    background: #ffffff;
    padding: 25px;
    border-radius: 10px;
  }
  h1 {
    color: #E60000;
    margin-bottom: 10px;
  }
  h2 {
    margin-top: 30px;
    margin-bottom: 8px;
    color: #E60000;
  }
  .meta {
    font-size: 12px;
    color: #666;
  }
  .card {
    background: #fafafa;
    padding: 15px;
    border-radius: 8px;
    margin-top: 15px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
    font-size: 14px;
  }
  th, td {
    border: 1px solid #ddd;
    padding: 6px 8px;
  }
  th {
    background: #E60000;
    color: #fff;
    text-align: left;
  }
  .muted {
    color: #888;
    font-size: 13px;
    text-align: center;
    padding: 10px;
  }
</style>
</head>
<body>
<div class="container">

  <h1>Kaufland Weekly Google Reviews Report</h1>
  <p><strong>Zeitraum:</strong> letzte 7 Tage • <strong>Stand:</strong> {date_str} • <strong>Zeitzone:</strong> {tz}</p>
  <p><strong>Empfänger:</strong> {recipient}</p>
  <p><strong>Neue Reviews (Woche gesamt):</strong> {total_new_reviews}</p>

  <h2>Executive Summary</h2>
  <div class="card">
    {summary_html}
  </div>

  <h2>🔻 Größter Rückgang der Ø-Bewertung</h2>
  <table>
    <thead>
      <tr>
        <th>Region</th>
        <th>Filiale</th>
        <th>Ø letzte Woche</th>
        <th>Ø diese Woche</th>
        <th>Δ Ø Bewertung</th>
        <th>Reviews (Vorwoche → Woche)</th>
      </tr>
    </thead>
    <tbody>
      {neg_rows_html}
    </tbody>
  </table>

  <h2>🔺 Größter Anstieg der Ø-Bewertung</h2>
  <table>
    <thead>
      <tr>
        <th>Region</th>
        <th>Filiale</th>
        <th>Ø letzte Woche</th>
        <th>Ø diese Woche</th>
        <th>Δ Ø Bewertung</th>
        <th>Reviews (Vorwoche → Woche)</th>
      </tr>
    </thead>
    <tbody>
      {pos_rows_html}
    </tbody>
  </table>

  <h2>📈 Filialen mit den meisten neuen Reviews</h2>
  <table>
    <thead>
      <tr>
        <th>Region</th>
        <th>Filiale</th>
        <th>Ø letzte Woche</th>
        <th>Ø diese Woche</th>
        <th>Δ Ø Bewertung</th>
        <th>Reviews (Vorwoche → Woche)</th>
      </tr>
    </thead>
    <tbody>
      {vol_rows_html}
    </tbody>
  </table>

  <p class="meta">{threshold_note}</p>

  <p style="margin-top: 30px; font-size: 11px; color:#999;">
    Dieser wöchentliche Bericht basiert auf den aggregierten Google-Reviews-Daten aller Filialen.
  </p>

</div>
</body>
</html>
