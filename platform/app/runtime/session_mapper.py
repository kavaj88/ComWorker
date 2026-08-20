"""Session identifier helpers for platform compatibility."""

from uuid import uuid4


SESSION_PREFIX = "agent:main:session-"


def normalize_platform_session_key(session_key: str | None) -> str:
    if session_key:
        return session_key
    return f"{SESSION_PREFIX}{uuid4()}"
