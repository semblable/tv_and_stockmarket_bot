import sqlite3
import logging
import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ProductivityMixin:
    def create_todo_item(self, guild_id: int, user_id: int, content: str) -> Optional[int]:
        if not content or not str(content).strip():
            return None
        query = """
        INSERT INTO todo_items (guild_id, user_id, content, is_done, remind_enabled, remind_level, next_remind_at)
        VALUES (:guild_id, :user_id, :content, 0, 0, 0, NULL)
        """
        params = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "content": str(content).strip()}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"create_todo_item failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return None
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def list_todo_items(self, guild_id: int, user_id: int, include_done: bool = False, limit: int = 30) -> List[Dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        base = """
        SELECT id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at
        FROM todo_items
        WHERE guild_id = :guild_id AND user_id = :user_id
        """
        params: Dict[str, Any] = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "limit": limit}
        if not include_done:
            base += " AND is_done = 0"
        base += " ORDER BY is_done ASC, id DESC LIMIT :limit"
        rows = self._execute_query(base, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def get_todo_item_any_scope(self, user_id: int, todo_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch a todo item by (user_id, id) regardless of guild scope.
        Useful for DM flows where ctx.guild is None but the item was created in a server.
        """
        query = """
        SELECT id, guild_id, user_id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at
        FROM todo_items
        WHERE user_id = :user_id AND id = :id
        """
        row = self._execute_query(query, {"user_id": str(int(user_id)), "id": int(todo_id)}, fetch_one=True)
        return row if isinstance(row, dict) else None

    def set_todo_done_any_scope(self, user_id: int, todo_id: int, done: bool) -> bool:
        """
        Mark a todo as done/undone by (user_id, id) regardless of guild scope.
        """
        query = """
        UPDATE todo_items
        SET is_done = :done,
            done_at = CASE WHEN :done = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
            remind_enabled = CASE WHEN :done = 1 THEN 0 ELSE remind_enabled END,
            remind_level = CASE WHEN :done = 1 THEN 0 ELSE remind_level END,
            next_remind_at = CASE WHEN :done = 1 THEN NULL ELSE next_remind_at END
        WHERE user_id = :user_id AND id = :id
        """
        params = {"done": 1 if done else 0, "user_id": str(int(user_id)), "id": int(todo_id)}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_todo_done_any_scope failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def delete_todo_item_any_scope(self, user_id: int, todo_id: int) -> bool:
        """
        Delete a todo by (user_id, id) regardless of guild scope.
        """
        query = "DELETE FROM todo_items WHERE user_id = :user_id AND id = :id"
        params = {"user_id": str(int(user_id)), "id": int(todo_id)}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_todo_item_any_scope failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_todo_reminder_any_scope(self, user_id: int, todo_id: int, enabled: bool, next_remind_at_utc: Optional[str]) -> bool:
        """
        Enable/disable reminders by (user_id, id) regardless of guild scope.
        """
        query = """
        UPDATE todo_items
        SET remind_enabled = :enabled,
            remind_level = CASE WHEN :enabled = 1 THEN remind_level ELSE 0 END,
            next_remind_at = :next_remind_at
        WHERE user_id = :user_id AND id = :id AND is_done = 0
        """
        params = {
            "enabled": 1 if enabled else 0,
            "next_remind_at": next_remind_at_utc,
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_todo_reminder_any_scope failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_todo_done(self, guild_id: int, user_id: int, todo_id: int, done: bool) -> bool:
        query = """
        UPDATE todo_items
        SET is_done = :done,
            done_at = CASE WHEN :done = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
            remind_enabled = CASE WHEN :done = 1 THEN 0 ELSE remind_enabled END,
            remind_level = CASE WHEN :done = 1 THEN 0 ELSE remind_level END,
            next_remind_at = CASE WHEN :done = 1 THEN NULL ELSE next_remind_at END
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        params = {
            "done": 1 if done else 0,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_todo_done failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def delete_todo_item(self, guild_id: int, user_id: int, todo_id: int) -> bool:
        query = "DELETE FROM todo_items WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id"
        params = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(todo_id)}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_todo_item failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_todo_reminder(self, guild_id: int, user_id: int, todo_id: int, enabled: bool, next_remind_at_utc: Optional[str]) -> bool:
        query = """
        UPDATE todo_items
        SET remind_enabled = :enabled,
            remind_level = CASE WHEN :enabled = 1 THEN remind_level ELSE 0 END,
            next_remind_at = :next_remind_at
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id AND is_done = 0
        """
        params = {
            "enabled": 1 if enabled else 0,
            "next_remind_at": next_remind_at_utc,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_todo_reminder failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def bump_todo_reminder(self, guild_id: int, user_id: int, todo_id: int, remind_level: int, next_remind_at_utc: str) -> bool:
        query = """
        UPDATE todo_items
        SET remind_level = :level,
            next_remind_at = :next_remind_at
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id AND is_done = 0 AND remind_enabled = 1
        """
        params = {
            "level": int(remind_level),
            "next_remind_at": next_remind_at_utc,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        return bool(self._execute_query(query, params, commit=True))

    def list_due_todo_reminders(self, now_utc: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        query = """
        SELECT id, guild_id, user_id, content, remind_level, next_remind_at
        FROM todo_items
        WHERE is_done = 0
          AND remind_enabled = 1
          AND next_remind_at IS NOT NULL
          AND next_remind_at <= :now
        ORDER BY next_remind_at ASC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"now": now_utc, "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    # -------------------------
    # Productivity: Habits
    # -------------------------
    def create_habit(
        self,
        guild_id: int,
        user_id: int,
        name: str,
        days_of_week: List[int],
        due_time_local: str,
        tz_name: str = "Europe/Warsaw",
        remind_enabled: bool = True,
        next_due_at_utc: Optional[str] = None,
    ) -> Optional[int]:
        if not name or not str(name).strip():
            return None
        try:
            days_json = json.dumps([int(d) for d in days_of_week])
        except Exception:
            days_json = json.dumps([0, 1, 2, 3, 4])
        query = """
        INSERT INTO habits (guild_id, user_id, name, days_of_week, due_time_local, tz_name, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at)
        VALUES (:guild_id, :user_id, :name, :days, :due_time_local, :tz_name, :due_time_utc, :remind_enabled, 0, :next_due_at, NULL)
        """
        params = {
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "name": str(name).strip(),
            "days": days_json,
            "due_time_local": str(due_time_local).strip(),
            "tz_name": str(tz_name or "Europe/Warsaw").strip(),
            # legacy column maintained for backwards compatibility; we store the same HH:MM
            "due_time_utc": str(due_time_local).strip(),
            "remind_enabled": 1 if remind_enabled else 0,
            "next_due_at": next_due_at_utc,
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"create_habit failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return None
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def list_habits(self, guild_id: int, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        query = """
        SELECT id, name, days_of_week, due_time_local, tz_name, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE guild_id = :guild_id AND user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def get_habit(self, guild_id: int, user_id: int, habit_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT id, name, days_of_week, due_time_local, tz_name, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        row = self._execute_query(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)}, fetch_one=True)
        return row if isinstance(row, dict) else None

    def get_habit_any_scope(self, user_id: int, habit_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch a habit by (user_id, id) regardless of guild scope.
        Useful for DM flows where the habit was created in a server.
        """
        query = """
        SELECT id, guild_id, name, days_of_week, due_time_local, tz_name, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE user_id = :user_id AND id = :id
        """
        row = self._execute_query(query, {"user_id": str(int(user_id)), "id": int(habit_id)}, fetch_one=True)
        return row if isinstance(row, dict) else None

    def delete_habit_any_scope(self, user_id: int, habit_id: int) -> bool:
        """
        Delete a habit by (user_id, id) regardless of guild scope.
        """
        query = "DELETE FROM habits WHERE user_id = :user_id AND id = :id"
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, {"user_id": str(int(user_id)), "id": int(habit_id)})
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_habit_any_scope failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_habit_reminder_enabled_any_scope(self, user_id: int, habit_id: int, enabled: bool) -> bool:
        """
        Enable/disable reminders by (user_id, id) regardless of guild scope.
        When disabling, clears next_remind_at and resets remind_level.
        """
        query = """
        UPDATE habits
        SET remind_enabled = :enabled,
            remind_level = CASE WHEN :enabled = 1 THEN remind_level ELSE 0 END,
            next_remind_at = CASE WHEN :enabled = 1 THEN next_remind_at ELSE NULL END
        WHERE user_id = :user_id AND id = :id
        """
        params = {"enabled": 1 if enabled else 0, "user_id": str(int(user_id)), "id": int(habit_id)}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_habit_reminder_enabled_any_scope failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def record_habit_checkin_any_scope(self, user_id: int, habit_id: int, note: Optional[str] = None, next_due_at_utc: Optional[str] = None) -> bool:
        """
        Record a check-in by (user_id, id) regardless of guild scope.
        Internally resolves the habit's guild_id to keep history rows consistent.
        """
        habit = self.get_habit_any_scope(user_id, habit_id)
        if not habit:
            return False
        try:
            guild_id = int(habit.get("guild_id") or 0)
        except Exception:
            guild_id = 0
        return self.record_habit_checkin(guild_id, user_id, habit_id, note, next_due_at_utc)

    def delete_habit(self, guild_id: int, user_id: int, habit_id: int) -> bool:
        query = "DELETE FROM habits WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id"
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)})
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_habit failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_habit_schedule_and_due(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        *,
        days_of_week: Optional[List[int]] = None,
        due_time_local: Optional[str] = None,
        tz_name: Optional[str] = None,
        next_due_at_utc: Optional[str] = None,
        remind_enabled: Optional[bool] = None,
        next_remind_at_utc: Optional[str] = None,
        remind_level: Optional[int] = None,
    ) -> bool:
        set_parts: List[str] = []
        params: Dict[str, Any] = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)}
        if days_of_week is not None:
            try:
                params["days"] = json.dumps([int(d) for d in days_of_week])
            except Exception:
                params["days"] = json.dumps([0, 1, 2, 3, 4])
            set_parts.append("days_of_week = :days")
        if due_time_local is not None:
            params["due_time_local"] = str(due_time_local).strip()
            set_parts.append("due_time_local = :due_time_local")
            # legacy column kept in sync (best-effort)
            params["due_time_utc"] = str(due_time_local).strip()
            set_parts.append("due_time_utc = :due_time_utc")
        if tz_name is not None:
            params["tz_name"] = str(tz_name).strip()
            set_parts.append("tz_name = :tz_name")
        if next_due_at_utc is not None:
            params["next_due_at"] = next_due_at_utc
            set_parts.append("next_due_at = :next_due_at")
        if remind_enabled is not None:
            params["remind_enabled"] = 1 if remind_enabled else 0
            set_parts.append("remind_enabled = :remind_enabled")
        if next_remind_at_utc is not None:
            params["next_remind_at"] = next_remind_at_utc
            set_parts.append("next_remind_at = :next_remind_at")
        if remind_level is not None:
            params["remind_level"] = int(remind_level)
            set_parts.append("remind_level = :remind_level")
        if not set_parts:
            return False
        query = f"""
        UPDATE habits
        SET {", ".join(set_parts)}
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        return bool(self._execute_query(query, params, commit=True))

    def record_habit_checkin(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        note: Optional[str] = None,
        next_due_at_utc: Optional[str] = None,
    ) -> bool:
        # Insert history row (best-effort) and update habit fields.
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO habit_checkins (habit_id, guild_id, user_id, note)
                    VALUES (:habit_id, :guild_id, :user_id, :note)
                    """,
                    {
                        "habit_id": int(habit_id),
                        "guild_id": str(int(guild_id)),
                        "user_id": str(int(user_id)),
                        "note": note.strip() if isinstance(note, str) and note.strip() else None,
                    },
                )
                cur.execute(
                    """
                    UPDATE habits
                    SET last_checkin_at = CURRENT_TIMESTAMP,
                        remind_level = 0,
                        next_remind_at = NULL,
                        next_due_at = :next_due_at
                    WHERE guild_id = :guild_id AND user_id = :user_id AND id = :habit_id
                    """,
                    {
                        "next_due_at": next_due_at_utc,
                        "guild_id": str(int(guild_id)),
                        "user_id": str(int(user_id)),
                        "habit_id": int(habit_id),
                    },
                )
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"record_habit_checkin failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def list_due_habit_reminders(self, now_utc: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        query = """
        SELECT id, guild_id, user_id, name, remind_level, next_due_at, next_remind_at
        FROM habits
        WHERE remind_enabled = 1
          AND next_due_at IS NOT NULL
          AND next_due_at <= :now
          AND (next_remind_at IS NULL OR next_remind_at <= :now)
        ORDER BY COALESCE(next_remind_at, next_due_at) ASC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"now": now_utc, "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def bump_habit_reminder(self, guild_id: int, user_id: int, habit_id: int, remind_level: int, next_remind_at_utc: str) -> bool:
        query = """
        UPDATE habits
        SET remind_level = :level,
            next_remind_at = :next_remind_at
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id AND remind_enabled = 1
        """
        params = {
            "level": int(remind_level),
            "next_remind_at": next_remind_at_utc,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(habit_id),
        }
        return bool(self._execute_query(query, params, commit=True))

    def set_habit_reminder_enabled(self, guild_id: int, user_id: int, habit_id: int, enabled: bool) -> bool:
        """
        Enables/disables reminders. When disabling, clears next_remind_at and resets remind_level.
        """
        query = """
        UPDATE habits
        SET remind_enabled = :enabled,
            remind_level = CASE WHEN :enabled = 1 THEN remind_level ELSE 0 END,
            next_remind_at = CASE WHEN :enabled = 1 THEN next_remind_at ELSE NULL END
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        params = {
            "enabled": 1 if enabled else 0,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(habit_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_habit_reminder_enabled failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    # --- TV Show Subscriptions ---
