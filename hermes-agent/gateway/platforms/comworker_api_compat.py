"""ComWorker compatibility overlay for the Hermes API server.

This module keeps ComWorker-specific ComWorker-compatible support outside
Hermes upstream files. The container entrypoint installs the overlay before
starting ``hermes gateway run``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - Hermes API server requires aiohttp at runtime.
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SESSION_ROUTES = (
    ("GET", "/api/hermes/sessions", "_handle_list_sessions"),
    ("POST", "/api/hermes/sessions", "_handle_create_session"),
    ("GET", "/api/hermes/sessions/{session_id}", "_handle_get_session"),
    ("PUT", "/api/hermes/sessions/{session_id}/title", "_handle_rename_session"),
    ("DELETE", "/api/hermes/sessions/{session_id}", "_handle_delete_session"),
    ("GET", "/api/hermes/events/stream", "_handle_events_stream"),
)

_SESSION_AGENT_RE = re.compile(r"^agent:([^:]+):")
_PROFILE_PROMPT_FILES = (
    "SOUL.md",
    "workspace/IDENTITY.md",
    "workspace/USER.md",
    "workspace/AGENTS.md",
)


def _openai_error(
    message: str,
    err_type: str = "invalid_request_error",
    code: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": err_type,
            "code": code,
        }
    }


def _agent_id_from_session_key(session_key: str | None) -> str | None:
    if not session_key:
        return None
    match = _SESSION_AGENT_RE.match(str(session_key))
    if not match:
        return None
    agent_id = match.group(1).strip()
    return agent_id or None


def _profiles_root() -> Path:
    try:
        from hermes_constants import get_default_hermes_root
        return get_default_hermes_root() / "profiles"
    except Exception:
        try:
            from hermes_constants import get_hermes_home
            return get_hermes_home() / "profiles"
        except Exception:
            return Path.home() / ".hermes" / "profiles"


def _profile_prompt_from_session_key(session_key: str | None) -> str | None:
    agent_id = _agent_id_from_session_key(session_key)
    if not agent_id:
        return None

    profile_dir = _profiles_root() / agent_id
    if not profile_dir.is_dir():
        return None

    parts: list[str] = []
    for rel_path in _PROFILE_PROMPT_FILES:
        path = profile_dir / rel_path
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(f"# {rel_path}\n{text}")

    if not parts:
        return None
    return (
        "以下内容来自当前 Hermes profile。请把它作为本次会话的 Agent 身份、"
        "任务边界和工作区约束。\n\n"
        + "\n\n".join(parts)
    )


def _route_key(route: Any) -> tuple[str, str] | None:
    method = getattr(route, "method", None)
    resource = getattr(route, "resource", None)
    path = getattr(resource, "canonical", None)
    if method and path:
        return str(method).upper(), str(path)
    if isinstance(route, tuple) and len(route) >= 2:
        return str(route[0]).upper(), str(route[1])
    return None


def _registered_route_keys(app: Any) -> set[tuple[str, str]]:
    router = getattr(app, "router", None)
    if router is None:
        return set()
    routes_attr = getattr(router, "routes", None)
    if callable(routes_attr):
        routes = routes_attr()
    else:
        routes = routes_attr or []
    keys = set()
    for route in routes:
        key = _route_key(route)
        if key is not None:
            keys.add(key)
    return keys


def _add_route(app: Any, method: str, path: str, handler: Any) -> None:
    add_method = {
        "GET": app.router.add_get,
        "POST": app.router.add_post,
        "PUT": app.router.add_put,
        "DELETE": app.router.add_delete,
    }[method]
    add_method(path, handler)


def register_routes(app: Any) -> None:
    """Register ComWorker session/event routes on an aiohttp app."""
    adapter = app["api_server_adapter"]
    existing = _registered_route_keys(app)
    for method, path, handler_name in SESSION_ROUTES:
        if (method, path) in existing:
            continue
        _add_route(app, method, path, getattr(adapter, handler_name))


def _format_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _ensure_event_subscribers(adapter: Any) -> set:
    subscribers = getattr(adapter, "_comworker_event_subscribers", None)
    if subscribers is None:
        subscribers = set()
        setattr(adapter, "_comworker_event_subscribers", subscribers)
    return subscribers


def _resolve_session_id(adapter: Any, session_id_or_prefix: str) -> Optional[str]:
    db = adapter._ensure_session_db()
    if db is None:
        return None
    try:
        resolved = db.resolve_session_id(session_id_or_prefix)
    except Exception:
        resolved = None
    return resolved or session_id_or_prefix


def _session_summary_payload(session: dict[str, Any]) -> dict[str, Any]:
    updated_at = _format_timestamp(session.get("last_active") or session.get("started_at"))
    created_at = _format_timestamp(session.get("started_at"))
    return {
        "session_id": session.get("id", ""),
        "title": session.get("title") or session.get("id", ""),
        "message_count": int(session.get("message_count") or 0),
        "created_at": created_at,
        "updated_at": updated_at,
        "last_message_at": updated_at,
    }


def _session_detail_payload(
    session: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _session_summary_payload(session)
    payload["messages"] = messages
    payload["source"] = session.get("source")
    payload["model"] = session.get("model")
    payload["ended_at"] = _format_timestamp(session.get("ended_at"))
    return payload


def _broadcast_event(adapter: Any, event: dict[str, Any]) -> None:
    stale = []
    for queue in list(_ensure_event_subscribers(adapter)):
        try:
            queue.put_nowait(event)
        except Exception:
            stale.append(queue)
    for queue in stale:
        _ensure_event_subscribers(adapter).discard(queue)


async def _handle_list_sessions(adapter: Any, request: "web.Request") -> "web.Response":
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    db = adapter._ensure_session_db()
    if db is None:
        return web.json_response({"sessions": []})

    try:
        limit = min(max(int(request.query.get("limit", "100")), 1), 500)
    except ValueError:
        return web.json_response(_openai_error("Invalid limit query parameter"), status=400)

    try:
        sessions = db.list_sessions_rich(limit=limit)
    except Exception as exc:
        logger.error("Failed to list Hermes sessions: %s", exc, exc_info=True)
        return web.json_response(
            _openai_error(f"Failed to list sessions: {exc}", err_type="server_error"),
            status=500,
        )

    return web.json_response({"sessions": [_session_summary_payload(session) for session in sessions]})


async def _handle_create_session(adapter: Any, request: "web.Request") -> "web.Response":
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    db = adapter._ensure_session_db()
    if db is None:
        return web.json_response(_openai_error("Session store unavailable"), status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response(_openai_error("Invalid JSON"), status=400)

    session_key = str(body.get("session_key") or body.get("session_id") or "").strip()
    if not session_key:
        return web.json_response(_openai_error("Missing 'session_key' field"), status=400)

    try:
        db.create_session(session_key, str(body.get("source") or "api"))
        title = body.get("title")
        if title:
            try:
                db.set_session_title(session_key, str(title))
            except ValueError:
                db.set_session_title(session_key, f"{str(title)[:180]} {session_key[-6:]}")
        session = db.get_session(session_key)
    except Exception as exc:
        logger.error("Failed to create Hermes session %s: %s", session_key, exc, exc_info=True)
        return web.json_response(
            _openai_error(f"Failed to create session: {exc}", err_type="server_error"),
            status=500,
        )

    return web.json_response(_session_summary_payload(session or {"id": session_key}))


async def _handle_get_session(adapter: Any, request: "web.Request") -> "web.Response":
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    db = adapter._ensure_session_db()
    if db is None:
        return web.json_response(_openai_error("Session store unavailable"), status=503)

    requested = request.match_info["session_id"]
    session_id = _resolve_session_id(adapter, requested)
    try:
        session = db.get_session(session_id) if session_id else None
        if not session:
            return web.json_response(
                _openai_error(f"Session not found: {requested}", code="session_not_found"),
                status=404,
            )
        messages = db.get_messages_as_conversation(session_id)
    except Exception as exc:
        logger.error("Failed to load Hermes session %s: %s", requested, exc, exc_info=True)
        return web.json_response(
            _openai_error(f"Failed to load session: {exc}", err_type="server_error"),
            status=500,
        )

    return web.json_response(_session_detail_payload(session, messages))


async def _handle_rename_session(adapter: Any, request: "web.Request") -> "web.Response":
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    db = adapter._ensure_session_db()
    if db is None:
        return web.json_response(_openai_error("Session store unavailable"), status=503)

    requested = request.match_info["session_id"]
    session_id = _resolve_session_id(adapter, requested)
    if not session_id:
        return web.json_response(
            _openai_error(f"Session not found: {requested}", code="session_not_found"),
            status=404,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response(_openai_error("Invalid JSON"), status=400)

    title = body.get("title")
    if title is None:
        return web.json_response(_openai_error("Missing 'title' field"), status=400)

    try:
        updated = db.set_session_title(session_id, str(title))
    except ValueError:
        try:
            updated = db.set_session_title(session_id, f"{str(title)[:180]} {session_id[-6:]}")
        except ValueError as exc:
            return web.json_response(_openai_error(str(exc)), status=400)
    except Exception as exc:
        logger.error("Failed to rename Hermes session %s: %s", requested, exc, exc_info=True)
        return web.json_response(
            _openai_error(f"Failed to rename session: {exc}", err_type="server_error"),
            status=500,
        )

    if not updated:
        return web.json_response(
            _openai_error(f"Session not found: {requested}", code="session_not_found"),
            status=404,
        )
    return web.json_response(
        {"ok": True, "session_id": session_id, "title": db.get_session_title(session_id)}
    )


async def _handle_delete_session(adapter: Any, request: "web.Request") -> "web.Response":
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    db = adapter._ensure_session_db()
    if db is None:
        return web.json_response(_openai_error("Session store unavailable"), status=503)

    requested = request.match_info["session_id"]
    session_id = _resolve_session_id(adapter, requested)
    if not session_id:
        return web.json_response(
            _openai_error(f"Session not found: {requested}", code="session_not_found"),
            status=404,
        )

    try:
        deleted = db.delete_session(session_id)
    except Exception as exc:
        logger.error("Failed to delete Hermes session %s: %s", requested, exc, exc_info=True)
        return web.json_response(
            _openai_error(f"Failed to delete session: {exc}", err_type="server_error"),
            status=500,
        )

    if not deleted:
        return web.json_response(
            _openai_error(f"Session not found: {requested}", code="session_not_found"),
            status=404,
        )
    return web.json_response({"ok": True, "session_id": session_id})


async def _handle_events_stream(adapter: Any, request: "web.Request") -> "web.StreamResponse":
    auth_err = adapter._check_auth(request)
    if auth_err:
        return auth_err

    queue: "asyncio.Queue[Optional[dict[str, Any]]]" = asyncio.Queue()
    _ensure_event_subscribers(adapter).add(queue)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                continue
            if event is None:
                break
            await response.write(f"data: {json.dumps(event)}\n\n".encode())
    except Exception as exc:
        logger.debug("[comworker_api_compat] global event stream closed: %s", exc)
    finally:
        _ensure_event_subscribers(adapter).discard(queue)

    return response


def _status_event(run_status: dict[str, Any], status: str) -> dict[str, Any]:
    base = {
        "run_id": run_status.get("run_id"),
        "session_id": run_status.get("session_id"),
        "timestamp": run_status.get("updated_at"),
    }
    if status == "completed":
        return {
            "type": "message.completed",
            **base,
            "message": {"role": "assistant", "content": run_status.get("output", "")},
            "usage": run_status.get("usage"),
        }
    if status == "failed":
        return {
            "type": "run.failed",
            **base,
            "error": run_status.get("error") or "run failed",
        }
    return {"type": f"run.{status}", **base}


def install() -> None:
    """Install ComWorker compatibility patches onto Hermes' API adapter."""
    from gateway.platforms import api_server

    adapter_cls = api_server.APIServerAdapter
    if getattr(adapter_cls, "_comworker_compat_installed", False):
        return

    adapter_cls._handle_list_sessions = _handle_list_sessions
    adapter_cls._handle_create_session = _handle_create_session
    adapter_cls._handle_get_session = _handle_get_session
    adapter_cls._handle_rename_session = _handle_rename_session
    adapter_cls._handle_delete_session = _handle_delete_session
    adapter_cls._handle_events_stream = _handle_events_stream
    adapter_cls._comworker_broadcast_event = _broadcast_event

    original_create_agent = adapter_cls._create_agent

    def create_agent_with_profile_prompt(self, *args: Any, **kwargs: Any):
        profile_prompt = _profile_prompt_from_session_key(kwargs.get("gateway_session_key"))
        if profile_prompt:
            existing_prompt = kwargs.get("ephemeral_system_prompt")
            kwargs["ephemeral_system_prompt"] = (
                f"{profile_prompt}\n\n# 请求附加 instructions\n{existing_prompt}"
                if existing_prompt
                else profile_prompt
            )
        return original_create_agent(self, *args, **kwargs)

    adapter_cls._create_agent = create_agent_with_profile_prompt

    original_set_run_status = adapter_cls._set_run_status

    def set_run_status_with_broadcast(self, run_id: str, status: str, **fields: Any):
        run_status = original_set_run_status(self, run_id, status, **fields)
        _broadcast_event(self, _status_event(run_status, status))
        return run_status

    adapter_cls._set_run_status = set_run_status_with_broadcast

    original_connect = adapter_cls.connect

    async def connect_with_comworker_routes(self, *args: Any, **kwargs: Any) -> bool:
        original_app_runner = api_server.web.AppRunner

        def app_runner_with_comworker_routes(app: Any, *runner_args: Any, **runner_kwargs: Any):
            register_routes(app)
            return original_app_runner(app, *runner_args, **runner_kwargs)

        api_server.web.AppRunner = app_runner_with_comworker_routes
        try:
            return await original_connect(self, *args, **kwargs)
        finally:
            api_server.web.AppRunner = original_app_runner

    adapter_cls.connect = connect_with_comworker_routes
    adapter_cls._comworker_compat_installed = True
