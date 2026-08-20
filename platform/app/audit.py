"""Helpers for writing audit log records.

Records are written by handlers throughout the platform via
``write_audit_log``. A FastAPI middleware (see ``app.audit_middleware``)
populates ``request_id`` / ``ip`` / ``user_agent`` from the current request
context, so callers do not need to thread these through every API.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

logger = logging.getLogger(__name__)


# ── Request-scoped metadata (set by audit_middleware) ──────────────────
# These context vars are populated for every HTTP request by the
# middleware. Handlers that call write_audit_log inherit them
# automatically without needing to know the request object.

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_request_id", default=None
)
_client_ip_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_client_ip", default=None
)
_user_agent_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_user_agent", default=None
)


def set_request_audit_context(
    *, request_id: str, client_ip: str | None, user_agent: str | None
) -> None:
    _request_id_var.set(request_id)
    _client_ip_var.set(client_ip)
    _user_agent_var.set(user_agent)


def get_request_audit_context() -> tuple[str | None, str | None, str | None]:
    return _request_id_var.get(), _client_ip_var.get(), _user_agent_var.get()


def new_request_id() -> str:
    return uuid.uuid4().hex


async def write_audit_log(
    db: AsyncSession,
    *,
    action: str,
    user_id: str | None = None,
    resource: str | None = None,
    detail: str | dict[str, Any] | list[Any] | None = None,
    commit: bool = False,
    status_code: int | None = None,
) -> AuditLog:
    """Append an audit log row.

    `detail` may be a string or JSON-serializable structure.
    By default this only stages the row in the current session; callers can
    commit together with their main transaction. Pass ``commit=True`` when the
    audit record must be persisted immediately.

    The middleware-populated context vars (request_id, ip, user_agent) are
    pulled in automatically. Pass ``status_code`` to record the HTTP result
    for actions that are tied 1:1 with a single request.
    """

    if detail is None:
        detail_text = None
    elif isinstance(detail, str):
        detail_text = detail
    else:
        detail_text = json.dumps(detail, ensure_ascii=False, separators=(",", ":"), default=str)

    request_id, ip, user_agent = get_request_audit_context()

    row = AuditLog(
        user_id=user_id,
        action=action,
        resource=resource,
        detail=detail_text,
        ip=ip,
        user_agent=user_agent,
        request_id=request_id,
        status_code=status_code,
    )
    db.add(row)
    if commit:
        await db.commit()
    return row