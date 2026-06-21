# api_clients/clockify_client.py
"""
Thin synchronous client for the Clockify REST API (https://docs.clockify.me/).

Notes
-----
- Auth is per-user: every function takes the caller's ``api_key`` as an argument
  (keys are stored per-user, not in the bot environment).
- Authentication header is ``X-Api-Key`` (Bearer tokens are not supported).
- Datetimes are ISO-8601 UTC with a trailing ``Z`` (no microseconds). Sending
  naive/local times is the cause of the well-known "stop added N extra hours" bug.
- Functions return parsed JSON on success, or an error dict shaped like
  ``{"error": "<code>", "message": "..."}``. Error codes:
  ``auth`` (bad/expired key), ``ratelimit`` (HTTP 429 — Free plan is 30 req/hour
  per workspace), ``timeout``, ``http``, ``network``, ``decode``.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import requests

import config

logger = logging.getLogger(__name__)

_BASE = (getattr(config, "CLOCKIFY_API_BASE_URL", "") or "https://api.clockify.me/api/v1").rstrip("/")
_TIMEOUT_S = 10
_DT_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _headers(api_key: str) -> dict:
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


def now_iso() -> str:
    """Current time as an ISO-8601 UTC string Clockify accepts (e.g. 2026-06-21T10:00:00Z)."""
    return datetime.now(timezone.utc).strftime(_DT_FMT)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a Clockify ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    # Clockify may return fractional seconds; normalise the trailing Z to +00:00.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _error(code: str, message: str) -> dict:
    return {"error": code, "message": message}


def _request(
    method: str,
    path: str,
    api_key: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> Any:
    """
    Perform a Clockify request and normalise the outcome.

    Returns parsed JSON (dict/list) on success, ``None`` for an empty 2xx body,
    or an error dict ``{"error": code, "message": ...}`` on any failure.
    """
    if not api_key:
        return _error("auth", "No Clockify API key configured.")

    url = f"{_BASE}{path}"
    try:
        response = requests.request(
            method,
            url,
            headers=_headers(api_key),
            params=params,
            json=json_body,
            timeout=_TIMEOUT_S,
        )
    except requests.exceptions.Timeout:
        logger.warning("Clockify request timed out: %s %s", method, path)
        return _error("timeout", "Clockify did not respond in time. Try again.")
    except requests.exceptions.RequestException as exc:
        logger.warning("Clockify network error on %s %s: %s", method, path, exc)
        return _error("network", "Could not reach Clockify (network error).")

    status = response.status_code
    if status in (401, 403):
        return _error("auth", "Clockify rejected the API key (invalid or expired).")
    if status == 429:
        return _error(
            "ratelimit",
            "Hit Clockify's rate limit (Free plan allows 30 requests/hour). Try again later.",
        )
    if status >= 400:
        # Surface Clockify's own message when present; otherwise the status text.
        detail = response.text.strip()
        try:
            payload = response.json()
            detail = payload.get("message") or payload.get("error") or detail
        except ValueError:
            pass
        logger.warning("Clockify HTTP %s on %s %s: %s", status, method, path, detail[:200])
        return _error("http", f"Clockify error {status}: {detail[:200]}" if detail else f"Clockify error {status}.")

    if status == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        logger.warning("Clockify returned non-JSON body on %s %s", method, path)
        return _error("decode", "Clockify returned an unreadable response.")


def is_error(result: Any) -> bool:
    """True if ``result`` is an error dict from this module."""
    return isinstance(result, dict) and "error" in result


# --- Account / workspace ----------------------------------------------------

def get_current_user(api_key: str) -> Any:
    """GET /user — returns the current user, incl. ``id`` and ``activeWorkspace``."""
    return _request("GET", "/user", api_key)


def get_workspaces(api_key: str) -> Any:
    """GET /workspaces — list of workspaces the key can access."""
    return _request("GET", "/workspaces", api_key)


def get_projects(api_key: str, workspace_id: str) -> Any:
    """GET /workspaces/{wid}/projects — used to resolve a project name to its id."""
    return _request(
        "GET",
        f"/workspaces/{workspace_id}/projects",
        api_key,
        params={"archived": "false", "page-size": 200},
    )


# --- Time entries -----------------------------------------------------------

def get_in_progress(api_key: str, workspace_id: str, user_id: str) -> Any:
    """
    GET the user's currently running entry, or ``None`` if nothing is running.
    Returns a single entry dict, ``None``, or an error dict.
    """
    result = _request(
        "GET",
        f"/workspaces/{workspace_id}/user/{user_id}/time-entries",
        api_key,
        params={"in-progress": "true"},
    )
    if is_error(result):
        return result
    if isinstance(result, list):
        return result[0] if result else None
    return None


def start_entry(
    api_key: str,
    workspace_id: str,
    description: str,
    project_id: Optional[str] = None,
    billable: bool = False,
) -> Any:
    """
    POST a new running time entry (no ``end`` -> it stays running).
    Returns the created entry dict or an error dict.
    """
    body: dict = {
        "start": now_iso(),
        "description": description or "",
        "billable": billable,
    }
    if project_id:
        body["projectId"] = project_id
    return _request("POST", f"/workspaces/{workspace_id}/time-entries", api_key, json_body=body)


def stop_entry(api_key: str, workspace_id: str, user_id: str) -> Any:
    """
    PATCH to stop the user's currently running timer.
    Returns the stopped entry dict, ``None`` if nothing was running, or an error dict.
    """
    result = _request(
        "PATCH",
        f"/workspaces/{workspace_id}/user/{user_id}/time-entries",
        api_key,
        json_body={"end": now_iso()},
    )
    if is_error(result):
        # Clockify returns 404 when there is no running timer; treat that as "nothing to stop".
        if result.get("error") == "http" and "404" in str(result.get("message", "")):
            return None
        return result
    return result
