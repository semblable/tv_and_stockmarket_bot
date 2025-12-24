import json
import logging
from typing import List, Dict, Any, Optional, Union, Tuple

logger = logging.getLogger(__name__)


class MediaMixin:
    def add_tv_show_subscription(self, user_id: int, show_tmdb_id: int, show_name: str, poster_path: str, show_tvmaze_id: Optional[int] = None) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT OR IGNORE INTO tv_subscriptions (user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details, show_tvmaze_id)
        VALUES (:user_id, :show_tmdb_id, :show_name, :poster_path, NULL, :show_tvmaze_id)
        """
        params = {
            "user_id": user_id_str, "show_tmdb_id": show_tmdb_id,
            "show_name": show_name, "poster_path": poster_path,
            "show_tvmaze_id": show_tvmaze_id
        }
        return self._execute_query(query, params, commit=True)

    def update_tv_subscription_tvmaze_id(self, user_id: int, show_tmdb_id: int, show_tvmaze_id: int) -> bool:
        user_id_str = str(user_id)
        query = """
        UPDATE tv_subscriptions
        SET show_tvmaze_id = :show_tvmaze_id
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "show_tvmaze_id": show_tvmaze_id
        }
        return self._execute_query(query, params, commit=True)

    def remove_tv_show_subscription(self, user_id: int, show_tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM tv_subscriptions WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id"
        params = {"user_id": user_id_str, "show_tmdb_id": show_tmdb_id}
        result = self._execute_query(query, params, commit=True)
        # Check if any row was actually deleted
        # The _execute_query for commit=True returns True on success, not rowcount.
        # To confirm deletion, we'd need to query before/after or check cursor.rowcount before commit.
        # For now, assume success if query executes. A more robust check might be needed.
        return result if isinstance(result, bool) else False


    def get_user_tv_subscriptions(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details, show_tvmaze_id FROM tv_subscriptions WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        subscriptions = self._execute_query(query, params, fetch_all=True)
        for sub in subscriptions:
            if sub.get('last_notified_episode_details'):
                try:
                    # SQLite returns TEXT directly, no LOB handling needed
                    details_str = sub['last_notified_episode_details']
                    sub['last_notified_episode_details'] = json.loads(details_str) if details_str else None
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding last_notified_episode_details for user {user_id_str}, show_tmdb_id {sub.get('show_tmdb_id')}: {e}")
                    sub['last_notified_episode_details'] = None
        return subscriptions

    def get_all_tv_subscriptions(self) -> Dict[str, List[Dict[str, Any]]]:
        query = "SELECT user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details, show_tvmaze_id FROM tv_subscriptions"
        subscriptions = self._execute_query(query, fetch_all=True)
        for sub in subscriptions:
            if sub.get('last_notified_episode_details'):
                try:
                    # SQLite returns TEXT directly
                    details_str = sub['last_notified_episode_details']
                    sub['last_notified_episode_details'] = json.loads(details_str) if details_str else None
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding last_notified_episode_details for show_tmdb_id {sub.get('show_tmdb_id')} in get_all: {e}")
                    sub['last_notified_episode_details'] = None
        # The old method returned a dict keyed by user_id. Let's try to match that.
        result_dict: Dict[str, List[Dict[str, Any]]] = {}
        for sub in subscriptions:
            uid = sub['user_id']
            if uid not in result_dict:
                result_dict[uid] = []
            # Remove user_id from the sub-dictionary as it's now the key
            entry = {k: v for k, v in sub.items() if k != 'user_id'}
            result_dict[uid].append(entry)
        return result_dict


    def update_last_notified_episode_details(self, user_id: int, show_tmdb_id: int, episode_details: Optional[Dict[str, Any]]) -> bool:
        user_id_str = str(user_id)
        # Serialize the episode_details dict to a JSON string for storage
        details_json = json.dumps(episode_details) if episode_details else None
        query = """
        UPDATE tv_subscriptions 
        SET last_notified_episode_details = :details
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id
        """
        params = {"details": details_json, "user_id": user_id_str, "show_tmdb_id": show_tmdb_id}
        return self._execute_query(query, params, commit=True)

    # --- Sent Episode Notifications ---
    def _normalize_episode_notification_id(self, episode_id: Any) -> Tuple[Optional[Union[int, str]], Optional[int]]:
        """
        Returns:
          - normalized_id: int|str|None (what we store/query as primary episode id)
          - legacy_int: int|None (parsed numeric suffix for backwards-compatible lookup)
        """
        if episode_id is None:
            return None, None
        # Keep ints as-is.
        if isinstance(episode_id, int):
            return int(episode_id), int(episode_id)
        # Allow storing/querying provider-prefixed ids like "tvmaze:12345".
        try:
            s = str(episode_id).strip()
        except Exception:
            return None, None
        if not s:
            return None, None
        legacy_int: Optional[int] = None
        if ":" in s:
            # Parse last segment as int if possible (e.g. "tvmaze:123").
            try:
                legacy_int = int(s.rsplit(":", 1)[-1])
            except Exception:
                legacy_int = None
        else:
            try:
                legacy_int = int(s)
            except Exception:
                legacy_int = None
        return s, legacy_int

    def add_sent_episode_notification(self, user_id: int, show_tmdb_id: int, episode_tmdb_id: Any, season_number: int, episode_number: int) -> bool:
        user_id_str = str(user_id)
        normalized_id, _legacy = self._normalize_episode_notification_id(episode_tmdb_id)
        if normalized_id is None:
            return False
        query = """
        INSERT OR IGNORE INTO sent_episode_notifications 
            (user_id, show_tmdb_id, episode_tmdb_id, season_number, episode_number)
        VALUES (:user_id, :show_tmdb_id, :episode_tmdb_id, :season_number, :episode_number)
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "episode_tmdb_id": normalized_id,
            "season_number": season_number,
            "episode_number": episode_number
        }
        return self._execute_query(query, params, commit=True)

    def has_user_been_notified_for_episode(self, user_id: int, show_tmdb_id: int, episode_tmdb_id: Any) -> bool:
        user_id_str = str(user_id)
        normalized_id, legacy_int = self._normalize_episode_notification_id(episode_tmdb_id)
        if normalized_id is None:
            return False
        query = """
        SELECT 1 FROM sent_episode_notifications
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id AND episode_tmdb_id = :episode_tmdb_id
        LIMIT 1
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "episode_tmdb_id": normalized_id
        }
        result = self._execute_query(query, params, fetch_one=True)
        if result:
            return True

        # Backwards-compat: older rows stored raw integer ids (no provider prefix).
        if legacy_int is not None:
            result2 = self._execute_query(
                """
                SELECT 1 FROM sent_episode_notifications
                WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id AND episode_tmdb_id = :legacy_id
                LIMIT 1
                """,
                {"user_id": user_id_str, "show_tmdb_id": show_tmdb_id, "legacy_id": legacy_int},
                fetch_one=True,
            )
            return bool(result2)
        return False

    def has_user_been_notified_for_episode_by_number(self, user_id: int, show_tmdb_id: int, season_number: int, episode_number: int) -> bool:
        """
        Checks if a user has been notified for an episode based on season/episode number.
        Useful for robustness across different data providers (TMDB vs TVMaze) where IDs might differ.
        """
        user_id_str = str(user_id)
        query = """
        SELECT 1 FROM sent_episode_notifications
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id 
          AND season_number = :season_number AND episode_number = :episode_number
        LIMIT 1
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "season_number": season_number,
            "episode_number": episode_number
        }
        result = self._execute_query(query, params, fetch_one=True)
        return bool(result)

    # --- Movie Subscriptions ---
    def add_movie_subscription(self, user_id: int, tmdb_id: int, title: str, poster_path: str) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT OR IGNORE INTO movie_subscriptions (user_id, tmdb_id, title, poster_path, notified_status)
        VALUES (:user_id, :tmdb_id, :title, :poster_path, 0)
        """
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id, "title": title, "poster_path": poster_path}
        return self._execute_query(query, params, commit=True)

    def remove_movie_subscription(self, user_id: int, tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM movie_subscriptions WHERE user_id = :user_id AND tmdb_id = :tmdb_id"
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id}
        return self._execute_query(query, params, commit=True)

    def get_user_movie_subscriptions(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT user_id, tmdb_id, title, poster_path, notified_status FROM movie_subscriptions WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        subscriptions = self._execute_query(query, params, fetch_all=True)
        for sub in subscriptions: # Convert 0/1 to False/True
            sub['notified_status'] = bool(sub.get('notified_status', 0))
        return subscriptions

    def get_all_movie_subscriptions(self) -> Dict[str, List[Dict[str, Any]]]:
        query = "SELECT user_id, tmdb_id, title, poster_path, notified_status FROM movie_subscriptions"
        subscriptions = self._execute_query(query, fetch_all=True)
        result_dict: Dict[str, List[Dict[str, Any]]] = {}
        for sub in subscriptions:
            sub['notified_status'] = bool(sub.get('notified_status', 0))
            uid = sub['user_id']
            if uid not in result_dict:
                result_dict[uid] = []
            entry = {k: v for k, v in sub.items() if k != 'user_id'}
            result_dict[uid].append(entry)
        return result_dict


    def update_movie_notified_status(self, user_id: int, tmdb_id: int, status: bool) -> bool:
        user_id_str = str(user_id)
        query = "UPDATE movie_subscriptions SET notified_status = :status WHERE user_id = :user_id AND tmdb_id = :tmdb_id"
        params = {"status": 1 if status else 0, "user_id": user_id_str, "tmdb_id": tmdb_id}
        return self._execute_query(query, params, commit=True)

    # --- Tracked Stocks ---
