import base64
import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
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
    sleep_total_min: Optional[int] = None
    sleep_score: Optional[float] = None
    sleep_deep_min: Optional[int] = None
    sleep_light_min: Optional[int] = None
    sleep_rem_min: Optional[int] = None
    sleep_awake_min: Optional[int] = None


def to_csv_bytes(days: Sequence[MoodDaySummary]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "bucket",
            "entries",
            "avg_mood",
            "avg_energy",
            "min_mood",
            "max_mood",
            "sleep_total_min",
            "sleep_score",
            "sleep_deep_min",
            "sleep_light_min",
            "sleep_rem_min",
            "sleep_awake_min",
        ]
    )
    for d in days:
        w.writerow(
            [
                d.label,
                d.n,
                ("" if d.avg_mood is None else f"{d.avg_mood:.2f}"),
                ("" if d.avg_energy is None else f"{d.avg_energy:.2f}"),
                ("" if d.min_mood is None else str(int(d.min_mood))),
                ("" if d.max_mood is None else str(int(d.max_mood))),
                ("" if d.sleep_total_min is None else str(int(d.sleep_total_min))),
                ("" if d.sleep_score is None else f"{float(d.sleep_score):.2f}"),
                ("" if d.sleep_deep_min is None else str(int(d.sleep_deep_min))),
                ("" if d.sleep_light_min is None else str(int(d.sleep_light_min))),
                ("" if d.sleep_rem_min is None else str(int(d.sleep_rem_min))),
                ("" if d.sleep_awake_min is None else str(int(d.sleep_awake_min))),
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
    start = days[0].start_day.isoformat() if days else ""
    end = days[-1].start_day.isoformat() if days else ""
    chart_uri = _b64_png_data_uri(chart_png_bytes)

    def fmt_opt(v: Optional[float]) -> str:
        return "—" if v is None else f"{v:.2f}"

    def is_day_granularity() -> bool:
        # Day-level summaries use ISO date labels like YYYY-MM-DD.
        return bool(days) and all(isinstance(d.label, str) and len(d.label) >= 10 and d.label[4] == "-" for d in days[:3])

    def mood_color(avg_mood: Optional[float], n: int) -> str:
        # Return a CSS color for the calendar cell background.
        if avg_mood is None or int(n or 0) <= 0:
            return "rgba(255,255,255,0.05)"  # gap
        try:
            v = max(1.0, min(10.0, float(avg_mood)))
        except Exception:
            return "rgba(255,255,255,0.05)"
        # Map 1..10 -> red..green hue
        hue = (v - 1.0) / 9.0 * 120.0
        return f"hsl({hue:.0f} 70% 45%)"

    def fmt_sleep_minutes(v: Optional[int]) -> str:
        if v is None:
            return "—"
        try:
            total = max(0, int(v))
        except Exception:
            return "—"
        hh = total // 60
        mm = total % 60
        if hh <= 0:
            return f"{mm}m"
        return f"{hh}h {mm:02d}m"

    def calendar_html() -> str:
        # Only meaningful for day-granularity exports.
        if not days or not is_day_granularity():
            return ""

        by_day = {d.start_day: d for d in days if isinstance(d.start_day, date)}
        start_day = min(by_day.keys()) if by_day else days[0].start_day
        end_day = max(by_day.keys()) if by_day else days[-1].start_day

        # Iterate month-by-month from the start month to the end month.
        cur = date(start_day.year, start_day.month, 1)
        last = date(end_day.year, end_day.month, 1)

        parts: list[str] = []
        parts.append("<div class='calendar'>")
        parts.append("<div class='cal-head'>Calendar</div>")
        parts.append(
            "<div class='cal-legend'>"
            "<span class='lg-label'>low</span>"
            "<span class='lg-swatch' style='background:hsl(0 70% 45%)'></span>"
            "<span class='lg-swatch' style='background:hsl(30 70% 45%)'></span>"
            "<span class='lg-swatch' style='background:hsl(60 70% 45%)'></span>"
            "<span class='lg-swatch' style='background:hsl(90 70% 45%)'></span>"
            "<span class='lg-swatch' style='background:hsl(120 70% 45%)'></span>"
            "<span class='lg-label'>high</span>"
            "<span class='lg-gap'>gap</span>"
            "</div>"
        )

        weekday_hdr = (
            "<div class='cal-weekdays'>"
            "<span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span>"
            "</div>"
        )

        while cur <= last:
            # Next month
            if cur.month == 12:
                next_month = date(cur.year + 1, 1, 1)
            else:
                next_month = date(cur.year, cur.month + 1, 1)
            last_day = next_month - timedelta(days=1)

            parts.append("<div class='cal-month'>")
            parts.append(f"<div class='cal-month-title'>{cur.strftime('%B %Y')}</div>")
            parts.append(weekday_hdr)
            parts.append("<div class='cal-grid'>")

            # Leading blanks (weekday(): Monday=0)
            lead = int(cur.weekday())
            for _ in range(lead):
                parts.append("<div class='cal-cell cal-empty'></div>")

            d = cur
            while d <= last_day:
                s = by_day.get(d)
                n = int(getattr(s, "n", 0) or 0) if s else 0
                avg_m = getattr(s, "avg_mood", None) if s else None
                avg_e = getattr(s, "avg_energy", None) if s else None
                bg = mood_color(avg_m, n)
                title = f"{d.isoformat()}"
                if n > 0 and avg_m is not None:
                    title += f" • avg mood {float(avg_m):.1f}/10 • entries {n}"
                    if avg_e is not None:
                        title += f" • avg energy {float(avg_e):.1f}/10"
                else:
                    title += " • gap"
                parts.append(
                    "<div class='cal-cell' "
                    f"style='background:{bg}' "
                    f"title='{_safe(title)}'>"
                    f"{d.day}"
                    "</div>"
                )
                d = d + timedelta(days=1)

            parts.append("</div>")  # grid
            parts.append("</div>")  # month
            cur = next_month

        parts.append("</div>")  # calendar
        return "\n".join(parts)

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
    .calendar {{
      margin-top: 14px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
    }}
    .cal-head {{ font-size: 14px; color: rgba(255,255,255,0.90); font-weight: 650; }}
    .cal-legend {{
      margin-top: 10px;
      display:flex;
      align-items:center;
      gap: 8px;
      color: var(--muted2);
      font-size: 12px;
      flex-wrap: wrap;
    }}
    .lg-swatch {{
      width: 14px; height: 14px;
      border-radius: 4px;
      border: 1px solid rgba(255,255,255,0.14);
    }}
    .lg-label {{ color: var(--muted); }}
    .lg-gap {{
      margin-left: 10px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.04);
      color: var(--muted);
    }}
    .cal-month {{
      margin-top: 12px;
      border-top: 1px solid rgba(255,255,255,0.08);
      padding-top: 12px;
    }}
    .cal-month:first-of-type {{ border-top: none; padding-top: 0; }}
    .cal-month-title {{
      font-size: 13px;
      color: rgba(255,255,255,0.86);
      margin-bottom: 6px;
    }}
    .cal-weekdays {{
      display:grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 6px;
      font-size: 11px;
      color: var(--muted2);
      margin-bottom: 6px;
    }}
    .cal-grid {{
      display:grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 6px;
    }}
    .cal-cell {{
      aspect-ratio: 1 / 1;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.10);
      font-size: 10px;
      color: rgba(255,255,255,0.92);
      display:flex;
      align-items:center;
      justify-content:center;
      user-select: none;
    }}
    .cal-empty {{
      background: transparent !important;
      border-color: transparent !important;
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
    </div>

    <div class="cards">
      <div class="card"><div class="k">Total entries</div><div class="v">{int(stats["total_entries"])}</div></div>
      <div class="card"><div class="k">Days with data</div><div class="v">{int(stats["days_with_data"])}</div></div>
      <div class="card"><div class="k">Gaps</div><div class="v">{int(stats["gaps"])}</div></div>
      <div class="card"><div class="k">Avg mood (daily avg)</div><div class="v">{fmt_opt(stats["overall_avg_mood"])}</div></div>
    </div>

    <div class="chart">
      {"<img alt='Mood chart' src='" + chart_uri + "'/>" if chart_uri else "<div class='note'>Chart unavailable (could not generate).</div>"}
    </div>

    {calendar_html()}

    <table>
      <thead>
        <tr>
          <th style="width: 22%;">Date (local)</th>
          <th style="width: 10%;">Entries</th>
          <th style="width: 16%;">Avg mood</th>
          <th style="width: 16%;">Avg energy</th>
          <th style="width: 16%;">Min–Max</th>
          <th style="width: 10%;">Sleep</th>
          <th style="width: 10%;">Score</th>
        </tr>
      </thead>
      <tbody>
"""

    for d in days:
        sleep_text = fmt_sleep_minutes(d.sleep_total_min)
        sleep_score = "—" if d.sleep_score is None else f"{float(d.sleep_score):.0f}"
        if d.n <= 0 or d.avg_mood is None:
            html += (
                f"<tr><td>{_safe(d.label)}</td><td class='gap'>0</td>"
                f"<td class='gap'>gap</td><td class='gap'>—</td><td class='gap'>—</td>"
                f"<td>{_safe(sleep_text)}</td><td>{_safe(sleep_score)}</td></tr>\n"
            )
            continue
        avg_m = f"{float(d.avg_mood):.2f}"
        avg_e = "—" if d.avg_energy is None else f"{float(d.avg_energy):.2f}"
        mm = "—" if d.min_mood is None or d.max_mood is None else f"{int(d.min_mood)}–{int(d.max_mood)}"
        html += (
            f"<tr><td>{_safe(d.label)}</td><td>{int(d.n)}</td><td><strong>{avg_m}</strong></td>"
            f"<td>{avg_e}</td><td>{mm}</td><td>{_safe(sleep_text)}</td><td>{_safe(sleep_score)}</td></tr>\n"
        )

    html += """      </tbody>
    </table>

  </div>
</body>
</html>
"""
    return html.encode("utf-8")

