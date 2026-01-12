import base64
import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class MoodDaySummary:
    label: str  # e.g. "2025-01-07" or "2025-01"
    start_day: date  # local date for ordering
    n: int
    avg_mood: Optional[float]  # 1..10
    avg_energy: Optional[float]  # 1..10
    min_mood: Optional[int]
    max_mood: Optional[int]


def to_csv_bytes(days: Sequence[MoodDaySummary]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["bucket", "entries", "avg_mood", "avg_energy", "min_mood", "max_mood"])
    for d in days:
        w.writerow(
            [
                d.label,
                d.n,
                ("" if d.avg_mood is None else f"{d.avg_mood:.2f}"),
                ("" if d.avg_energy is None else f"{d.avg_energy:.2f}"),
                ("" if d.min_mood is None else str(int(d.min_mood))),
                ("" if d.max_mood is None else str(int(d.max_mood))),
            ]
        )
    return buf.getvalue().encode("utf-8")


def _b64_png_data_uri(png_bytes: Optional[bytes]) -> str:
    if not png_bytes:
        return ""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _safe(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _overall_stats(days: Sequence[MoodDaySummary]) -> dict:
    total_entries = sum(int(d.n) for d in days)
    days_with_data = sum(1 for d in days if (d.n or 0) > 0)
    gaps = len(days) - days_with_data
    moods: list[float] = []
    energies: list[float] = []
    for d in days:
        if d.avg_mood is not None and d.n > 0:
            moods.append(float(d.avg_mood))
        if d.avg_energy is not None and d.n > 0:
            energies.append(float(d.avg_energy))

    overall_avg_mood = (sum(moods) / len(moods)) if moods else None
    overall_avg_energy = (sum(energies) / len(energies)) if energies else None
    return {
        "total_entries": total_entries,
        "days_with_data": days_with_data,
        "gaps": gaps,
        "overall_avg_mood": overall_avg_mood,
        "overall_avg_energy": overall_avg_energy,
    }


def to_html_report_bytes(
    *,
    title: str,
    tz_label: str,
    period_label: str,
    days: Sequence[MoodDaySummary],
    chart_png_bytes: Optional[bytes] = None,
) -> bytes:
    stats = _overall_stats(days)
    start = days[0].label if days else ""
    end = days[-1].label if days else ""
    chart_uri = _b64_png_data_uri(chart_png_bytes)

    def fmt_opt(v: Optional[float]) -> str:
        return "—" if v is None else f"{v:.2f}"

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{_safe(title)}</title>
  <style>
    :root {{
      --bg: #0b1020;
      --panel: rgba(255,255,255,0.06);
      --panel2: rgba(255,255,255,0.08);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.70);
      --muted2: rgba(255,255,255,0.55);
      --border: rgba(255,255,255,0.12);
      --accent: #7c3aed;
      --good: #10b981;
    }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, Helvetica Neue, Arial;
      background: radial-gradient(1200px 800px at 20% 10%, rgba(124,58,237,0.25), transparent 60%),
                  radial-gradient(1200px 800px at 80% 0%, rgba(16,185,129,0.18), transparent 55%),
                  var(--bg);
      color: var(--text);
    }}
    .wrap {{ max-width: 980px; margin: 32px auto; padding: 0 18px; }}
    .header {{ display:flex; gap:14px; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; }}
    .title h1 {{ margin:0; font-size: 24px; letter-spacing: 0.2px; }}
    .title .meta {{ margin-top:6px; color: var(--muted); font-size: 13px; }}
    .pill {{
      background: rgba(124,58,237,0.18);
      border: 1px solid rgba(124,58,237,0.35);
      color: rgba(255,255,255,0.90);
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      align-self: flex-start;
    }}
    .cards {{ display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }}
    @media (max-width: 860px) {{ .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 14px;
    }}
    .card .k {{ color: var(--muted2); font-size: 12px; }}
    .card .v {{ margin-top: 8px; font-size: 18px; font-weight: 650; }}
    .chart {{
      margin-top: 14px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
      overflow: hidden;
    }}
    .chart img {{ width: 100%; height: auto; display:block; border-radius: 10px; }}
    .note {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
    }}
    thead th {{
      text-align:left;
      font-size: 12px;
      color: var(--muted);
      background: rgba(255,255,255,0.06);
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
    }}
    tbody td {{
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      font-size: 13px;
      color: rgba(255,255,255,0.88);
    }}
    tbody tr:last-child td {{ border-bottom: none; }}
    .gap {{ color: var(--muted2); }}
    .badge {{
      display:inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.06);
      color: rgba(255,255,255,0.82);
    }}
    .footer {{
      margin-top: 16px;
      color: var(--muted2);
      font-size: 12px;
    }}
    .footer code {{
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.10);
      padding: 1px 6px;
      border-radius: 8px;
      color: rgba(255,255,255,0.85);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="title">
        <h1>{_safe(title)}</h1>
        <div class="meta">
          <span class="badge">{_safe(period_label)}</span>
          &nbsp;·&nbsp; range <strong>{_safe(start)}</strong> → <strong>{_safe(end)}</strong>
          &nbsp;·&nbsp; timezone <strong>{_safe(tz_label)}</strong>
        </div>
      </div>
      <div class="pill">gaps are neutral</div>
    </div>

    <div class="cards">
      <div class="card"><div class="k">Total entries</div><div class="v">{int(stats["total_entries"])}</div></div>
      <div class="card"><div class="k">Days with data</div><div class="v">{int(stats["days_with_data"])}</div></div>
      <div class="card"><div class="k">Gaps</div><div class="v">{int(stats["gaps"])}</div></div>
      <div class="card"><div class="k">Avg mood (daily avg)</div><div class="v">{fmt_opt(stats["overall_avg_mood"])}</div></div>
    </div>

    <div class="chart">
      {"<img alt='Mood chart' src='" + chart_uri + "'/>" if chart_uri else "<div class='note'>Chart unavailable (could not generate).</div>"}
      <div class="note">
        This is descriptive, not evaluative. Missed days are shown as <em>gaps</em>—not failures.
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 28%;">Date (local)</th>
          <th style="width: 12%;">Entries</th>
          <th style="width: 20%;">Avg mood</th>
          <th style="width: 20%;">Avg energy</th>
          <th style="width: 20%;">Min–Max</th>
        </tr>
      </thead>
      <tbody>
"""

    for d in days:
        if d.n <= 0 or d.avg_mood is None:
            html += f"<tr><td>{_safe(d.label)}</td><td class='gap'>0</td><td class='gap'>gap</td><td class='gap'>—</td><td class='gap'>—</td></tr>\n"
            continue
        avg_m = f"{float(d.avg_mood):.2f}"
        avg_e = "—" if d.avg_energy is None else f"{float(d.avg_energy):.2f}"
        mm = "—" if d.min_mood is None or d.max_mood is None else f"{int(d.min_mood)}–{int(d.max_mood)}"
        html += f"<tr><td>{_safe(d.label)}</td><td>{int(d.n)}</td><td><strong>{avg_m}</strong></td><td>{avg_e}</td><td>{mm}</td></tr>\n"

    html += """      </tbody>
    </table>

    <div class="footer">
      Export tip: this report is meant for reflection, not perfection.
      You can keep raw data in the CSV and review trends periodically.
    </div>
  </div>
</body>
</html>
"""
    return html.encode("utf-8")

