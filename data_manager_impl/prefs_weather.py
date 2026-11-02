import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class PrefsWeatherMixin:
    def get_user_preference(self, user_id: int, key: str, default: Any = None) -> Any:
        user_id_str = str(user_id)
        query = "SELECT pref_value FROM user_preferences WHERE user_id = :user_id AND pref_key = :key"
        params = {"user_id": user_id_str, "key": key}
        result = self._execute_query(query, params, fetch_one=True)
        if result and result.get('pref_value'):
            try:
                value_str = result['pref_value']
                # SQLite returns TEXT directly, no LOB handling needed
                return json.loads(value_str)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding preference value for user {user_id_str}, key {key}: {e}")
                return default
        return default

    def set_user_preference(self, user_id: int, key: str, value: Any) -> bool:
        user_id_str = str(user_id)
        value_json = json.dumps(value)
        query = """
        INSERT INTO user_preferences (user_id, pref_key, pref_value)
        VALUES (:user_id, :key, :value_json)
        ON CONFLICT(user_id, pref_key) DO UPDATE SET
            pref_value = :value_json
        """
        params = {"user_id": user_id_str, "key": key, "value_json": value_json}
        return self._execute_query(query, params, commit=True)

    def delete_user_preference(self, user_id: int, key: str) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM user_preferences WHERE user_id = :user_id AND pref_key = :key"
        params = {"user_id": user_id_str, "key": key}
        return self._execute_query(query, params, commit=True)

    def get_user_all_preferences(self, user_id: int) -> Dict[str, Any]:
        user_id_str = str(user_id)
        query = "SELECT pref_key, pref_value FROM user_preferences WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        prefs_list = self._execute_query(query, params, fetch_all=True)
        
        user_prefs = {}
        for pref_row in prefs_list:
            key = pref_row['pref_key']
            try:
                value_str = pref_row['pref_value']
                # SQLite returns TEXT directly
                user_prefs[key] = json.loads(value_str)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding preference value for user {user_id_str}, key {key} in get_all_preferences: {e}")
                user_prefs[key] = None # Or some default error indicator
        return user_prefs

    def list_users_with_preference(self, key: str) -> List[Dict[str, Any]]:
        """
        Returns rows of {user_id, pref_value} for a given preference key.
        """
        query = "SELECT user_id, pref_value FROM user_preferences WHERE pref_key = :key"
        rows = self._execute_query(query, {"key": key}, fetch_all=True)
        out: List[Dict[str, Any]] = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            uid = r.get("user_id")
            pv = r.get("pref_value")
            if not isinstance(uid, str):
                continue
            # Best-effort JSON decode; keep raw string if invalid
            val: Any
            if isinstance(pv, str) and pv:
                try:
                    val = json.loads(pv)
                except json.JSONDecodeError:
                    val = pv
            else:
                val = None
            out.append({"user_id": uid, "value": val})
        return out

    def get_user_id_for_preference_value(self, key: str, value: Any) -> Optional[str]:
        """
        Returns the user_id that has pref_key=key and pref_value=value (JSON-encoded).
        """
        value_json = json.dumps(value)
        query = "SELECT user_id FROM user_preferences WHERE pref_key = :key AND pref_value = :value_json"
        params = {"key": key, "value_json": value_json}
        result = self._execute_query(query, params, fetch_one=True)
        if result and isinstance(result.get("user_id"), str):
            return result["user_id"]

        # If checking report_webhook_token, also check multi-webhook dictionaries in report_webhooks
        if key == "report_webhook_token" and isinstance(value, str):
            return self.get_user_id_for_report_token(value)

        return None

    def get_user_webhooks(self, user_id: Any) -> Dict[str, str]:
        """
        Returns a dict of {name: token} for the user.
        Preserves and migrates legacy 'report_webhook_token' as 'default'.
        """
        user_id_str = str(user_id)
        raw_webhooks = self.get_user_preference(user_id_str, "report_webhooks", None)
        webhooks: Dict[str, str] = {}
        if isinstance(raw_webhooks, dict):
            for k, v in raw_webhooks.items():
                if isinstance(v, str):
                    webhooks[str(k).lower()] = v
                elif isinstance(v, dict) and "token" in v:
                    webhooks[str(k).lower()] = str(v["token"])

        legacy_token = self.get_user_preference(user_id_str, "report_webhook_token", None)
        if legacy_token and "default" not in webhooks:
            webhooks["default"] = str(legacy_token)
            self.set_user_preference(user_id_str, "report_webhooks", webhooks)

        return webhooks

    def set_user_webhook(self, user_id: Any, name: str, token: str) -> None:
        """
        Sets or updates a named webhook token for the user.
        If name is 'default', also updates 'report_webhook_token' for backward compatibility.
        """
        user_id_str = str(user_id)
        name_clean = (name or "default").strip().lower()
        webhooks = self.get_user_webhooks(user_id_str)
        webhooks[name_clean] = token
        self.set_user_preference(user_id_str, "report_webhooks", webhooks)
        if name_clean == "default":
            self.set_user_preference(user_id_str, "report_webhook_token", token)

    def delete_user_webhook(self, user_id: Any, name: str) -> bool:
        """
        Deletes a named webhook. Returns True if deleted, False if not found.
        """
        user_id_str = str(user_id)
        name_clean = (name or "default").strip().lower()
        webhooks = self.get_user_webhooks(user_id_str)
        if name_clean in webhooks:
            del webhooks[name_clean]
            self.set_user_preference(user_id_str, "report_webhooks", webhooks)
            if name_clean == "default":
                self.delete_user_preference(user_id_str, "report_webhook_token")
            return True
        return False

    def get_user_id_for_report_token(self, token: str) -> Optional[str]:
        """
        Finds the user_id that owns the given webhook token.
        Checks both legacy 'report_webhook_token' and multi-token 'report_webhooks'.
        """
        if not token:
            return None

        # 1. Fast path: check single legacy preference directly via SQL
        value_json = json.dumps(token)
        query = "SELECT user_id FROM user_preferences WHERE pref_key = 'report_webhook_token' AND pref_value = :value_json"
        result = self._execute_query(query, {"value_json": value_json}, fetch_one=True)
        if result and isinstance(result.get("user_id"), str):
            return result["user_id"]

        # 2. Check multi-token dictionaries in report_webhooks
        query = "SELECT user_id, pref_value FROM user_preferences WHERE pref_key = 'report_webhooks'"
        rows = self._execute_query(query, fetch_all=True) or []
        for row in rows:
            try:
                raw = row.get("pref_value")
                if not raw:
                    continue
                data = json.loads(raw)
                if isinstance(data, dict):
                    for v in data.values():
                        tok = v.get("token") if isinstance(v, dict) else v
                        if tok == token:
                            return str(row["user_id"])
            except Exception:
                continue
        return None

    # --- Currency Rates ---
    def update_currency_rate(self, currency_pair: str, rate: float) -> bool:
        query = """
        INSERT INTO currency_rates (currency_pair, rate, last_updated)
        VALUES (:pair, :rate, CURRENT_TIMESTAMP)
        ON CONFLICT(currency_pair) DO UPDATE SET
            rate = :rate,
            last_updated = CURRENT_TIMESTAMP
        """
        params = {"pair": currency_pair, "rate": rate}
        return self._execute_query(query, params, commit=True)

    def get_currency_rate(self, currency_pair: str) -> Optional[float]:
        query = "SELECT rate FROM currency_rates WHERE currency_pair = :pair"
        params = {"pair": currency_pair}
        result = self._execute_query(query, params, fetch_one=True)
        if result:
            return float(result['rate'])
        return None

    # --- Weather Schedules ---
    def add_weather_schedule(self, user_id: int, schedule_time: str, location: Optional[str] = None) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT INTO weather_schedules (user_id, schedule_time, location)
        VALUES (:user_id, :time, :location)
        ON CONFLICT(user_id, schedule_time) DO UPDATE SET
            location = :location
        """
        params = {"user_id": user_id_str, "time": schedule_time, "location": location}
        return self._execute_query(query, params, commit=True)

    def remove_weather_schedule(self, user_id: int, schedule_time: str) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM weather_schedules WHERE user_id = :user_id AND schedule_time = :time"
        params = {"user_id": user_id_str, "time": schedule_time}
        return self._execute_query(query, params, commit=True)

    def clear_weather_schedules(self, user_id: int) -> bool:
        """
        Remove ALL scheduled weather notifications for a user.
        """
        user_id_str = str(user_id)
        query = "DELETE FROM weather_schedules WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        return self._execute_query(query, params, commit=True)

    def get_user_weather_schedules(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT schedule_time, location FROM weather_schedules WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        return self._execute_query(query, params, fetch_all=True)

    def get_weather_schedules_for_time(self, schedule_time: str) -> List[Dict[str, Any]]:
        query = "SELECT user_id, location FROM weather_schedules WHERE schedule_time = :time"
        params = {"time": schedule_time}
        return self._execute_query(query, params, fetch_all=True)

