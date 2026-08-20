"""Shared fixtures and helpers for ComWorker Platform API tests.

All tests use the *deployed* API (docker compose services must be running).
Configure via environment variables:

  COMWORKER_BASE_URL    API base URL (default: http://localhost:8080)
  ADMIN_USERNAME       Admin username (default: admin)
  ADMIN_PASSWORD       Admin password (default: admin123)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _load_env_file() -> dict[str, str]:
    """Read project-level .env file and return key=value pairs."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        result[key.strip()] = value
    return result


_env = _load_env_file()

BASE_URL = os.getenv("COMWORKER_BASE_URL", "http://localhost:8080").rstrip("/")
ADMIN_USERNAME = (
    os.getenv("COMWORKER_USERNAME")
    or _env.get("ADMIN_USERNAME")
    or "admin"
)
ADMIN_PASSWORD = (
    os.getenv("COMWORKER_PASSWORD")
    or _env.get("ADMIN_PASSWORD")
    or "admin123"
)

_jwt_cache: dict[tuple[str, str], str] = {}
_test_users: list[dict] = []  # track users created during tests for cleanup


def api_url(path: str) -> str:
    """Build a full API URL from a path like '/api/auth/login'."""
    path = path.lstrip("/")
    return f"{BASE_URL}/{path}"


def json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: int = 60,
    raw_response: bool = False,
):
    """Send a JSON request and return parsed JSON.

    Args:
        url: Full URL.
        method: HTTP method (GET / POST / PUT / DELETE).
        payload: JSON-serialisable dict sent as request body.
        headers: Extra HTTP headers (Authorization etc.).
        timeout: Request timeout in seconds.
        raw_response: If True, return (parsed_json, status_code, resp_headers).

    Returns:
        Parsed JSON body, or (body, status_code, headers) if raw_response=True.

    Raises:
        RuntimeError: On non-2xx status.
    """
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    data_bytes = None
    if payload is not None:
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = Request(url, data=data_bytes, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            if raw_response:
                return body, resp.status, dict(resp.headers)
            return body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"detail": body or f"HTTP {exc.code}"}
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def auth_headers(token: str) -> dict:
    """Return an Authorization header dict for the given JWT."""
    return {"Authorization": f"Bearer {token}"}


def admin_token() -> str:
    """Get (cached) JWT for the admin user."""
    cache_key = (BASE_URL, ADMIN_USERNAME)
    if cache_key in _jwt_cache:
        return _jwt_cache[cache_key]

    result = json_request(
        api_url("/api/auth/login"),
        method="POST",
        payload={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    token = result["access_token"]
    _jwt_cache[cache_key] = token
    return token


def register_user(
    username: str,
    email: str,
    password: str,
) -> dict:
    """Register a new user and return the token response."""
    result = json_request(
        api_url("/api/auth/register"),
        method="POST",
        payload={
            "username": username,
            "email": email,
            "password": password,
        },
    )
    _test_users.append({"username": username, "user_id": result.get("user_id")})
    return result


def unique_username(prefix: str = "test") -> str:
    """Generate a unique username for a test."""
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


def unique_email(username: str) -> str:
    return f"{username}@test.local"


def cleanup_test_users():
    """Delete all test users created during this session (admin-only)."""
    token = admin_token()
    for user in _test_users:
        uid = user.get("user_id")
        if not uid:
            continue
        try:
            json_request(
                api_url(f"/api/admin/users/{uid}/container"),
                method="DELETE",
                headers=auth_headers(token),
            )
        except RuntimeError:
            pass  # no container or already deleted
    _test_users.clear()
