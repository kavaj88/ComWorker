"""FastAPI middleware that stamps every request with an audit-friendly
context: a request_id (echoed via the ``X-Request-ID`` response header)
plus the client IP and User-Agent. Handlers that call
``app.audit.write_audit_log`` pick these up automatically.
"""

from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.audit import (
    new_request_id,
    set_request_audit_context,
)

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def _extract_client_ip(request: Request) -> str | None:
    """Honor common proxy headers when present, fall back to the socket."""

    for header in ("f-cdn-client-ip", "x-forwarded-for", "x-real-ip"):
        value = request.headers.get(header)
        if value:
            # X-Forwarded-For may contain a comma-separated chain; the
            # leftmost entry is the original client.
            return value.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Stamp each request with an audit context."""

    async def dispatch(self, request: Request, call_next):
        # Use the caller's X-Request-ID if present (useful for distributed
        # traces from the frontend), otherwise mint a fresh one.
        request_id = request.headers.get(REQUEST_ID_HEADER.lower()) or new_request_id()
        client_ip = _extract_client_ip(request)
        user_agent = request.headers.get("user-agent")

        # Set BEFORE downstream so any handler that calls write_audit_log
        # picks up the right metadata.
        set_request_audit_context(
            request_id=request_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        try:
            response: Response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception during %s %s", request.method, request.url.path)
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        return response