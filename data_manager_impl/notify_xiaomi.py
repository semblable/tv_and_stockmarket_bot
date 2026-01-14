import secrets
import json
import logging
from typing import Any, Dict, Optional

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

