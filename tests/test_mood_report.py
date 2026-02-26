from datetime import date

import pytest

from utils.mood_report import MoodDaySummary, _overall_stats, to_csv_bytes, to_html_report_bytes


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


# ---------------------------------------------------------------------------
# Fix 1: float mood values should not be truncated to int
# ---------------------------------------------------------------------------

def test_avg_mood_preserves_float():
    """8 + 8.76 should average to 8.38, not 8.0 (old int-cast bug)."""
    avg = (8.0 + 8.76) / 2  # 8.38
    day = MoodDaySummary(
        label="2026-02-25",
        start_day=date(2026, 2, 25),
        n=2,
        avg_mood=avg,
        avg_energy=None,
        min_mood=8.0,
        max_mood=8.76,
    )
    assert abs(day.avg_mood - 8.38) < 1e-9

    html_b = to_html_report_bytes(
        title="Float test",
        tz_label="UTC",
        period_label="Test",
        days=[day],
        chart_png_bytes=None,
    )
    assert b"8.38" in html_b


# ---------------------------------------------------------------------------
# Fix 2: overall average must be weighted by entry count, not avg-of-avgs
# ---------------------------------------------------------------------------

def test_overall_stats_weighted_average():
    """
    Day A: 1 entry, mood 5.0
    Day B: 2 entries, avg mood 9.0
    Naive (avg of avgs): (5+9)/2 = 7.0  ← wrong
    Weighted: (5*1 + 9*2) / 3 = 7.667   ← correct
    """
    day_a = MoodDaySummary("2026-02-24", date(2026, 2, 24), 1, 5.0, None, 5.0, 5.0)
    day_b = MoodDaySummary("2026-02-25", date(2026, 2, 25), 2, 9.0, None, 9.0, 9.0)
    stats = _overall_stats([day_a, day_b])

    expected = (5.0 * 1 + 9.0 * 2) / 3  # 7.6667
    assert abs(stats["overall_avg_mood"] - expected) < 1e-9, (
        f"Got {stats['overall_avg_mood']}, expected {expected}"
    )
    assert stats["total_entries"] == 3
    assert stats["days_with_data"] == 2
    assert stats["gaps"] == 0


def test_overall_stats_with_gaps():
    """Gap days must not contribute to average."""
    day_data = MoodDaySummary("2026-02-24", date(2026, 2, 24), 2, 8.38, None, 8.0, 8.76)
    day_gap  = MoodDaySummary("2026-02-25", date(2026, 2, 25), 0, None, None, None, None)
    stats = _overall_stats([day_data, day_gap])

    assert abs(stats["overall_avg_mood"] - 8.38) < 1e-9
    assert stats["total_entries"] == 2
    assert stats["days_with_data"] == 1
    assert stats["gaps"] == 1


# ---------------------------------------------------------------------------
# Fix 3: notes appear in MoodDaySummary and in the calendar hover tooltip
# ---------------------------------------------------------------------------

def test_notes_stored_in_summary():
    day = MoodDaySummary(
        label="2026-02-25",
        start_day=date(2026, 2, 25),
        n=2,
        avg_mood=8.38,
        avg_energy=None,
        min_mood=8.0,
        max_mood=8.76,
        notes=("Had a great morning", "Tired by evening"),
    )
    assert "Had a great morning" in day.notes
    assert "Tired by evening" in day.notes


def test_notes_appear_in_calendar_tooltip():
    day = MoodDaySummary(
        label="2026-02-25",
        start_day=date(2026, 2, 25),
        n=1,
        avg_mood=8.0,
        avg_energy=None,
        min_mood=8.0,
        max_mood=8.0,
        notes=("Felt really good today",),
    )
    html_b = to_html_report_bytes(
        title="Notes test",
        tz_label="UTC",
        period_label="Test",
        days=[day],
        chart_png_bytes=None,
    )
    assert b"Felt really good today" in html_b, "Note text not found in HTML tooltip"


def test_no_notes_field_defaults_to_empty():
    """MoodDaySummary without notes= should default to empty tuple (no crash)."""
    day = MoodDaySummary("2026-02-25", date(2026, 2, 25), 1, 7.0, None, 7.0, 7.0)
    assert day.notes == ()
    # HTML generation must not crash
    html_b = to_html_report_bytes(
        title="No notes test",
        tz_label="UTC",
        period_label="Test",
        days=[day],
        chart_png_bytes=None,
    )
    assert b"No notes test" in html_b
