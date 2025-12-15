import sqlite3
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class BooksMixin:
    def add_book_author_subscription(
        self,
        guild_id: int,
        user_id: int,
        author_id: str,
        author_name: Optional[str] = None,
        channel_id: Optional[int] = None,
    ) -> bool:
        query = """
        INSERT OR IGNORE INTO book_author_subscriptions
            (guild_id, user_id, author_id, author_name, channel_id)
        VALUES (:guild_id, :user_id, :author_id, :author_name, :channel_id)
        """
        params = {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "author_id": author_id,
            "author_name": author_name,
            "channel_id": str(channel_id) if channel_id is not None else None,
        }
        return self._execute_query(query, params, commit=True)

    def remove_book_author_subscription(self, guild_id: int, user_id: int, author_id: str) -> bool:
        """
        Deletes a subscription and returns True only if a row was actually removed.
        """
        query = """
        DELETE FROM book_author_subscriptions
        WHERE guild_id = :guild_id AND user_id = :user_id AND author_id = :author_id
        """
        params = {"guild_id": str(guild_id), "user_id": str(user_id), "author_id": author_id}
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
                logger.error(f"remove_book_author_subscription failed: {e}")
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

    def update_book_author_subscription_name(self, guild_id: int, user_id: int, author_id: str, author_name: str) -> bool:
        """
        Updates the stored author_name for an existing subscription.
        """
        query = """
        UPDATE book_author_subscriptions
        SET author_name = :author_name
        WHERE guild_id = :guild_id AND user_id = :user_id AND author_id = :author_id
        """
        params = {
            "author_name": author_name,
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "author_id": author_id,
        }
        return self._execute_query(query, params, commit=True)

    def get_user_book_author_subscriptions(self, guild_id: int, user_id: int) -> List[Dict[str, Any]]:
        query = """
        SELECT author_id, author_name, channel_id, created_at
        FROM book_author_subscriptions
        WHERE guild_id = :guild_id AND user_id = :user_id
        ORDER BY author_name COLLATE NOCASE
        """
        params = {"guild_id": str(guild_id), "user_id": str(user_id)}
        return self._execute_query(query, params, fetch_all=True)

    def get_all_book_author_subscriptions(self) -> List[Dict[str, Any]]:
        query = """
        SELECT guild_id, user_id, author_id, author_name, channel_id, created_at
        FROM book_author_subscriptions
        """
        return self._execute_query(query, fetch_all=True)

    def get_book_author_subscriptions_for_author(self, author_id: str) -> List[Dict[str, Any]]:
        query = """
        SELECT guild_id, user_id, author_id, author_name, channel_id
        FROM book_author_subscriptions
        WHERE author_id = :author_id
        """
        return self._execute_query(query, {"author_id": author_id}, fetch_all=True)

    def get_seen_work_ids_for_author(self, author_id: str) -> List[str]:
        query = "SELECT work_id FROM book_author_seen_works WHERE author_id = :author_id"
        rows = self._execute_query(query, {"author_id": author_id}, fetch_all=True)
        out: List[str] = []
        for r in rows:
            wid = r.get("work_id")
            if isinstance(wid, str):
                out.append(wid)
        return out

    def mark_author_work_seen(self, author_id: str, work_id: str) -> bool:
        query = """
        INSERT OR IGNORE INTO book_author_seen_works (author_id, work_id)
        VALUES (:author_id, :work_id)
        """
        return self._execute_query(query, {"author_id": author_id, "work_id": work_id}, commit=True)

    def mark_author_works_seen(self, author_id: str, work_ids: List[str]) -> bool:
        """
        Best-effort bulk insert. Returns True if the operation completes.
        """
        if not work_ids:
            return True
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.executemany(
                    "INSERT OR IGNORE INTO book_author_seen_works (author_id, work_id) VALUES (?, ?)",
                    [(author_id, wid) for wid in work_ids],
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error(f"Bulk mark_author_works_seen failed: {e}")
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

    def get_seen_work_ids_for_user_author(self, user_id: int, author_id: str) -> List[str]:
        query = """
        SELECT work_id
        FROM book_author_user_seen_works
        WHERE user_id = :user_id AND author_id = :author_id
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "author_id": author_id}, fetch_all=True)
        out: List[str] = []
        for r in rows:
            wid = r.get("work_id")
            if isinstance(wid, str):
                out.append(wid)
        return out

    def mark_user_author_work_seen(self, user_id: int, author_id: str, work_id: str) -> bool:
        query = """
        INSERT OR IGNORE INTO book_author_user_seen_works (user_id, author_id, work_id)
        VALUES (:user_id, :author_id, :work_id)
        """
        params = {"user_id": str(user_id), "author_id": author_id, "work_id": work_id}
        return self._execute_query(query, params, commit=True)

    def mark_user_author_works_seen(self, user_id: int, author_id: str, work_ids: List[str]) -> bool:
        """
        Best-effort bulk insert. Returns True if the operation completes.
        """
        if not work_ids:
            return True
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.executemany(
                    "INSERT OR IGNORE INTO book_author_user_seen_works (user_id, author_id, work_id) VALUES (?, ?, ?)",
                    [(str(user_id), author_id, wid) for wid in work_ids],
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error(f"Bulk mark_user_author_works_seen failed: {e}")
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

    # --- Reading Progress ---
