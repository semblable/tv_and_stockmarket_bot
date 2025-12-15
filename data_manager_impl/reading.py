import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ReadingMixin:
    def create_reading_item(
        self,
        user_id: int,
        title: str,
        author: Optional[str] = None,
        ol_work_id: Optional[str] = None,
        ol_edition_id: Optional[str] = None,
        cover_url: Optional[str] = None,
        format: Optional[str] = None,
        total_pages: Optional[int] = None,
        total_audio_seconds: Optional[int] = None,
    ) -> Optional[int]:
        """
        Creates a new reading item and returns its ID.
        """
        if not title or not str(title).strip():
            return None
        query = """
        INSERT INTO reading_items
            (user_id, title, author, ol_work_id, ol_edition_id, cover_url, format, total_pages, total_audio_seconds, status)
        VALUES
            (:user_id, :title, :author, :ol_work_id, :ol_edition_id, :cover_url, :format, :total_pages, :total_audio_seconds, 'reading')
        """
        params = {
            "user_id": str(user_id),
            "title": title.strip(),
            "author": author.strip() if isinstance(author, str) and author.strip() else None,
            "ol_work_id": ol_work_id.strip() if isinstance(ol_work_id, str) and ol_work_id.strip() else None,
            "ol_edition_id": ol_edition_id.strip() if isinstance(ol_edition_id, str) and ol_edition_id.strip() else None,
            "cover_url": cover_url.strip() if isinstance(cover_url, str) and cover_url.strip() else None,
            "format": format.strip() if isinstance(format, str) and format.strip() else None,
            "total_pages": int(total_pages) if total_pages is not None else None,
            "total_audio_seconds": int(total_audio_seconds) if total_audio_seconds is not None else None,
        }
        conn = self._get_connection()
        cur = None
        # Connection is shared; keep this consistent with other mixins.
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"create_reading_item failed: {e}")
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

    def get_reading_item(self, user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT *
        FROM reading_items
        WHERE user_id = :user_id AND id = :item_id
        """
        row = self._execute_query(query, {"user_id": str(user_id), "item_id": int(item_id)}, fetch_one=True)
        return row if isinstance(row, dict) else None

    def list_reading_items(
        self,
        user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        statuses = statuses or ["reading", "paused"]
        placeholders = ", ".join([f":s{i}" for i in range(len(statuses))]) if statuses else "'reading'"
        query = f"""
        SELECT *
        FROM reading_items
        WHERE user_id = :user_id AND status IN ({placeholders})
        ORDER BY COALESCE(last_update_at, created_at) DESC, id DESC
        LIMIT :limit
        """
        params: Dict[str, Any] = {"user_id": str(user_id), "limit": int(limit)}
        for i, s in enumerate(statuses):
            params[f"s{i}"] = s
        rows = self._execute_query(query, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def set_current_reading_item_id(self, user_id: int, item_id: Optional[int]) -> bool:
        """
        Persists the user's current reading item id in user_preferences.
        """
        if item_id is None:
            return self.delete_user_preference(user_id, "reading_current_item_id")
        return self.set_user_preference(user_id, "reading_current_item_id", int(item_id))

    def get_current_reading_item_id(self, user_id: int) -> Optional[int]:
        item_id = self.get_user_preference(user_id, "reading_current_item_id", None)
        if item_id is None:
            return None
        try:
            return int(item_id)
        except (TypeError, ValueError):
            return None

    def get_current_reading_item(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Returns the current reading item (preference-backed). Falls back to latest active.
        """
        preferred_id = self.get_current_reading_item_id(user_id)
        if preferred_id is not None:
            item = self.get_reading_item(user_id, preferred_id)
            if item:
                return item

        # fallback: most recently updated active item
        items = self.list_reading_items(user_id, statuses=["reading", "paused"], limit=1)
        return items[0] if items else None

    def _insert_reading_update(
        self,
        user_id: int,
        item_id: int,
        kind: str,
        value: Optional[float] = None,
        note: Optional[str] = None,
    ) -> bool:
        query = """
        INSERT INTO reading_updates (item_id, user_id, kind, value, note)
        VALUES (:item_id, :user_id, :kind, :value, :note)
        """
        params = {
            "item_id": int(item_id),
            "user_id": str(user_id),
            "kind": kind,
            "value": value,
            "note": note.strip() if isinstance(note, str) and note.strip() else None,
        }
        return bool(self._execute_query(query, params, commit=True))

    def update_reading_progress(
        self,
        user_id: int,
        item_id: int,
        *,
        page: Optional[int] = None,
        pages_delta: Optional[int] = None,
        kindle_loc: Optional[int] = None,
        percent: Optional[float] = None,
        audio_seconds: Optional[int] = None,
        audio_delta_seconds: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Applies progress updates to the reading_items row and logs them into reading_updates.
        Returns the updated item dict, or None on failure.
        """
        item = self.get_reading_item(user_id, item_id)
        if not item:
            return None

        updates: Dict[str, Any] = {}
        log_entries: List[tuple[str, Optional[float]]] = []
        delta_entries: List[tuple[str, Optional[float]]] = []

        # Capture "before" values for deltas/stats
        try:
            prev_page = int(item.get("current_page") or 0)
        except (TypeError, ValueError):
            prev_page = 0
        try:
            prev_audio = int(item.get("current_audio_seconds") or 0)
        except (TypeError, ValueError):
            prev_audio = 0

        if pages_delta is not None:
            try:
                current = int(item.get("current_page") or 0)
                page = current + int(pages_delta)
            except (TypeError, ValueError):
                page = None

        if audio_delta_seconds is not None:
            try:
                current = int(item.get("current_audio_seconds") or 0)
                audio_seconds = current + int(audio_delta_seconds)
            except (TypeError, ValueError):
                audio_seconds = None

        if page is not None:
            try:
                page_i = max(0, int(page))
                updates["current_page"] = page_i
                log_entries.append(("page", float(page_i)))
            except (TypeError, ValueError):
                pass

        if kindle_loc is not None:
            try:
                kl = max(0, int(kindle_loc))
                updates["current_kindle_location"] = kl
                log_entries.append(("kindle_loc", float(kl)))
            except (TypeError, ValueError):
                pass

        if percent is not None:
            try:
                p = float(percent)
                # treat values like 0.42 as 42%
                if 0.0 <= p <= 1.0:
                    p *= 100.0
                p = max(0.0, min(100.0, p))
                updates["current_percent"] = p
                log_entries.append(("percent", float(p)))

                # If total_pages is known, also keep current_page in sync when the user updates by percent.
                # Do not override an explicit page/pages_delta update in the same call.
                if page is None and pages_delta is None and "current_page" not in updates:
                    try:
                        total_pages = int(item.get("total_pages")) if item.get("total_pages") is not None else None
                    except (TypeError, ValueError):
                        total_pages = None
                    if total_pages is not None and total_pages > 0:
                        # Round to nearest page; clamp within [0, total_pages]
                        derived_page = int(round((p / 100.0) * float(total_pages)))
                        derived_page = max(0, min(int(total_pages), derived_page))
                        updates["current_page"] = derived_page
                        log_entries.append(("page", float(derived_page)))
            except (TypeError, ValueError):
                pass

        if audio_seconds is not None:
            try:
                a = max(0, int(audio_seconds))
                updates["current_audio_seconds"] = a
                log_entries.append(("audio_seconds", float(a)))
            except (TypeError, ValueError):
                pass

        if not updates and not (isinstance(note, str) and note.strip()):
            return item

        # Deltas for stats (always non-negative)
        if "current_page" in updates:
            try:
                delta = int(updates["current_page"]) - int(prev_page)
                if delta > 0:
                    delta_entries.append(("pages_delta", float(delta)))
            except (TypeError, ValueError):
                pass
        if "current_audio_seconds" in updates:
            try:
                delta = int(updates["current_audio_seconds"]) - int(prev_audio)
                if delta > 0:
                    delta_entries.append(("audio_delta_seconds", float(delta)))
            except (TypeError, ValueError):
                pass

        # Update item row (best-effort; note-only updates should still touch last_update_at)
        set_parts = []
        params: Dict[str, Any] = {"user_id": str(user_id), "item_id": int(item_id)}
        for k, v in updates.items():
            set_parts.append(f"{k} = :{k}")
            params[k] = v

        set_parts.append("last_update_at = CURRENT_TIMESTAMP")
        # If user updates progress on an item, assume it's active again
        if item.get("status") in ("paused", "abandoned"):
            set_parts.append("status = 'reading'")

        query = f"""
        UPDATE reading_items
        SET {", ".join(set_parts)}
        WHERE user_id = :user_id AND id = :item_id
        """
        ok = self._execute_query(query, params, commit=True)
        if not ok:
            return None

        # Log updates
        for kind, value in log_entries:
            self._insert_reading_update(user_id, item_id, kind, value=value, note=note)

        # Log deltas for stats (do not attach note to avoid noisy history)
        for kind, value in delta_entries:
            self._insert_reading_update(user_id, item_id, kind, value=value, note=None)

        # Note-only entry
        if not log_entries and isinstance(note, str) and note.strip():
            self._insert_reading_update(user_id, item_id, "note", value=None, note=note)

        updated_item = self.get_reading_item(user_id, item_id)
        if not updated_item:
            return None

        # Auto-finish detection
        try:
            status = str(updated_item.get("status") or "reading")
        except Exception:
            status = "reading"
        if status != "finished":
            try:
                total_pages = int(updated_item.get("total_pages")) if updated_item.get("total_pages") is not None else None
            except (TypeError, ValueError):
                total_pages = None
            try:
                total_audio = int(updated_item.get("total_audio_seconds")) if updated_item.get("total_audio_seconds") is not None else None
            except (TypeError, ValueError):
                total_audio = None
            try:
                cur_page = int(updated_item.get("current_page") or 0)
            except (TypeError, ValueError):
                cur_page = 0
            try:
                cur_audio = int(updated_item.get("current_audio_seconds") or 0)
            except (TypeError, ValueError):
                cur_audio = 0

            should_finish = False
            if total_pages is not None and total_pages > 0 and cur_page >= total_pages:
                should_finish = True
            if total_audio is not None and total_audio > 0 and cur_audio >= total_audio:
                should_finish = True

            if should_finish:
                # Will also append a "finished" update entry
                self.finish_reading_item(user_id, item_id)
                updated_item = self.get_reading_item(user_id, item_id) or updated_item

        return updated_item

    def finish_reading_item(self, user_id: int, item_id: int) -> bool:
        query = """
        UPDATE reading_items
        SET status = 'finished',
            finished_at = CURRENT_TIMESTAMP,
            last_update_at = CURRENT_TIMESTAMP
        WHERE user_id = :user_id AND id = :item_id
        """
        params = {"user_id": str(user_id), "item_id": int(item_id)}
        ok = self._execute_query(query, params, commit=True)
        if ok:
            self._insert_reading_update(user_id, item_id, "finished", value=1.0, note=None)
        return bool(ok)

    def list_reading_updates(self, user_id: int, item_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        query = """
        SELECT id, item_id, kind, value, note, created_at
        FROM reading_updates
        WHERE user_id = :user_id AND item_id = :item_id
        ORDER BY id DESC
        LIMIT :limit
        """
        params = {"user_id": str(user_id), "item_id": int(item_id), "limit": int(limit)}
        rows = self._execute_query(query, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def import_reading_item(
        self,
        user_id: int,
        title: str,
        *,
        author: Optional[str] = None,
        format: Optional[str] = None,
        status: str = "reading",
        total_pages: Optional[int] = None,
        total_audio_seconds: Optional[int] = None,
        current_page: Optional[int] = None,
        current_audio_seconds: Optional[int] = None,
        started_at_iso: Optional[str] = None,
        finished_at_iso: Optional[str] = None,
    ) -> Optional[int]:
        """
        Import helper that creates a reading item with optional status/timestamps.

        Important: does NOT create pages/audio delta logs (so imports don't inflate stats).
        """
        item_id = self.create_reading_item(
            user_id,
            title,
            author=author,
            format=format,
            total_pages=total_pages,
            total_audio_seconds=total_audio_seconds,
        )
        if not item_id:
            return None

        status_norm = (status or "reading").strip().lower()
        if status_norm not in ("reading", "paused", "finished", "abandoned"):
            status_norm = "reading"

        # Apply progress fields if provided (no logging)
        set_parts: List[str] = []
        params: Dict[str, Any] = {"user_id": str(user_id), "item_id": int(item_id)}
        if current_page is not None:
            try:
                set_parts.append("current_page = :current_page")
                params["current_page"] = max(0, int(current_page))
            except (TypeError, ValueError):
                pass
        if current_audio_seconds is not None:
            try:
                set_parts.append("current_audio_seconds = :current_audio_seconds")
                params["current_audio_seconds"] = max(0, int(current_audio_seconds))
            except (TypeError, ValueError):
                pass

        if started_at_iso and isinstance(started_at_iso, str) and started_at_iso.strip():
            set_parts.append("started_at = :started_at")
            params["started_at"] = started_at_iso.strip()

        # Status/finished timestamp
        set_parts.append("status = :status")
        params["status"] = status_norm

        if status_norm == "finished":
            if finished_at_iso and isinstance(finished_at_iso, str) and finished_at_iso.strip():
                set_parts.append("finished_at = :finished_at")
                params["finished_at"] = finished_at_iso.strip()
            else:
                set_parts.append("finished_at = CURRENT_TIMESTAMP")
            set_parts.append("last_update_at = COALESCE(finished_at, CURRENT_TIMESTAMP)")
        else:
            set_parts.append("last_update_at = CURRENT_TIMESTAMP")

        query = f"""
        UPDATE reading_items
        SET {", ".join(set_parts)}
        WHERE user_id = :user_id AND id = :item_id
        """
        ok = self._execute_query(query, params, commit=True)
        if not ok:
            return item_id

        if status_norm == "finished":
            # Log a finished marker for history (but still no deltas)
            self._insert_reading_update(user_id, item_id, "finished", value=1.0, note="import")

        return item_id

    def list_reading_items_all(self, user_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Lists all reading items for a user (all statuses).
        """
        query = """
        SELECT *
        FROM reading_items
        WHERE user_id = :user_id
        ORDER BY COALESCE(last_update_at, created_at) DESC, id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "limit": int(limit)}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def list_reading_updates_all(self, user_id: int, limit: int = 5000) -> List[Dict[str, Any]]:
        """
        Lists recent reading updates for a user across all items.
        """
        query = """
        SELECT id, item_id, kind, value, note, created_at
        FROM reading_updates
        WHERE user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "limit": int(limit)}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def get_reading_day_totals(self, user_id: int, day_iso: str) -> Dict[str, int]:
        """
        Returns totals for a specific UTC day (YYYY-MM-DD) based on delta logs.
        """
        query = """
        SELECT
            COALESCE(SUM(CASE WHEN kind = 'pages_delta' THEN value END), 0) AS pages,
            COALESCE(SUM(CASE WHEN kind = 'audio_delta_seconds' THEN value END), 0) AS audio_seconds
        FROM reading_updates
        WHERE user_id = :user_id AND date(created_at) = :day
        """
        row = self._execute_query(query, {"user_id": str(user_id), "day": day_iso}, fetch_one=True) or {}
        try:
            pages = int(float(row.get("pages") or 0))
        except (TypeError, ValueError):
            pages = 0
        try:
            audio_seconds = int(float(row.get("audio_seconds") or 0))
        except (TypeError, ValueError):
            audio_seconds = 0
        return {"pages": max(0, pages), "audio_seconds": max(0, audio_seconds)}

    def get_reading_daily_totals(self, user_id: int, days: int = 7) -> List[Dict[str, Any]]:
        """
        Returns per-day totals for the last N days (UTC), inclusive of today.
        Output rows: {day, pages, audio_seconds}
        """
        days = max(1, min(365, int(days)))
        query = """
        WITH RECURSIVE days(day) AS (
            SELECT date('now', 'utc', '-' || (:days - 1) || ' day')
            UNION ALL
            SELECT date(day, '+1 day') FROM days WHERE day < date('now', 'utc')
        )
        SELECT
            d.day AS day,
            COALESCE(SUM(CASE WHEN u.kind = 'pages_delta' THEN u.value END), 0) AS pages,
            COALESCE(SUM(CASE WHEN u.kind = 'audio_delta_seconds' THEN u.value END), 0) AS audio_seconds
        FROM days d
        LEFT JOIN reading_updates u
               ON date(u.created_at) = d.day
              AND u.user_id = :user_id
        GROUP BY d.day
        ORDER BY d.day ASC
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "days": days}, fetch_all=True)
        out: List[Dict[str, Any]] = []
        for r in (rows or []):
            if not isinstance(r, dict):
                continue
            day = r.get("day")
            try:
                pages = int(float(r.get("pages") or 0))
            except (TypeError, ValueError):
                pages = 0
            try:
                audio_seconds = int(float(r.get("audio_seconds") or 0))
            except (TypeError, ValueError):
                audio_seconds = 0
            out.append({"day": day, "pages": max(0, pages), "audio_seconds": max(0, audio_seconds)})
        return out

    def get_reading_range_totals(self, user_id: int, start_day_iso: str, end_day_iso: str) -> Dict[str, int]:
        """
        Returns totals for an inclusive day range (UTC) based on delta logs.
        """
        query = """
        SELECT
            COALESCE(SUM(CASE WHEN kind = 'pages_delta' THEN value END), 0) AS pages,
            COALESCE(SUM(CASE WHEN kind = 'audio_delta_seconds' THEN value END), 0) AS audio_seconds
        FROM reading_updates
        WHERE user_id = :user_id
          AND date(created_at) >= :start_day
          AND date(created_at) <= :end_day
        """
        row = self._execute_query(
            query,
            {"user_id": str(user_id), "start_day": start_day_iso, "end_day": end_day_iso},
            fetch_one=True,
        ) or {}
        try:
            pages = int(float(row.get("pages") or 0))
        except (TypeError, ValueError):
            pages = 0
        try:
            audio_seconds = int(float(row.get("audio_seconds") or 0))
        except (TypeError, ValueError):
            audio_seconds = 0
        return {"pages": max(0, pages), "audio_seconds": max(0, audio_seconds)}

    # --- Games Tracking ---
