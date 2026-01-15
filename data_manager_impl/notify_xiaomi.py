import secrets
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NotifyXiaomiMixin:
    """
    Per-user webhook mapping for health data ingestion (Notify for Xiaomi / Tasker).

    Security model:
    - Each Discord user gets a unique random token.
    - Incoming POSTs to /webhook/xiaomi/<token> are mapped to the user_id.
    - Token must be treated as a secret (anyone with it can post data for that user).
    """

    # ---- Read helpers ----
    def get_xiaomi_webhook(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self._execute_query(
            """
            SELECT user_id, token, enabled, created_at, last_seen_at
            FROM xiaomi_webhooks
            WHERE user_id = :user_id
            """,
            params={"user_id": str(user_id)},
            fetch_one=True,
        )

    def get_xiaomi_user_id_by_token(self, token: str) -> Optional[str]:
        row = self._execute_query(
            """
            SELECT user_id
            FROM xiaomi_webhooks
            WHERE token = :token AND enabled = 1
            """,
            params={"token": str(token)},
            fetch_one=True,
        )
        if not row:
            return None
        return str(row.get("user_id")) if row.get("user_id") is not None else None

    # ---- Write helpers ----
    def create_or_get_xiaomi_webhook(self, user_id: int) -> Dict[str, Any]:
        existing = self.get_xiaomi_webhook(user_id)
        if existing and existing.get("token"):
            return existing

        token = secrets.token_urlsafe(32)
        ok = self._execute_query(
            """
            INSERT OR REPLACE INTO xiaomi_webhooks (user_id, token, enabled)
            VALUES (:user_id, :token, 1)
            """,
            params={"user_id": str(user_id), "token": token},
            commit=True,
        )
        if not ok:
            raise RuntimeError("Failed to create Xiaomi webhook token")
        return self.get_xiaomi_webhook(user_id) or {"user_id": str(user_id), "token": token, "enabled": 1}

    def rotate_xiaomi_webhook(self, user_id: int) -> Dict[str, Any]:
        token = secrets.token_urlsafe(32)
        ok = self._execute_query(
            """
            INSERT OR REPLACE INTO xiaomi_webhooks (user_id, token, enabled, created_at, last_seen_at, last_payload)
            VALUES (:user_id, :token, 1, CURRENT_TIMESTAMP, NULL, NULL)
            """,
            params={"user_id": str(user_id), "token": token},
            commit=True,
        )
        if not ok:
            raise RuntimeError("Failed to rotate Xiaomi webhook token")
        return self.get_xiaomi_webhook(user_id) or {"user_id": str(user_id), "token": token, "enabled": 1}

    def set_xiaomi_webhook_enabled(self, user_id: int, enabled: bool) -> bool:
        return bool(
            self._execute_query(
                """
                UPDATE xiaomi_webhooks
                SET enabled = :enabled
                WHERE user_id = :user_id
                """,
                params={"user_id": str(user_id), "enabled": 1 if enabled else 0},
                commit=True,
            )
        )

    def touch_xiaomi_webhook_last_seen(self, user_id: str, payload: Dict[str, Any]) -> bool:
        # Store only a compact JSON string for debugging; keep it small-ish.
        try:
            payload_text = json.dumps(payload, ensure_ascii=False)[:50000]
        except Exception:
            payload_text = None

        return bool(
            self._execute_query(
                """
                UPDATE xiaomi_webhooks
                SET last_seen_at = CURRENT_TIMESTAMP,
                    last_payload = :payload
                WHERE user_id = :user_id
                """,
                params={"user_id": str(user_id), "payload": payload_text},
                commit=True,
            )
        )

    # ---- Sleep CSV ingestion ----
    def replace_xiaomi_sleep_entries(self, user_id: int, entries: List[Dict[str, Any]]) -> int:
        """
        Replace sleep entries for the dates present in `entries`.
        Returns number of rows inserted.
        """
        uid = str(int(user_id))
        if not entries:
            return 0

        dates = sorted({str(e.get("sleep_date") or "").strip() for e in entries if e.get("sleep_date")})
        dates = [d for d in dates if len(d) >= 8]
        if not dates:
            return 0

        # Delete existing rows for those dates to avoid duplicates on re-upload.
        placeholders = []
        params: Dict[str, Any] = {"user_id": uid}
        for i, d in enumerate(dates):
            key = f"d{i}"
            placeholders.append(f":{key}")
            params[key] = d
        q_del = f"""
        DELETE FROM xiaomi_sleep_entries
        WHERE user_id = :user_id AND sleep_date IN ({", ".join(placeholders)})
        """
        self._execute_query(q_del, params=params, commit=True)

        q_ins = """
        INSERT INTO xiaomi_sleep_entries (
            user_id, sleep_date, start_text, end_text,
            duration_min, deep_min, light_min, rem_min, awake_min,
            sleep_score, source_filename, raw_json
        )
        VALUES (
            :user_id, :sleep_date, :start_text, :end_text,
            :duration_min, :deep_min, :light_min, :rem_min, :awake_min,
            :sleep_score, :source_filename, :raw_json
        )
        """
        inserted = 0
        for e in entries:
            params = {
                "user_id": uid,
                "sleep_date": str(e.get("sleep_date") or "").strip(),
                "start_text": e.get("start_text"),
                "end_text": e.get("end_text"),
                "duration_min": e.get("duration_min"),
                "deep_min": e.get("deep_min"),
                "light_min": e.get("light_min"),
                "rem_min": e.get("rem_min"),
                "awake_min": e.get("awake_min"),
                "sleep_score": e.get("sleep_score"),
                "source_filename": e.get("source_filename"),
                "raw_json": e.get("raw_json"),
            }
            ok = self._execute_query(q_ins, params=params, commit=True)
            if ok:
                inserted += 1
        return inserted

    def list_xiaomi_sleep_entries_between(
        self,
        user_id: int,
        start_date: str,
        end_date: str,
        limit: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Returns sleep entries in [start_date, end_date] (inclusive), using YYYY-MM-DD strings.
        """
        uid = str(int(user_id))
        lim = max(1, min(20000, int(limit)))
        s = str(start_date or "").strip()
        e = str(end_date or "").strip()
        if len(s) < 8 or len(e) < 8:
            return []
        q = """
        SELECT id, user_id, sleep_date, start_text, end_text,
               duration_min, deep_min, light_min, rem_min, awake_min,
               sleep_score, source_filename, raw_json, created_at
        FROM xiaomi_sleep_entries
        WHERE user_id = :user_id
          AND sleep_date >= :start_date
          AND sleep_date <= :end_date
        ORDER BY sleep_date ASC, id ASC
        LIMIT :lim
        """
        return (
            self._execute_query(
                q,
                {"user_id": uid, "start_date": s, "end_date": e, "lim": lim},
                fetch_all=True,
            )
            or []
        )

