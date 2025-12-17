import sqlite3
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class GamesMixin:
    def get_game_item_by_steam_appid(self, user_id: int, steam_appid: int) -> Optional[Dict[str, Any]]:
        """
        Returns an existing game item for this user by Steam appid (if any).
        """
        try:
            appid = int(steam_appid)
        except (TypeError, ValueError):
            return None

        query = """
        SELECT *
        FROM game_items
        WHERE user_id = :user_id AND steam_appid = :steam_appid
        ORDER BY id DESC
        LIMIT 1
        """
        row = self._execute_query(query, {"user_id": str(user_id), "steam_appid": int(appid)}, fetch_one=True)
        if not isinstance(row, dict):
            return None
        if row.get("genres"):
            try:
                row["genres"] = json.loads(row["genres"])
            except Exception:
                pass
        return row

    def get_game_item_by_title(self, user_id: int, title: str) -> Optional[Dict[str, Any]]:
        """
        Returns an existing game item for this user by exact stored title (case-insensitive).
        """
        t = str(title or "").strip()
        if not t:
            return None
        query = """
        SELECT *
        FROM game_items
        WHERE user_id = :user_id AND LOWER(title) = LOWER(:title)
        ORDER BY id DESC
        LIMIT 1
        """
        row = self._execute_query(query, {"user_id": str(user_id), "title": t}, fetch_one=True)
        if not isinstance(row, dict):
            return None
        if row.get("genres"):
            try:
                row["genres"] = json.loads(row["genres"])
            except Exception:
                pass
        return row

    def create_game_item(
        self,
        user_id: int,
        title: str,
        *,
        status: str = "backlog",
        platform: Optional[str] = None,
        steam_appid: Optional[int] = None,
        steam_url: Optional[str] = None,
        cover_url: Optional[str] = None,
        release_date: Optional[str] = None,
        genres: Optional[list] = None,
        developer: Optional[str] = None,
        publisher: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        """
        Creates a new game item and returns its ID.
        """
        if not title or not str(title).strip():
            return None

        # Duplicate guard (best-effort): avoid double-adding the same Steam app,
        # and avoid exact duplicate titles for manual entries.
        if steam_appid is not None:
            try:
                existing = self.get_game_item_by_steam_appid(user_id, int(steam_appid))
                if existing and existing.get("id") is not None:
                    return int(existing["id"])
            except Exception:
                pass
        else:
            try:
                existing_t = self.get_game_item_by_title(user_id, str(title))
                if existing_t and existing_t.get("id") is not None:
                    return int(existing_t["id"])
            except Exception:
                pass

        status_norm = (status or "backlog").strip().lower()
        if status_norm not in ("backlog", "playing", "paused", "completed", "dropped"):
            status_norm = "backlog"

        genres_json = None
        if genres is not None:
            try:
                genres_json = json.dumps(genres)
            except Exception:
                genres_json = None

        query = """
        INSERT INTO game_items
            (user_id, title, status, platform, steam_appid, steam_url, cover_url, release_date, genres, developer, publisher, notes, last_update_at, started_at, finished_at)
        VALUES
            (:user_id, :title, :status, :platform, :steam_appid, :steam_url, :cover_url, :release_date, :genres, :developer, :publisher, :notes,
             CURRENT_TIMESTAMP,
             CASE WHEN :status = 'playing' THEN CURRENT_TIMESTAMP ELSE NULL END,
             CASE WHEN :status = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END)
        """
        params = {
            "user_id": str(user_id),
            "title": str(title).strip(),
            "status": status_norm,
            "platform": platform.strip() if isinstance(platform, str) and platform.strip() else None,
            "steam_appid": int(steam_appid) if steam_appid is not None else None,
            "steam_url": steam_url.strip() if isinstance(steam_url, str) and steam_url.strip() else None,
            "cover_url": cover_url.strip() if isinstance(cover_url, str) and cover_url.strip() else None,
            "release_date": release_date.strip() if isinstance(release_date, str) and release_date.strip() else None,
            "genres": genres_json,
            "developer": developer.strip() if isinstance(developer, str) and developer.strip() else None,
            "publisher": publisher.strip() if isinstance(publisher, str) and publisher.strip() else None,
            "notes": notes.strip() if isinstance(notes, str) and notes.strip() else None,
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
                logger.error(f"create_game_item failed: {e}")
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

    def get_game_item(self, user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT *
        FROM game_items
        WHERE user_id = :user_id AND id = :item_id
        """
        row = self._execute_query(query, {"user_id": str(user_id), "item_id": int(item_id)}, fetch_one=True)
        if not isinstance(row, dict):
            return None
        # Decode genres JSON if present
        if row.get("genres"):
            try:
                row["genres"] = json.loads(row["genres"])
            except Exception:
                pass
        return row

    def list_game_items(
        self,
        user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        statuses = statuses or ["backlog", "playing", "paused"]
        statuses = [str(s).strip().lower() for s in statuses if isinstance(s, str) and s.strip()]
        if not statuses:
            statuses = ["backlog", "playing", "paused"]
        placeholders = ", ".join([f":s{i}" for i in range(len(statuses))])
        query = f"""
        SELECT *
        FROM game_items
        WHERE user_id = :user_id AND status IN ({placeholders})
        ORDER BY
            CASE status
                WHEN 'playing' THEN 0
                WHEN 'paused' THEN 1
                WHEN 'backlog' THEN 2
                WHEN 'completed' THEN 3
                WHEN 'dropped' THEN 4
                ELSE 5
            END,
            COALESCE(last_update_at, created_at) DESC,
            id DESC
        LIMIT :limit
        """
        params: Dict[str, Any] = {"user_id": str(user_id), "limit": int(limit)}
        for i, s in enumerate(statuses):
            params[f"s{i}"] = s
        rows = self._execute_query(query, params, fetch_all=True)
        out = rows if isinstance(rows, list) else []
        for r in out:
            if isinstance(r, dict) and r.get("genres"):
                try:
                    r["genres"] = json.loads(r["genres"])
                except Exception:
                    pass
        return out

    def list_game_items_all(self, user_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        query = """
        SELECT *
        FROM game_items
        WHERE user_id = :user_id
        ORDER BY COALESCE(last_update_at, created_at) DESC, id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "limit": int(limit)}, fetch_all=True)
        out = rows if isinstance(rows, list) else []
        for r in out:
            if isinstance(r, dict) and r.get("genres"):
                try:
                    r["genres"] = json.loads(r["genres"])
                except Exception:
                    pass
        return out

    def delete_game_item(self, user_id: int, item_id: int) -> bool:
        query = "DELETE FROM game_items WHERE user_id = :user_id AND id = :item_id"
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, {"user_id": str(user_id), "item_id": int(item_id)})
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_game_item failed: {e}")
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

    def update_game_notes(self, user_id: int, item_id: int, notes: Optional[str]) -> bool:
        query = """
        UPDATE game_items
        SET notes = :notes,
            last_update_at = CURRENT_TIMESTAMP
        WHERE user_id = :user_id AND id = :item_id
        """
        params = {
            "user_id": str(user_id),
            "item_id": int(item_id),
            "notes": notes.strip() if isinstance(notes, str) and notes.strip() else None,
        }
        return bool(self._execute_query(query, params, commit=True))

    def update_game_status(self, user_id: int, item_id: int, status: str) -> bool:
        status_norm = (status or "").strip().lower()
        if status_norm not in ("backlog", "playing", "paused", "completed", "dropped"):
            return False

        # Preserve existing started_at unless moving to playing for the first time.
        # Set finished_at when completed; clear finished_at if moved away from completed.
        query = """
        UPDATE game_items
        SET status = :status,
            last_update_at = CURRENT_TIMESTAMP,
            started_at = CASE
                WHEN :status = 'playing' AND started_at IS NULL THEN CURRENT_TIMESTAMP
                ELSE started_at
            END,
            finished_at = CASE
                WHEN :status = 'completed' THEN COALESCE(finished_at, CURRENT_TIMESTAMP)
                WHEN :status != 'completed' THEN NULL
                ELSE finished_at
            END
        WHERE user_id = :user_id AND id = :item_id
        """
        params = {"user_id": str(user_id), "item_id": int(item_id), "status": status_norm}
        return bool(self._execute_query(query, params, commit=True))

    def set_current_game_item_id(self, user_id: int, item_id: Optional[int]) -> bool:
        if item_id is None:
            return self.delete_user_preference(user_id, "games_current_item_id")
        return self.set_user_preference(user_id, "games_current_item_id", int(item_id))

    def get_current_game_item_id(self, user_id: int) -> Optional[int]:
        item_id = self.get_user_preference(user_id, "games_current_item_id", None)
        if item_id is None:
            return None
        try:
            return int(item_id)
        except (TypeError, ValueError):
            return None

    def get_current_game_item(self, user_id: int) -> Optional[Dict[str, Any]]:
        preferred_id = self.get_current_game_item_id(user_id)
        if preferred_id is not None:
            item = self.get_game_item(user_id, preferred_id)
            if item:
                return item
        # fallback: latest active
        items = self.list_game_items(user_id, statuses=["playing", "paused", "backlog"], limit=1)
        return items[0] if items else None

#
# NOTE:
# This module is a mixin only. Keep it import-safe (no standalone run logic here).
