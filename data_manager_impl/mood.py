import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_UNSET = object()


class MoodMixin:
    """
    Mood tracking (optional).

    Data model goals:
    - Allow multiple mood logs per day.
    - Keep logging lightweight but support qualitative context (note).
    - Avoid "streak" mechanics; this layer is just storage.
    """

    def create_mood_entry(
        self,
        user_id: int,
        mood: int,
        *,
        energy: Optional[int] = None,
        note: Optional[str] = None,
        created_at_utc: Optional[str] = None,
    ) -> Optional[int]:
        """
        Creates a mood entry.

        Args:
            created_at_utc: SQLite UTC timestamp string "YYYY-MM-DD HH:MM:SS" (optional).
                           If omitted, CURRENT_TIMESTAMP is used.
        """
        try:
            m = int(mood)
        except Exception:
            return None
        if not (1 <= m <= 10):
            return None

        e: Optional[int] = None
        if energy is not None:
            try:
                e = int(energy)
            except Exception:
                return None
            if not (1 <= e <= 10):
                return None

        n = (note or "").strip()
        if n:
            # Keep notes reasonably sized so embeds/messages remain readable.
            n = n[:1000]
        else:
            n = None

        if created_at_utc is not None:
            ts = str(created_at_utc).strip()
            if len(ts) < 19:
                return None
            q = """
            INSERT INTO mood_entries (user_id, mood, energy, note, created_at)
            VALUES (:user_id, :mood, :energy, :note, :created_at)
            """
            params = {"user_id": str(int(user_id)), "mood": m, "energy": e, "note": n, "created_at": ts}
        else:
            q = """
            INSERT INTO mood_entries (user_id, mood, energy, note)
            VALUES (:user_id, :mood, :energy, :note)
            """
            params = {"user_id": str(int(user_id)), "mood": m, "energy": e, "note": n}

        ok = self._execute_query(q, params, commit=True)
        if not ok:
            return None
        row = self._execute_query("SELECT last_insert_rowid() AS id;", fetch_one=True)
        try:
            return int((row or {}).get("id"))
        except Exception:
            return None

    def get_mood_entry(self, user_id: int, entry_id: int) -> Optional[Dict[str, Any]]:
        q = """
        SELECT id, user_id, mood, energy, note, created_at
        FROM mood_entries
        WHERE user_id = :user_id AND id = :id
        LIMIT 1
        """
        return self._execute_query(q, {"user_id": str(int(user_id)), "id": int(entry_id)}, fetch_one=True)

    def update_mood_entry(
        self,
        user_id: int,
        entry_id: int,
        *,
        mood: Optional[int] = None,
        energy: object = _UNSET,  # int 1..10, None to clear, _UNSET to keep
        note: object = _UNSET,  # str, None/"" to clear, _UNSET to keep
        created_at_utc: object = _UNSET,  # "YYYY-MM-DD HH:MM:SS" (UTC), _UNSET to keep
    ) -> bool:
        """
        Update fields on a mood entry owned by user.

        Args:
            mood: int 1..10 or None to leave unchanged
            energy: int 1..10, None to leave unchanged, or explicit null via energy=None is ambiguous in Python.
                    To clear energy, pass energy=0 is NOT allowed; instead pass energy="" at command level and map to note=None?
                    This DB method supports clearing energy via energy set to None *only when explicitly passed from caller*
                    using energy=(None) and note parameter to signal "explicit". Caller should use this signature carefully.
            note: string, None to leave unchanged, or "" (empty) to clear
        """
        sets = []
        params: Dict[str, Any] = {"user_id": str(int(user_id)), "id": int(entry_id)}

        # Ownership guard: if the row doesn't exist for this user, don't report success.
        if self.get_mood_entry(int(user_id), int(entry_id)) is None:
            return False

        if mood is not None:
            try:
                m = int(mood)
            except Exception:
                return False
            if not (1 <= m <= 10):
                return False
            sets.append("mood = :mood")
            params["mood"] = m

        if energy is not _UNSET:
            if energy is None:
                sets.append("energy = NULL")
            else:
                try:
                    e = int(energy)
                except Exception:
                    return False
                if not (1 <= e <= 10):
                    return False
                sets.append("energy = :energy")
                params["energy"] = e

        if note is not _UNSET:
            if note is None:
                sets.append("note = NULL")
            else:
                n = str(note).strip()
                if not n:
                    sets.append("note = NULL")
                else:
                    sets.append("note = :note")
                    params["note"] = n[:1000]

        if created_at_utc is not _UNSET:
            if not isinstance(created_at_utc, str):
                return False
            ts = created_at_utc.strip()
            if len(ts) < 19:
                return False
            sets.append("created_at = :created_at")
            params["created_at"] = ts

        if not sets:
            return False

        q = f"""
        UPDATE mood_entries
        SET {", ".join(sets)}
        WHERE user_id = :user_id AND id = :id
        """
        ok = self._execute_query(q, params, commit=True)
        return bool(ok)

    def delete_mood_entry(self, user_id: int, entry_id: int) -> bool:
        if self.get_mood_entry(int(user_id), int(entry_id)) is None:
            return False
        q = "DELETE FROM mood_entries WHERE user_id = :user_id AND id = :id"
        return bool(self._execute_query(q, {"user_id": str(int(user_id)), "id": int(entry_id)}, commit=True))

    def list_mood_entries(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        lim = max(1, min(500, int(limit)))
        q = """
        SELECT id, user_id, mood, energy, note, created_at
        FROM mood_entries
        WHERE user_id = :user_id
        ORDER BY created_at DESC, id DESC
        LIMIT :lim
        """
        return self._execute_query(q, {"user_id": str(int(user_id)), "lim": lim}, fetch_all=True) or []

    def list_mood_entries_between(
        self,
        user_id: int,
        start_utc: str,
        end_utc: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Returns entries in [start_utc, end_utc) where timestamps are UTC SQLite strings.
        """
        lim = max(1, min(2000, int(limit)))
        s = str(start_utc or "").strip()
        e = str(end_utc or "").strip()
        if len(s) < 19 or len(e) < 19:
            return []
        q = """
        SELECT id, user_id, mood, energy, note, created_at
        FROM mood_entries
        WHERE user_id = :user_id
          AND created_at >= :start_utc
          AND created_at < :end_utc
        ORDER BY created_at ASC, id ASC
        LIMIT :lim
        """
        return (
            self._execute_query(
                q,
                {"user_id": str(int(user_id)), "start_utc": s, "end_utc": e, "lim": lim},
                fetch_all=True,
            )
            or []
        )

    def get_first_mood_entry_created_at_utc(self, user_id: int) -> Optional[str]:
        """
        Returns the earliest `created_at` timestamp (UTC SQLite string) for the user's mood entries.

        This is useful for "all-time" reports.
        """
        q = """
        SELECT MIN(created_at) AS created_at
        FROM mood_entries
        WHERE user_id = :user_id
        """
        row = self._execute_query(q, {"user_id": str(int(user_id))}, fetch_one=True)
        v = (row or {}).get("created_at")
        if not isinstance(v, str):
            return None
        s = v.strip()
        return s if len(s) >= 19 else None

