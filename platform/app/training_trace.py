"""Local JSONL training trace helpers.

This module is intentionally independent from FastAPI and HTTP clients so the
capture path can stay an optional side effect around product routes.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.models import User

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
)


def redact_text(value: str) -> str:
    """Mask common secret shapes while preserving the text's training value."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def stable_scope_hash(value: str, *, salt: str = "") -> str:
    """Return a short stable hash for user/session scoping fields."""
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
    return digest[:16]


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def redact_trace_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a recursively redacted copy of an incoming trace record."""
    return _redact_value(record)


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            "role": str(message.get("role") or ""),
            "content": redact_text(str(message.get("content") or "")),
        }
    role = getattr(message, "role", "")
    content = getattr(message, "content", "")
    return {"role": str(role or ""), "content": redact_text(str(content or ""))}


def build_model_chat_trace_record(
    *,
    link_id: str,
    session_id: str,
    request_user_id: int,
    function_id: int,
    messages: list[Any],
    user: User | None,
    run_id: str,
    model: str,
    runtime: str,
    tool_events: list[dict[str, Any]],
    final_output: str,
    status: str,
    trace_hash_salt: str = "",
) -> dict[str, Any]:
    """Build one sanitized trace record for a completed model-chat run."""
    raw_user_scope = user.id if user is not None and user.id else str(request_user_id)
    return {
        "trace_id": str(uuid.uuid4()),
        "source": "model_chat",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user_scope": stable_scope_hash(str(raw_user_scope), salt=trace_hash_salt),
        "session_scope": stable_scope_hash(session_id, salt=trace_hash_salt),
        "runtime": runtime,
        "run_id": run_id,
        "model": model,
        "status": status,
        "request": {
            "link_id": link_id,
            "function_id": function_id,
            "xapi_version": None,
        },
        "messages": [_message_to_dict(message) for message in messages],
        "tool_events": [_redact_value(event) for event in tool_events],
        "final_output": redact_text(final_output),
        "next_state": None,
        "labels": {},
        "privacy": {
            "redaction": "basic-secret-patterns",
            "raw_user_identity_stored": False,
        },
    }


def append_jsonl_trace(
    trace_dir: str | Path,
    record: dict[str, Any],
    *,
    prefix: str = "model_chat",
) -> Path:
    """Append one trace record to a daily JSONL file and return its path."""
    output_dir = Path(trace_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{prefix}-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return path
