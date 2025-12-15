import sqlite3
import logging
import datetime
import json
from typing import List, Dict, Any, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    # Python 3.9+ (may require tzdata package on some platforms)
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


class ProductivityMixin:
    _HABIT_STATS_MAX_DAYS = 3650
    _HABIT_REMIND_PROFILES = {"gentle", "normal", "aggressive", "quiet"}
    _HABIT_SNOOZE_PERIODS = {"week", "month"}

    def _normalize_habit_remind_profile(self, profile: Optional[str]) -> str:
        p = str(profile or "").strip().lower()
        if not p:
            return "normal"
        aliases = {
            "low": "gentle",
            "soft": "gentle",
            "medium": "normal",
            "default": "normal",
            "high": "aggressive",
            "hard": "aggressive",
            "silent": "quiet",
            "daily": "quiet",
        }
        p = aliases.get(p, p)
        return p if p in self._HABIT_REMIND_PROFILES else "normal"

    def _normalize_habit_snooze_period(self, period: Optional[str]) -> str:
        p = str(period or "").strip().lower()
        aliases = {"weekly": "week", "w": "week", "monthly": "month", "m": "month"}
        p = aliases.get(p, p)
        return p if p in self._HABIT_SNOOZE_PERIODS else "week"

    def _parse_sqlite_utc_timestamp(self, ts: Optional[str]) -> Optional[datetime.datetime]:
        if not isinstance(ts, str) or not ts.strip():
            return None
        s = ts.strip()
        try:
            # "YYYY-MM-DD HH:MM:SS" (SQLite CURRENT_TIMESTAMP in UTC)
            dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            return None

    def _tzinfo_from_name(self, tz_name: Optional[str]) -> datetime.tzinfo:
        name = (tz_name or "").strip()
        if not name or name.upper() in ("UTC", "ETC/UTC", "Z"):
            return datetime.timezone.utc

        if ZoneInfo is not None:
            try:
                return ZoneInfo(name)
            except Exception:
                pass

        # Fallback: best-effort fixed offset for the only common case in this bot.
        if name == "Europe/Warsaw":
            return datetime.timezone(datetime.timedelta(hours=1), name="CET")
        return datetime.timezone.utc

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

    def list_todo_items_any_scope(self, user_id: int, include_done: bool = False, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Lists todo items for a user across all guild scopes.
        Useful for DM flows where users still want to see server-created items.
        """
        limit = max(1, min(200, int(limit)))
        base = """
        SELECT id, guild_id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at
        FROM todo_items
        WHERE user_id = :user_id
        """
        params: Dict[str, Any] = {"user_id": str(int(user_id)), "limit": limit}
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

    def get_todo_stats(
        self,
        guild_id: int,
        user_id: int,
        *,
        days: int = 30,
        now_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Computes to-do stats and series for the given scope (guild_id/user_id).

        Returns keys:
          range_start_utc_day, range_end_utc_day,
          open_count, done_count, total_count,
          created_in_range, done_in_range,
          current_done_streak_days, best_done_streak_days,
          avg_hours_to_done (for tasks done within range),
          daily_labels, daily_created, daily_done,
          weekday_done_counts (Mon..Sun)
        """
        days = max(1, min(365, int(days)))
        now_dt = self._parse_sqlite_utc_timestamp(now_utc) if isinstance(now_utc, str) and now_utc.strip() else None
        if not now_dt:
            now_dt = datetime.datetime.now(datetime.timezone.utc)
        end_day = now_dt.date()
        start_day = end_day - datetime.timedelta(days=days - 1)
        start_day_s = start_day.isoformat()
        end_day_s = end_day.isoformat()

        base_params = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id))}

        # Counts
        row_counts = self._execute_query(
            """
            SELECT
                COALESCE(SUM(CASE WHEN is_done = 0 THEN 1 END), 0) AS open_count,
                COALESCE(SUM(CASE WHEN is_done = 1 THEN 1 END), 0) AS done_count,
                COUNT(*) AS total_count
            FROM todo_items
            WHERE guild_id = :guild_id AND user_id = :user_id
            """,
            base_params,
            fetch_one=True,
        ) or {}

        open_count = int(row_counts.get("open_count") or 0)
        done_count = int(row_counts.get("done_count") or 0)
        total_count = int(row_counts.get("total_count") or 0)

        row_range = self._execute_query(
            """
            SELECT
                COALESCE(SUM(CASE WHEN date(created_at) >= :start_day AND date(created_at) <= :end_day THEN 1 END), 0) AS created_in_range,
                COALESCE(SUM(CASE WHEN is_done = 1 AND done_at IS NOT NULL AND date(done_at) >= :start_day AND date(done_at) <= :end_day THEN 1 END), 0) AS done_in_range
            FROM todo_items
            WHERE guild_id = :guild_id AND user_id = :user_id
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s},
            fetch_one=True,
        ) or {}
        created_in_range = int(row_range.get("created_in_range") or 0)
        done_in_range = int(row_range.get("done_in_range") or 0)

        # Avg hours from created->done (for items done in range)
        row_avg = self._execute_query(
            """
            SELECT
                AVG((julianday(done_at) - julianday(created_at)) * 24.0) AS avg_hours
            FROM todo_items
            WHERE guild_id = :guild_id AND user_id = :user_id
              AND is_done = 1
              AND done_at IS NOT NULL
              AND created_at IS NOT NULL
              AND date(done_at) >= :start_day AND date(done_at) <= :end_day
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s},
            fetch_one=True,
        ) or {}
        try:
            avg_hours_to_done = float(row_avg.get("avg_hours")) if row_avg.get("avg_hours") is not None else None
        except (TypeError, ValueError):
            avg_hours_to_done = None

        # Weekday distribution (done_at) in UTC: strftime('%w') => 0=Sun..6=Sat
        wd_rows = self._execute_query(
            """
            SELECT strftime('%w', done_at) AS wd, COUNT(*) AS c
            FROM todo_items
            WHERE guild_id = :guild_id AND user_id = :user_id
              AND is_done = 1 AND done_at IS NOT NULL
              AND date(done_at) >= :start_day AND date(done_at) <= :end_day
            GROUP BY wd
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s},
            fetch_all=True,
        ) or []
        weekday_done_counts = [0, 0, 0, 0, 0, 0, 0]  # Mon..Sun
        for r in wd_rows:
            if not isinstance(r, dict):
                continue
            wd = r.get("wd")
            try:
                c = int(r.get("c") or 0)
            except (TypeError, ValueError):
                c = 0
            try:
                wd_i = int(wd)
            except (TypeError, ValueError):
                continue
            # Convert 0=Sun..6=Sat to 0=Mon..6=Sun
            if wd_i == 0:
                idx = 6
            else:
                idx = wd_i - 1
            if 0 <= idx <= 6:
                weekday_done_counts[idx] += c

        # Daily series using recursive CTE (UTC days)
        series = self._execute_query(
            """
            WITH RECURSIVE days(day) AS (
                SELECT date(:end_day, '-' || (:days - 1) || ' day')
                UNION ALL
                SELECT date(day, '+1 day') FROM days WHERE day < date(:end_day)
            ),
            created AS (
                SELECT date(created_at) AS day, COUNT(*) AS c
                FROM todo_items
                WHERE guild_id = :guild_id AND user_id = :user_id
                  AND date(created_at) >= :start_day AND date(created_at) <= :end_day
                GROUP BY date(created_at)
            ),
            done AS (
                SELECT date(done_at) AS day, COUNT(*) AS c
                FROM todo_items
                WHERE guild_id = :guild_id AND user_id = :user_id
                  AND is_done = 1 AND done_at IS NOT NULL
                  AND date(done_at) >= :start_day AND date(done_at) <= :end_day
                GROUP BY date(done_at)
            )
            SELECT
                d.day AS day,
                COALESCE(c.c, 0) AS created_count,
                COALESCE(x.c, 0) AS done_count
            FROM days d
            LEFT JOIN created c ON c.day = d.day
            LEFT JOIN done x ON x.day = d.day
            ORDER BY d.day ASC
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s, "days": days},
            fetch_all=True,
        ) or []

        daily_labels: List[str] = []
        daily_created: List[int] = []
        daily_done: List[int] = []
        for r in series:
            if not isinstance(r, dict):
                continue
            day_s = r.get("day")
            if not isinstance(day_s, str):
                continue
            daily_labels.append(day_s[5:] if len(day_s) >= 10 else day_s)
            try:
                daily_created.append(int(r.get("created_count") or 0))
            except (TypeError, ValueError):
                daily_created.append(0)
            try:
                daily_done.append(int(r.get("done_count") or 0))
            except (TypeError, ValueError):
                daily_done.append(0)

        # Streaks (UTC days) where done_count > 0
        cur = 0
        for v in reversed(daily_done):
            if int(v) > 0:
                cur += 1
            else:
                break
        best = 0
        run = 0
        for v in daily_done:
            if int(v) > 0:
                run += 1
                if run > best:
                    best = run
            else:
                run = 0

        return {
            "range_start_utc_day": start_day_s,
            "range_end_utc_day": end_day_s,
            "open_count": int(open_count),
            "done_count": int(done_count),
            "total_count": int(total_count),
            "created_in_range": int(created_in_range),
            "done_in_range": int(done_in_range),
            "current_done_streak_days": int(cur),
            "best_done_streak_days": int(best),
            "avg_hours_to_done": avg_hours_to_done,
            "daily_labels": daily_labels,
            "daily_created": daily_created,
            "daily_done": daily_done,
            "weekday_done_counts": weekday_done_counts,
        }

    def get_todo_stats_any_scope(
        self,
        user_id: int,
        *,
        days: int = 30,
        now_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Same as get_todo_stats, but aggregates across all guild scopes for the user.
        Useful for DM flows.
        """
        days = max(1, min(365, int(days)))
        now_dt = self._parse_sqlite_utc_timestamp(now_utc) if isinstance(now_utc, str) and now_utc.strip() else None
        if not now_dt:
            now_dt = datetime.datetime.now(datetime.timezone.utc)
        end_day = now_dt.date()
        start_day = end_day - datetime.timedelta(days=days - 1)
        start_day_s = start_day.isoformat()
        end_day_s = end_day.isoformat()

        base_params = {"user_id": str(int(user_id))}

        row_counts = self._execute_query(
            """
            SELECT
                COALESCE(SUM(CASE WHEN is_done = 0 THEN 1 END), 0) AS open_count,
                COALESCE(SUM(CASE WHEN is_done = 1 THEN 1 END), 0) AS done_count,
                COUNT(*) AS total_count
            FROM todo_items
            WHERE user_id = :user_id
            """,
            base_params,
            fetch_one=True,
        ) or {}
        open_count = int(row_counts.get("open_count") or 0)
        done_count = int(row_counts.get("done_count") or 0)
        total_count = int(row_counts.get("total_count") or 0)

        row_range = self._execute_query(
            """
            SELECT
                COALESCE(SUM(CASE WHEN date(created_at) >= :start_day AND date(created_at) <= :end_day THEN 1 END), 0) AS created_in_range,
                COALESCE(SUM(CASE WHEN is_done = 1 AND done_at IS NOT NULL AND date(done_at) >= :start_day AND date(done_at) <= :end_day THEN 1 END), 0) AS done_in_range
            FROM todo_items
            WHERE user_id = :user_id
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s},
            fetch_one=True,
        ) or {}
        created_in_range = int(row_range.get("created_in_range") or 0)
        done_in_range = int(row_range.get("done_in_range") or 0)

        row_avg = self._execute_query(
            """
            SELECT
                AVG((julianday(done_at) - julianday(created_at)) * 24.0) AS avg_hours
            FROM todo_items
            WHERE user_id = :user_id
              AND is_done = 1
              AND done_at IS NOT NULL
              AND created_at IS NOT NULL
              AND date(done_at) >= :start_day AND date(done_at) <= :end_day
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s},
            fetch_one=True,
        ) or {}
        try:
            avg_hours_to_done = float(row_avg.get("avg_hours")) if row_avg.get("avg_hours") is not None else None
        except (TypeError, ValueError):
            avg_hours_to_done = None

        wd_rows = self._execute_query(
            """
            SELECT strftime('%w', done_at) AS wd, COUNT(*) AS c
            FROM todo_items
            WHERE user_id = :user_id
              AND is_done = 1 AND done_at IS NOT NULL
              AND date(done_at) >= :start_day AND date(done_at) <= :end_day
            GROUP BY wd
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s},
            fetch_all=True,
        ) or []
        weekday_done_counts = [0, 0, 0, 0, 0, 0, 0]
        for r in wd_rows:
            if not isinstance(r, dict):
                continue
            wd = r.get("wd")
            try:
                c = int(r.get("c") or 0)
            except (TypeError, ValueError):
                c = 0
            try:
                wd_i = int(wd)
            except (TypeError, ValueError):
                continue
            idx = 6 if wd_i == 0 else wd_i - 1
            if 0 <= idx <= 6:
                weekday_done_counts[idx] += c

        series = self._execute_query(
            """
            WITH RECURSIVE days(day) AS (
                SELECT date(:end_day, '-' || (:days - 1) || ' day')
                UNION ALL
                SELECT date(day, '+1 day') FROM days WHERE day < date(:end_day)
            ),
            created AS (
                SELECT date(created_at) AS day, COUNT(*) AS c
                FROM todo_items
                WHERE user_id = :user_id
                  AND date(created_at) >= :start_day AND date(created_at) <= :end_day
                GROUP BY date(created_at)
            ),
            done AS (
                SELECT date(done_at) AS day, COUNT(*) AS c
                FROM todo_items
                WHERE user_id = :user_id
                  AND is_done = 1 AND done_at IS NOT NULL
                  AND date(done_at) >= :start_day AND date(done_at) <= :end_day
                GROUP BY date(done_at)
            )
            SELECT
                d.day AS day,
                COALESCE(c.c, 0) AS created_count,
                COALESCE(x.c, 0) AS done_count
            FROM days d
            LEFT JOIN created c ON c.day = d.day
            LEFT JOIN done x ON x.day = d.day
            ORDER BY d.day ASC
            """,
            {**base_params, "start_day": start_day_s, "end_day": end_day_s, "days": days},
            fetch_all=True,
        ) or []

        daily_labels: List[str] = []
        daily_created: List[int] = []
        daily_done: List[int] = []
        for r in series:
            if not isinstance(r, dict):
                continue
            day_s = r.get("day")
            if not isinstance(day_s, str):
                continue
            daily_labels.append(day_s[5:] if len(day_s) >= 10 else day_s)
            try:
                daily_created.append(int(r.get("created_count") or 0))
            except (TypeError, ValueError):
                daily_created.append(0)
            try:
                daily_done.append(int(r.get("done_count") or 0))
            except (TypeError, ValueError):
                daily_done.append(0)

        cur = 0
        for v in reversed(daily_done):
            if int(v) > 0:
                cur += 1
            else:
                break
        best = 0
        run = 0
        for v in daily_done:
            if int(v) > 0:
                run += 1
                if run > best:
                    best = run
            else:
                run = 0

        return {
            "range_start_utc_day": start_day_s,
            "range_end_utc_day": end_day_s,
            "open_count": int(open_count),
            "done_count": int(done_count),
            "total_count": int(total_count),
            "created_in_range": int(created_in_range),
            "done_in_range": int(done_in_range),
            "current_done_streak_days": int(cur),
            "best_done_streak_days": int(best),
            "avg_hours_to_done": avg_hours_to_done,
            "daily_labels": daily_labels,
            "daily_created": daily_created,
            "daily_done": daily_done,
            "weekday_done_counts": weekday_done_counts,
        }

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

    def list_users_with_productivity_data(self, limit: int = 5000) -> List[int]:
        """
        Returns distinct user_ids that have habits or todos (any guild scope).
        """
        limit = max(1, min(50000, int(limit)))
        rows = self._execute_query(
            """
            SELECT user_id FROM (
                SELECT DISTINCT user_id FROM habits
                UNION
                SELECT DISTINCT user_id FROM todo_items
            )
            LIMIT :limit
            """,
            {"limit": int(limit)},
            fetch_all=True,
        )
        out: List[int] = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            uid = r.get("user_id")
            try:
                out.append(int(uid))
            except Exception:
                continue
        return out

    def list_habits_any_scope(self, user_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        """
        Lists habits for a user across all guild scopes.
        """
        limit = max(1, min(2000, int(limit)))
        query = """
        SELECT id, guild_id, name, days_of_week, due_time_local, tz_name, due_time_utc,
               remind_enabled, remind_profile,
               snoozed_until, last_snooze_at, last_snooze_period,
               remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(int(user_id)), "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    # -------------------------
    # Productivity: Habits
    # -------------------------
    def list_habit_checkins(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        *,
        since_utc: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Returns check-in history rows for a habit.
        Output rows: {checked_in_at, note}
        """
        limit = max(1, min(20000, int(limit)))
        query = """
        SELECT checked_in_at, note
        FROM habit_checkins
        WHERE habit_id = :habit_id
          AND guild_id = :guild_id
          AND user_id = :user_id
        """
        params: Dict[str, Any] = {
            "habit_id": int(habit_id),
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "limit": limit,
        }
        if isinstance(since_utc, str) and since_utc.strip():
            query += " AND checked_in_at >= :since"
            params["since"] = since_utc.strip()
        query += " ORDER BY checked_in_at ASC LIMIT :limit"
        rows = self._execute_query(query, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def list_habit_checkins_any_scope(
        self,
        user_id: int,
        habit_id: int,
        *,
        since_utc: Optional[str] = None,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Returns check-ins by (user_id, habit_id) regardless of guild scope.
        """
        habit = self.get_habit_any_scope(user_id, habit_id)
        if not habit:
            return []
        try:
            guild_id = int(habit.get("guild_id") or 0)
        except Exception:
            guild_id = 0
        return self.list_habit_checkins(guild_id, user_id, habit_id, since_utc=since_utc, limit=limit)

    def _parse_days_of_week_json(self, days_json: Optional[str]) -> List[int]:
        try:
            days = json.loads(days_json or "[]")
            if not isinstance(days, list):
                return [0, 1, 2, 3, 4]
            out = []
            for d in days:
                try:
                    di = int(d)
                except Exception:
                    continue
                if 0 <= di <= 6:
                    out.append(di)
            out = sorted(set(out))
            return out if out else [0, 1, 2, 3, 4]
        except Exception:
            return [0, 1, 2, 3, 4]

    def _bucket_checkins_by_local_date(
        self,
        checkins: List[Dict[str, Any]],
        tz: datetime.tzinfo,
    ) -> Tuple[Set[datetime.date], Dict[datetime.date, int], Dict[int, int], int, Optional[datetime.datetime]]:
        """
        Returns:
          - completed_dates: set of local dates with >=1 check-in
          - per_day_counts: local date -> count
          - per_weekday_counts: 0=Mon..6=Sun -> count
          - total_checkins
          - last_checkin_utc_dt (max, if any)
        """
        completed_dates: Set[datetime.date] = set()
        per_day_counts: Dict[datetime.date, int] = {}
        per_weekday_counts: Dict[int, int] = {i: 0 for i in range(7)}
        total = 0
        last_dt_utc: Optional[datetime.datetime] = None

        for r in checkins or []:
            if not isinstance(r, dict):
                continue
            ts = r.get("checked_in_at")
            dt_utc = self._parse_sqlite_utc_timestamp(ts if isinstance(ts, str) else None)
            if not dt_utc:
                continue
            if last_dt_utc is None or dt_utc > last_dt_utc:
                last_dt_utc = dt_utc
            dt_local = dt_utc.astimezone(tz)
            d = dt_local.date()
            completed_dates.add(d)
            per_day_counts[d] = int(per_day_counts.get(d, 0)) + 1
            try:
                wd = int(dt_local.weekday())
            except Exception:
                wd = 0
            if 0 <= wd <= 6:
                per_weekday_counts[wd] = int(per_weekday_counts.get(wd, 0)) + 1
            total += 1

        return completed_dates, per_day_counts, per_weekday_counts, total, last_dt_utc

    def get_habit_stats(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        *,
        days: Optional[int] = 30,
        now_utc: Optional[str] = None,
        streak_max_days: int = 3650,
    ) -> Optional[Dict[str, Any]]:
        """
        Computes habit stats for the last N local days and scheduled-day streaks.

        Returns dict with keys:
          name, tz_name, days_of_week, range_start_local, range_end_local,
          scheduled_days, completed_days, completion_rate,
          current_streak, best_streak,
          total_checkins, last_checkin_at_utc,
          daily_labels, daily_counts,
          weekday_counts (Mon..Sun)
        """
        # days=None means "all time since created_at" (with a safety cap).
        if days is None:
            days_n: Optional[int] = None
        else:
            days_n = max(1, min(self._HABIT_STATS_MAX_DAYS, int(days)))
        streak_max_days = max(30, min(3650, int(streak_max_days)))

        habit = self.get_habit(guild_id, user_id, habit_id)
        if not habit:
            return None

        tz_name = str(habit.get("tz_name") or "UTC").strip()
        tz = self._tzinfo_from_name(tz_name)
        sched_days = self._parse_days_of_week_json(habit.get("days_of_week"))
        sched_set = set(sched_days)

        now_dt = self._parse_sqlite_utc_timestamp(now_utc) if isinstance(now_utc, str) and now_utc.strip() else None
        if not now_dt:
            now_dt = datetime.datetime.now(datetime.timezone.utc)
        now_local = now_dt.astimezone(tz)
        range_end_local = now_local.date()

        # Provisional range start (before applying created_at clamp):
        cap_start = range_end_local - datetime.timedelta(days=self._HABIT_STATS_MAX_DAYS - 1)
        provisional_start = cap_start if days_n is None else (range_end_local - datetime.timedelta(days=days_n - 1))

        created_dt = self._parse_sqlite_utc_timestamp(habit.get("created_at"))
        created_local_date = created_dt.astimezone(tz).date() if created_dt else provisional_start

        # If caller overrides `now_utc` to an earlier time than the habit's created_at (tests / backfills),
        # clamp the effective "created" bound so stats still work for that requested window.
        if created_local_date > range_end_local:
            created_local_date = provisional_start

        range_start_local = max(created_local_date, cap_start) if days_n is None else provisional_start
        # Avoid iterating unbounded histories for streak computations.
        earliest_local_for_streak = max(created_local_date, range_end_local - datetime.timedelta(days=streak_max_days))

        # Fetch checkins (for bucketing): best-effort, limited.
        # We fetch from earliest_local_for_streak - 2 days (UTC) to reduce edge misses due to tz offsets.
        earliest_streak_dt_local = datetime.datetime.combine(earliest_local_for_streak, datetime.time(0, 0), tzinfo=tz)
        earliest_streak_dt_utc = earliest_streak_dt_local.astimezone(datetime.timezone.utc) - datetime.timedelta(days=2)
        since_utc = earliest_streak_dt_utc.strftime("%Y-%m-%d %H:%M:%S")

        checkins = self.list_habit_checkins(guild_id, user_id, habit_id, since_utc=since_utc, limit=20000)
        completed_dates, per_day_counts, per_weekday_counts, total_checkins, last_dt_utc = self._bucket_checkins_by_local_date(checkins, tz)

        # Daily series (last N days)
        daily_labels: List[str] = []
        daily_counts: List[int] = []
        scheduled_days = 0
        completed_days = 0

        d = range_start_local
        while d <= range_end_local:
            label = d.strftime("%m-%d")
            daily_labels.append(label)
            cnt = int(per_day_counts.get(d, 0))
            daily_counts.append(cnt)
            # Do not count scheduled/completed days before the habit existed.
            if d >= created_local_date and d.weekday() in sched_set:
                scheduled_days += 1
                if cnt > 0:
                    completed_days += 1
            d = d + datetime.timedelta(days=1)

        completion_rate = (float(completed_days) / float(scheduled_days)) if scheduled_days > 0 else 0.0

        # Streaks are over *scheduled days* only.
        # Best streak since earliest_local_for_streak
        best = 0
        run = 0
        d = earliest_local_for_streak
        while d <= range_end_local:
            if d.weekday() in sched_set:
                if d in completed_dates:
                    run += 1
                    if run > best:
                        best = run
                else:
                    run = 0
            d = d + datetime.timedelta(days=1)

        # Current streak: walk backwards through scheduled days
        cur = 0
        d = range_end_local
        # Find the last scheduled day <= today
        guard = 0
        while d.weekday() not in sched_set and guard < 14:
            d = d - datetime.timedelta(days=1)
            guard += 1
        # Now count consecutive completions on scheduled days
        guard = 0
        while d >= earliest_local_for_streak and guard < streak_max_days:
            if d.weekday() in sched_set:
                if d in completed_dates:
                    cur += 1
                else:
                    break
            d = d - datetime.timedelta(days=1)
            guard += 1

        weekday_counts = [int(per_weekday_counts.get(i, 0)) for i in range(7)]

        return {
            "id": int(habit_id),
            "name": habit.get("name") or "Habit",
            "tz_name": tz_name or "UTC",
            "days_of_week": sched_days,
            "range_start_local": range_start_local.isoformat(),
            "range_end_local": range_end_local.isoformat(),
            "scheduled_days": int(scheduled_days),
            "completed_days": int(completed_days),
            "completion_rate": float(completion_rate),
            "current_streak": int(cur),
            "best_streak": int(best),
            "total_checkins": int(total_checkins),
            "last_checkin_at_utc": (last_dt_utc.strftime("%Y-%m-%d %H:%M:%S") if last_dt_utc else None),
            "daily_labels": daily_labels,
            "daily_counts": daily_counts,
            "weekday_counts": weekday_counts,
        }

    def get_habits_overall_stats(
        self,
        guild_id: int,
        user_id: int,
        *,
        days: Optional[int] = 30,
        limit_habits: int = 50,
        now_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregated habit stats across all habits in the given scope (guild_id/user_id).

        Notes:
        - Habits can have different timezones; per-habit stats are computed in each habit's tz,
          then summed for overall scheduled/completed days totals.
        """
        days_n: Optional[int]
        if days is None:
            days_n = None
        else:
            days_n = max(1, min(self._HABIT_STATS_MAX_DAYS, int(days)))
        limit_habits = max(1, min(200, int(limit_habits)))

        habits = self.list_habits(guild_id, user_id, limit_habits)
        summaries: List[Dict[str, Any]] = []

        total_scheduled = 0
        total_completed = 0
        total_checkins = 0
        best_streak_max = 0
        current_streak_sum = 0
        habits_with_stats = 0

        for h in habits or []:
            if not isinstance(h, dict):
                continue
            hid = h.get("id")
            try:
                hid_i = int(hid)
            except Exception:
                continue

            s = self.get_habit_stats(guild_id, user_id, hid_i, days=days_n, now_utc=now_utc)
            if not isinstance(s, dict):
                continue

            scheduled = int(s.get("scheduled_days") or 0)
            completed = int(s.get("completed_days") or 0)
            rate = float(s.get("completion_rate") or 0.0)
            cur = int(s.get("current_streak") or 0)
            best = int(s.get("best_streak") or 0)
            tcheck = int(s.get("total_checkins") or 0)

            summaries.append(
                {
                    "id": hid_i,
                    "name": str(s.get("name") or h.get("name") or "Habit"),
                    "scheduled_days": scheduled,
                    "completed_days": completed,
                    "completion_rate": rate,
                    "current_streak": cur,
                    "best_streak": best,
                    "total_checkins": tcheck,
                    "tz_name": str(s.get("tz_name") or h.get("tz_name") or "UTC"),
                }
            )

            total_scheduled += scheduled
            total_completed += completed
            total_checkins += tcheck
            best_streak_max = max(best_streak_max, best)
            current_streak_sum += cur
            habits_with_stats += 1

        overall_rate = (float(total_completed) / float(total_scheduled)) if total_scheduled > 0 else 0.0
        avg_current_streak = (float(current_streak_sum) / float(habits_with_stats)) if habits_with_stats > 0 else 0.0
        avg_habit_rate = (
            (sum(float(x.get("completion_rate") or 0.0) for x in summaries) / float(habits_with_stats))
            if habits_with_stats > 0
            else 0.0
        )

        return {
            "guild_id": int(guild_id),
            "user_id": int(user_id),
            "days": (None if days_n is None else int(days_n)),
            "habits_total": int(len(habits or [])),
            "habits_with_stats": int(habits_with_stats),
            "total_scheduled_days": int(total_scheduled),
            "total_completed_days": int(total_completed),
            "overall_completion_rate": float(overall_rate),
            "avg_habit_completion_rate": float(avg_habit_rate),
            "total_checkins": int(total_checkins),
            "best_streak_max": int(best_streak_max),
            "avg_current_streak": float(avg_current_streak),
            "habits": summaries,
        }

    def get_habits_overall_stats_any_scope(
        self,
        user_id: int,
        *,
        days: Optional[int] = 30,
        limit_habits: int = 50,
        now_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregated habit stats across all habits for the user, regardless of guild scope.
        Intended for DM usage.
        """
        days_n: Optional[int]
        if days is None:
            days_n = None
        else:
            days_n = max(1, min(self._HABIT_STATS_MAX_DAYS, int(days)))
        limit_habits = max(1, min(200, int(limit_habits)))

        habits = self.list_habits_any_scope(user_id, limit_habits)
        summaries: List[Dict[str, Any]] = []

        total_scheduled = 0
        total_completed = 0
        total_checkins = 0
        best_streak_max = 0
        current_streak_sum = 0
        habits_with_stats = 0

        for h in habits or []:
            if not isinstance(h, dict):
                continue
            hid = h.get("id")
            try:
                hid_i = int(hid)
            except Exception:
                continue
            try:
                gid_i = int(h.get("guild_id") or 0)
            except Exception:
                gid_i = 0

            s = self.get_habit_stats(gid_i, user_id, hid_i, days=days_n, now_utc=now_utc)
            if not isinstance(s, dict):
                continue

            scheduled = int(s.get("scheduled_days") or 0)
            completed = int(s.get("completed_days") or 0)
            rate = float(s.get("completion_rate") or 0.0)
            cur = int(s.get("current_streak") or 0)
            best = int(s.get("best_streak") or 0)
            tcheck = int(s.get("total_checkins") or 0)

            summaries.append(
                {
                    "id": hid_i,
                    "guild_id": int(gid_i),
                    "name": str(s.get("name") or h.get("name") or "Habit"),
                    "scheduled_days": scheduled,
                    "completed_days": completed,
                    "completion_rate": rate,
                    "current_streak": cur,
                    "best_streak": best,
                    "total_checkins": tcheck,
                    "tz_name": str(s.get("tz_name") or h.get("tz_name") or "UTC"),
                }
            )

            total_scheduled += scheduled
            total_completed += completed
            total_checkins += tcheck
            best_streak_max = max(best_streak_max, best)
            current_streak_sum += cur
            habits_with_stats += 1

        overall_rate = (float(total_completed) / float(total_scheduled)) if total_scheduled > 0 else 0.0
        avg_current_streak = (float(current_streak_sum) / float(habits_with_stats)) if habits_with_stats > 0 else 0.0
        avg_habit_rate = (
            (sum(float(x.get("completion_rate") or 0.0) for x in summaries) / float(habits_with_stats))
            if habits_with_stats > 0
            else 0.0
        )

        return {
            "user_id": int(user_id),
            "days": (None if days_n is None else int(days_n)),
            "habits_total": int(len(habits or [])),
            "habits_with_stats": int(habits_with_stats),
            "total_scheduled_days": int(total_scheduled),
            "total_completed_days": int(total_completed),
            "overall_completion_rate": float(overall_rate),
            "avg_habit_completion_rate": float(avg_habit_rate),
            "total_checkins": int(total_checkins),
            "best_streak_max": int(best_streak_max),
            "avg_current_streak": float(avg_current_streak),
            "habits": summaries,
        }
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
        SELECT id, name, days_of_week, due_time_local, tz_name, due_time_utc,
               remind_enabled, remind_profile,
               snoozed_until, last_snooze_at, last_snooze_period,
               remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE guild_id = :guild_id AND user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def get_habit(self, guild_id: int, user_id: int, habit_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT id, name, days_of_week, due_time_local, tz_name, due_time_utc,
               remind_enabled, remind_profile,
               snoozed_until, last_snooze_at, last_snooze_period,
               remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
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
        SELECT id, guild_id, name, days_of_week, due_time_local, tz_name, due_time_utc,
               remind_enabled, remind_profile,
               snoozed_until, last_snooze_at, last_snooze_period,
               remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
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
        name: Optional[str] = None,
        days_of_week: Optional[List[int]] = None,
        due_time_local: Optional[str] = None,
        tz_name: Optional[str] = None,
        next_due_at_utc: Optional[str] = None,
        remind_enabled: Optional[bool] = None,
        remind_profile: Optional[str] = None,
        next_remind_at_utc: Optional[str] = None,
        remind_level: Optional[int] = None,
        clear_next_remind_at: bool = False,
        clear_snoozed_until: bool = False,
    ) -> bool:
        set_parts: List[str] = []
        params: Dict[str, Any] = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)}
        if name is not None:
            nm = str(name).strip()
            if nm:
                params["name"] = nm
                set_parts.append("name = :name")
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
        if remind_profile is not None:
            params["remind_profile"] = self._normalize_habit_remind_profile(remind_profile)
            set_parts.append("remind_profile = :remind_profile")
        if next_remind_at_utc is not None:
            params["next_remind_at"] = next_remind_at_utc
            set_parts.append("next_remind_at = :next_remind_at")
        if remind_level is not None:
            params["remind_level"] = int(remind_level)
            set_parts.append("remind_level = :remind_level")
        if clear_next_remind_at:
            set_parts.append("next_remind_at = NULL")
        if clear_snoozed_until:
            set_parts.append("snoozed_until = NULL")
        if not set_parts:
            return False
        query = f"""
        UPDATE habits
        SET {", ".join(set_parts)}
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        return bool(self._execute_query(query, params, commit=True))

    def set_habit_schedule_and_due_any_scope(
        self,
        user_id: int,
        habit_id: int,
        *,
        name: Optional[str] = None,
        days_of_week: Optional[List[int]] = None,
        due_time_local: Optional[str] = None,
        tz_name: Optional[str] = None,
        next_due_at_utc: Optional[str] = None,
        remind_enabled: Optional[bool] = None,
        remind_profile: Optional[str] = None,
        next_remind_at_utc: Optional[str] = None,
        remind_level: Optional[int] = None,
        clear_next_remind_at: bool = False,
        clear_snoozed_until: bool = False,
    ) -> bool:
        habit = self.get_habit_any_scope(user_id, habit_id)
        if not habit:
            return False
        try:
            guild_id = int(habit.get("guild_id") or 0)
        except Exception:
            guild_id = 0
        return self.set_habit_schedule_and_due(
            guild_id,
            user_id,
            habit_id,
            name=name,
            days_of_week=days_of_week,
            due_time_local=due_time_local,
            tz_name=tz_name,
            next_due_at_utc=next_due_at_utc,
            remind_enabled=remind_enabled,
            remind_profile=remind_profile,
            next_remind_at_utc=next_remind_at_utc,
            remind_level=remind_level,
            clear_next_remind_at=clear_next_remind_at,
            clear_snoozed_until=clear_snoozed_until,
        )

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
                        snoozed_until = NULL,
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
        SELECT id, guild_id, user_id, name, remind_profile, snoozed_until, last_snooze_at, last_snooze_period,
               remind_level, next_due_at, next_remind_at
        FROM habits
        WHERE remind_enabled = 1
          AND next_due_at IS NOT NULL
          AND next_due_at <= :now
          AND (snoozed_until IS NULL OR snoozed_until <= :now)
          AND (next_remind_at IS NULL OR next_remind_at <= :now)
        ORDER BY COALESCE(next_remind_at, next_due_at) ASC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"now": now_utc, "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def snooze_habit_for_day(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        now_utc: Optional[str] = None,
        period: str = "week",
        days: int = 1,
    ) -> Dict[str, Any]:
        """
        Snooze a habit for the rest of today (and optionally N days) in the habit's timezone.
        Snooze will NOT extend past the habit's upcoming `next_due_at` (if it is in the future).
        Enforces: once per week or once per calendar month (per habit).

        Returns: { ok: bool, error?: str, snoozed_until?: str, next_allowed_at?: str, effective_period?: str }
        """
        period_n = self._normalize_habit_snooze_period(period)
        now_dt = self._parse_sqlite_utc_timestamp(now_utc) if now_utc else datetime.datetime.now(datetime.timezone.utc)
        if not now_dt:
            now_dt = datetime.datetime.now(datetime.timezone.utc)
        days = max(1, min(30, int(days)))
        now_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        habit = self.get_habit(guild_id, user_id, habit_id)
        if not habit:
            return {"ok": False, "error": "not_found", "effective_period": period_n}

        tz = self._tzinfo_from_name(habit.get("tz_name"))
        now_local = now_dt.astimezone(tz)
        target_date = now_local.date() + datetime.timedelta(days=days)
        snoozed_until_local = datetime.datetime.combine(target_date, datetime.time(0, 0), tzinfo=tz)
        snoozed_until_dt = snoozed_until_local.astimezone(datetime.timezone.utc)

        # Don't snooze beyond the next upcoming due time (if not due yet).
        next_due_dt = self._parse_sqlite_utc_timestamp(habit.get("next_due_at"))
        if next_due_dt and next_due_dt > now_dt and next_due_dt < snoozed_until_dt:
            snoozed_until_dt = next_due_dt

        snoozed_until = snoozed_until_dt.strftime("%Y-%m-%d %H:%M:%S")

        last_ts = self._parse_sqlite_utc_timestamp(habit.get("last_snooze_at"))
        if last_ts:
            if period_n == "week":
                next_allowed = last_ts + datetime.timedelta(days=7)
                if now_dt < next_allowed:
                    return {
                        "ok": False,
                        "error": "cooldown",
                        "next_allowed_at": next_allowed.strftime("%Y-%m-%d %H:%M:%S"),
                        "effective_period": period_n,
                    }
            else:
                # month = once per calendar month (UTC)
                if last_ts.year == now_dt.year and last_ts.month == now_dt.month:
                    # next allowed at start of next month (UTC)
                    if now_dt.month == 12:
                        next_allowed = datetime.datetime(now_dt.year + 1, 1, 1, tzinfo=datetime.timezone.utc)
                    else:
                        next_allowed = datetime.datetime(now_dt.year, now_dt.month + 1, 1, tzinfo=datetime.timezone.utc)
                    return {
                        "ok": False,
                        "error": "cooldown",
                        "next_allowed_at": next_allowed.strftime("%Y-%m-%d %H:%M:%S"),
                        "effective_period": period_n,
                    }

        query = """
        UPDATE habits
        SET snoozed_until = :snoozed_until,
            last_snooze_at = :now,
            last_snooze_period = :period,
            remind_level = 0,
            next_remind_at = :snoozed_until
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        params = {
            "snoozed_until": snoozed_until,
            "now": now_s,
            "period": period_n,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(habit_id),
        }
        ok = bool(self._execute_query(query, params, commit=True))
        return {"ok": ok, "snoozed_until": snoozed_until, "effective_period": period_n}

    def snooze_habit_for_day_any_scope(
        self,
        user_id: int,
        habit_id: int,
        now_utc: Optional[str] = None,
        period: str = "week",
        days: int = 1,
    ) -> Dict[str, Any]:
        period_n = self._normalize_habit_snooze_period(period)
        now_dt = self._parse_sqlite_utc_timestamp(now_utc) if now_utc else datetime.datetime.now(datetime.timezone.utc)
        if not now_dt:
            now_dt = datetime.datetime.now(datetime.timezone.utc)
        days = max(1, min(30, int(days)))
        now_s = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        habit = self.get_habit_any_scope(user_id, habit_id)
        if not habit:
            return {"ok": False, "error": "not_found", "effective_period": period_n}

        tz = self._tzinfo_from_name(habit.get("tz_name"))
        now_local = now_dt.astimezone(tz)
        target_date = now_local.date() + datetime.timedelta(days=days)
        snoozed_until_local = datetime.datetime.combine(target_date, datetime.time(0, 0), tzinfo=tz)
        snoozed_until_dt = snoozed_until_local.astimezone(datetime.timezone.utc)

        # Don't snooze beyond the next upcoming due time (if not due yet).
        next_due_dt = self._parse_sqlite_utc_timestamp(habit.get("next_due_at"))
        if next_due_dt and next_due_dt > now_dt and next_due_dt < snoozed_until_dt:
            snoozed_until_dt = next_due_dt

        snoozed_until = snoozed_until_dt.strftime("%Y-%m-%d %H:%M:%S")

        last_ts = self._parse_sqlite_utc_timestamp(habit.get("last_snooze_at"))
        if last_ts:
            if period_n == "week":
                next_allowed = last_ts + datetime.timedelta(days=7)
                if now_dt < next_allowed:
                    return {
                        "ok": False,
                        "error": "cooldown",
                        "next_allowed_at": next_allowed.strftime("%Y-%m-%d %H:%M:%S"),
                        "effective_period": period_n,
                    }
            else:
                if last_ts.year == now_dt.year and last_ts.month == now_dt.month:
                    if now_dt.month == 12:
                        next_allowed = datetime.datetime(now_dt.year + 1, 1, 1, tzinfo=datetime.timezone.utc)
                    else:
                        next_allowed = datetime.datetime(now_dt.year, now_dt.month + 1, 1, tzinfo=datetime.timezone.utc)
                    return {
                        "ok": False,
                        "error": "cooldown",
                        "next_allowed_at": next_allowed.strftime("%Y-%m-%d %H:%M:%S"),
                        "effective_period": period_n,
                    }

        query = """
        UPDATE habits
        SET snoozed_until = :snoozed_until,
            last_snooze_at = :now,
            last_snooze_period = :period,
            remind_level = 0,
            next_remind_at = :snoozed_until
        WHERE user_id = :user_id AND id = :id
        """
        params = {"snoozed_until": snoozed_until, "now": now_s, "period": period_n, "user_id": str(int(user_id)), "id": int(habit_id)}
        ok = bool(self._execute_query(query, params, commit=True))
        return {"ok": ok, "snoozed_until": snoozed_until, "effective_period": period_n}

    def set_habit_reminder_profile(self, guild_id: int, user_id: int, habit_id: int, profile: str) -> bool:
        """
        Sets how often reminders are sent for a habit.
        Allowed profiles: gentle|normal|aggressive|quiet
        """
        p = self._normalize_habit_remind_profile(profile)
        query = """
        UPDATE habits
        SET remind_profile = :profile
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        params = {"profile": p, "guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)}
        return bool(self._execute_query(query, params, commit=True))

    def set_habit_reminder_profile_any_scope(self, user_id: int, habit_id: int, profile: str) -> bool:
        """
        Sets reminder profile by (user_id, id) regardless of guild scope.
        """
        p = self._normalize_habit_remind_profile(profile)
        query = """
        UPDATE habits
        SET remind_profile = :profile
        WHERE user_id = :user_id AND id = :id
        """
        params = {"profile": p, "user_id": str(int(user_id)), "id": int(habit_id)}
        return bool(self._execute_query(query, params, commit=True))

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
