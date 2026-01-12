from datetime import date

from utils.mood_report import MoodDaySummary, to_csv_bytes, to_html_report_bytes


def test_mood_report_csv_and_html_basic():
    days = [
        MoodDaySummary(label="2025-01-01", start_day=date(2025, 1, 1), n=2, avg_mood=6.5, avg_energy=5.0, min_mood=6, max_mood=7),
        MoodDaySummary(label="2025-01-02", start_day=date(2025, 1, 2), n=0, avg_mood=None, avg_energy=None, min_mood=None, max_mood=None),
        MoodDaySummary(label="2025-01-03", start_day=date(2025, 1, 3), n=1, avg_mood=3.0, avg_energy=None, min_mood=3, max_mood=3),
    ]

    csv_b = to_csv_bytes(days)
    assert b"bucket,entries,avg_mood,avg_energy,min_mood,max_mood" in csv_b
    assert b"2025-01-01,2,6.50,5.00,6,7" in csv_b
    assert b"2025-01-02,0" in csv_b

    html_b = to_html_report_bytes(
        title="Mood report — test",
        tz_label="UTC",
        period_label="Test period",
        days=days,
        chart_png_bytes=None,
    )
    # Basic content present
    assert b"Mood report" in html_b
    assert b"Calendar" in html_b
    assert b"2025-01-02" in html_b
    assert b"gap" in html_b

