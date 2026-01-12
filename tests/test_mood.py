import pytest


def test_mood_entries_crud_basic(db_manager):
    user_id = 424242

    # Not enabled by default (preference-based)
    assert db_manager.get_user_preference(user_id, "mood_enabled", False) is False

    # Enable
    assert db_manager.set_user_preference(user_id, "mood_enabled", True) is True
    assert db_manager.get_user_preference(user_id, "mood_enabled", False) is True

    # Create an entry
    eid = db_manager.create_mood_entry(user_id, 7, energy=6, note="felt okay", created_at_utc="2025-01-01 10:00:00")
    assert isinstance(eid, int)

    # List latest
    rows = db_manager.list_mood_entries(user_id, 10)
    assert isinstance(rows, list)
    assert any(int(r.get("id") or 0) == eid for r in rows)

    # Range query
    day_rows = db_manager.list_mood_entries_between(user_id, "2025-01-01 00:00:00", "2025-01-02 00:00:00", 100)
    assert len(day_rows) == 1
    assert int(day_rows[0].get("mood") or 0) == 7
    assert int(day_rows[0].get("energy") or 0) == 6

    # Update mood + clear note
    ok = db_manager.update_mood_entry(user_id, eid, mood=8, note=None)
    assert ok is True
    row2 = db_manager.get_mood_entry(user_id, eid)
    assert row2 is not None
    assert int(row2.get("mood") or 0) == 8
    assert row2.get("note") is None

    # Ownership guard: other user cannot update/delete
    ok2 = db_manager.update_mood_entry(999, eid, mood=2)
    assert ok2 is False
    # It must not change the real row for owner.
    row3 = db_manager.get_mood_entry(user_id, eid)
    assert row3 is not None
    assert int(row3.get("mood") or 0) == 8

    # Delete works only for owner
    assert db_manager.delete_mood_entry(user_id, eid) is True
    assert db_manager.get_mood_entry(user_id, eid) is None

    # Other user cannot delete (already deleted => should still be False)
    assert db_manager.delete_mood_entry(999, eid) is False


@pytest.mark.parametrize("mood", [0, 11, -1, 999])
def test_mood_entries_reject_out_of_range_mood(db_manager, mood):
    user_id = 1
    eid = db_manager.create_mood_entry(user_id, mood, created_at_utc="2025-01-01 10:00:00")
    assert eid is None


@pytest.mark.parametrize("energy", [0, 11, -3, 999])
def test_mood_entries_reject_out_of_range_energy(db_manager, energy):
    user_id = 2
    eid = db_manager.create_mood_entry(user_id, 5, energy=energy, created_at_utc="2025-01-01 10:00:00")
    assert eid is None

