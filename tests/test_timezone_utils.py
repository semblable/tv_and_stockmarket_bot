from datetime import datetime, timezone, timedelta

from utils import timezone_utils as tz


def test_utc_now_is_tz_aware_utc():
    now = tz.utc_now()
    assert now.tzinfo is timezone.utc


def test_sqlite_utc_timestamp_formats_in_utc():
    # 12:00 in a +02:00 zone == 10:00 UTC
    dt = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert tz.sqlite_utc_timestamp(dt) == "2026-01-02 10:00:00"


def test_parse_sqlite_utc_timestamp_roundtrip():
    parsed = tz.parse_sqlite_utc_timestamp("2026-06-29 08:30:00")
    assert parsed == datetime(2026, 6, 29, 8, 30, 0, tzinfo=timezone.utc)


def test_parse_sqlite_utc_timestamp_invalid_inputs():
    assert tz.parse_sqlite_utc_timestamp(None) is None
    assert tz.parse_sqlite_utc_timestamp("") is None
    assert tz.parse_sqlite_utc_timestamp("not-a-date") is None


def test_parse_hhmm_valid():
    assert tz.parse_hhmm("18:30") == (18, 30)
    assert tz.parse_hhmm(" 9:05 ") == (9, 5)


def test_parse_hhmm_invalid():
    assert tz.parse_hhmm("24:00") is None
    assert tz.parse_hhmm("12:60") is None
    assert tz.parse_hhmm("abc") is None
    assert tz.parse_hhmm("") is None


def test_tzinfo_from_name_utc_aliases():
    for name in ("UTC", "utc", "Etc/UTC", "Z"):
        assert tz.tzinfo_from_name(name) is timezone.utc


def test_tzinfo_from_name_empty_defaults_to_cet_offset():
    # Whether zoneinfo data is present (Europe/Warsaw) or not (fixed CET),
    # the offset in January (standard time) is +01:00.
    info = tz.tzinfo_from_name("")
    jan = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc).astimezone(info)
    assert jan.utcoffset() == timedelta(hours=1)


def test_tzinfo_from_name_unknown_falls_back_to_utc():
    assert tz.tzinfo_from_name("Totally/Bogus") is timezone.utc
