import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RemindersMixin:
    """
    Generic, user-created reminders (one-off and repeating).

    Timestamps are stored as SQLite UTC strings: "YYYY-MM-DD HH:MM:SS".
    """

    def create_reminder(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        message: str,
        trigger_at_utc: str,
        repeat_interval_seconds: Optional[int] = None,
    ) -> Optional[int]:
        msg = (message or "").strip()
        if not msg:
            return None
        if not isinstance(trigger_at_utc, str) or len(trigger_at_utc.strip()) < 19:
            return None
        # Safety: repeating reminders can get spammy. Enforce a minimum interval.
        MIN_REPEAT_INTERVAL_SECONDS = 30 * 60

        rep = None
        if repeat_interval_seconds is not None:
            try:
                rep = int(repeat_interval_seconds)
            except Exception:
                rep = None
            if rep is not None and rep <= 0:
                rep = None
            if rep is not None and rep < MIN_REPEAT_INTERVAL_SECONDS:
                rep = MIN_REPEAT_INTERVAL_SECONDS

        q = """
        INSERT INTO reminders (guild_id, channel_id, user_id, message, trigger_at, repeat_interval_seconds, repeat_count, is_active)
        VALUES (:guild_id, :channel_id, :user_id, :message, :trigger_at, :repeat_interval_seconds, 0, 1)
        """
        params = {
            "guild_id": str(int(guild_id or 0)),
            "channel_id": str(int(channel_id or 0)),
            "user_id": str(int(user_id)),
            "message": msg,
            "trigger_at": trigger_at_utc.strip(),
            "repeat_interval_seconds": rep,
        }
        ok = self._execute_query(q, params, commit=True)
        if not ok:
            return None
        row = self._execute_query("SELECT last_insert_rowid() AS id;", fetch_one=True)
        try:
            return int((row or {}).get("id"))
        except Exception:
            return None

    def list_due_reminders(self, now_utc: str, limit: int = 50) -> List[Dict[str, Any]]:
        lim = max(1, min(500, int(limit)))
        q = """
        SELECT id, guild_id, channel_id, user_id, message, trigger_at, repeat_interval_seconds, repeat_count
        FROM reminders
        WHERE is_active = 1
          AND trigger_at IS NOT NULL
          AND trigger_at <= :now_utc
        ORDER BY trigger_at ASC
        LIMIT :lim
        """
        return self._execute_query(q, {"now_utc": now_utc, "lim": lim}, fetch_all=True) or []

    def list_user_reminders(self, user_id: int, include_inactive: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
        lim = max(1, min(200, int(limit)))
        q = """
        SELECT id, guild_id, channel_id, user_id, message, trigger_at, repeat_interval_seconds, repeat_count, is_active, created_at
        FROM reminders
        WHERE user_id = :user_id
        """
        params = {"user_id": str(int(user_id))}
        if not include_inactive:
            q += " AND is_active = 1"
        q += " ORDER BY trigger_at ASC, id ASC LIMIT :lim"
        params["lim"] = lim
        return self._execute_query(q, params, fetch_all=True) or []

    def deactivate_reminder(self, user_id: int, reminder_id: int) -> bool:
        q = """
        UPDATE reminders
        SET is_active = 0
        WHERE id = :id AND user_id = :user_id
        """
        return bool(self._execute_query(q, {"id": int(reminder_id), "user_id": str(int(user_id))}, commit=True))

    def bump_reminder_after_send(self, reminder_id: int, *, next_trigger_at_utc: str) -> bool:
        """
        After sending a reminder, update trigger_at for repeating reminders and increment repeat_count.
        """
        if not isinstance(next_trigger_at_utc, str) or len(next_trigger_at_utc.strip()) < 19:
            return False
        q = """
        UPDATE reminders
        SET trigger_at = :trigger_at,
            repeat_count = COALESCE(repeat_count, 0) + 1
        WHERE id = :id AND is_active = 1
        """
        return bool(self._execute_query(q, {"id": int(reminder_id), "trigger_at": next_trigger_at_utc.strip()}, commit=True))

    def snooze_reminder(self, reminder_id: int, *, next_trigger_at_utc: str) -> bool:
        """
        Move a reminder's trigger time forward WITHOUT counting it as a repeat send.

        This is used for throttling (e.g., user-level 30min cooldown) and best-effort DND backoff,
        so we don't spin and/or spam when multiple reminders are due at once.
        """
        if not isinstance(next_trigger_at_utc, str) or len(next_trigger_at_utc.strip()) < 19:
            return False
        q = """
        UPDATE reminders
        SET trigger_at = :trigger_at
        WHERE id = :id AND is_active = 1
        """
        return bool(self._execute_query(q, {"id": int(reminder_id), "trigger_at": next_trigger_at_utc.strip()}, commit=True))

    def complete_oneoff_reminder(self, reminder_id: int) -> bool:
        q = "UPDATE reminders SET is_active = 0 WHERE id = :id"
        return bool(self._execute_query(q, {"id": int(reminder_id)}, commit=True))







