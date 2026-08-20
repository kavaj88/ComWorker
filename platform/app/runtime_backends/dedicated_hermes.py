from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.auth.service import decode_token, get_user_by_id
from app.config import settings
from app.container.manager import ensure_running
from app.db.engine import async_session
from app.hermes_client import HermesClient
from app.runtime_backend import RuntimeContext
from app.runtime_backends.hermes_files import (
    DEFAULT_HERMES_UPLOAD_DIR,
    read_data_file_from_hermes_container,
    write_upload_to_hermes_container,
)
from app.runtime_backends.hermes_knowledge import build_knowledge_context
from app.runtime_backends.hermes_agents import (
    agent_id_from_session_key,
    agent_identity_prompt_from_hermes_container,
    build_agent_info,
    list_agent_profiles_from_hermes_container,
    model_for_session_key,
)
from app.runtime_backends.hermes_run import (
    HermesEventSanitizer,
    HermesRunTimingTracker,
    format_latency_ms,
    sanitize_hermes_message,
    sanitize_hermes_messages,
    sanitize_run_events,
    sanitize_sse_block,
    summarize_run_events,
)
from app.runtime_backends.hermes_skills import list_skills_from_hermes_container
from app.runtime_backends.hermes_commands import list_hermes_commands_from_container

logger = logging.getLogger(__name__)

LEGACY_COMWORKER_SESSIONS_INDEX = "agents/main/sessions/sessions.json"
RUNTIME_SCOPE = "dedicated"

# Highest-priority language directive.  Reasoning models sometimes default to
# English reasoning even when the user writes in Chinese, so this is injected
# both as the leading system instruction and as a conversation system message.
_LANGUAGE_INSTRUCTION = (
    "语言要求（最高优先级）：\n"
    "- 如果用户使用中文（或系统语言为中文），你的思考过程、推理步骤、内部计划、工具说明与最终回答必须全部使用中文。\n"
    "- 即使你在进行内部推理（reasoning/thinking），也一律使用中文；禁止默认使用英文。\n"
    "- 如果用户使用其他语言，则使用该语言作答。"
)


def _is_generated_comworker_session_key(session_key: str) -> bool:
    return session_key.startswith("agent:") and ":session-" in session_key


def _fallback_title(message: str) -> str | None:
    title = " ".join(message.strip().split())
    if not title:
        return None
    return title[:48]


def _title_from_messages(messages: list[Any]) -> str | None:
    """Derive a readable title from the first non-empty user message."""
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip() != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return _fallback_title(content)
    return None


def _looks_like_raw_session_id(value: str) -> bool:
    """Return True if *value* appears to be an auto-generated session ID rather than
    a human-readable title.  This prevents raw IDs such as ``agent:main:session-1786194``
    or ``session-1786194`` from leaking into the UI session list."""
    if not value:
        return False
    v = value.strip()
    # Full key pattern:  agent:<name>:session-<digits>
    # Stripped pattern:  session-<digits>
    return (
        (v.startswith("agent:") and ":session-" in v)
        or bool(_SESSION_ID_SUFFIX_RE.match(v))
    )


import re as _re
_SESSION_ID_SUFFIX_RE = _re.compile(r"^session-\d{10,}$")


def _empty_comworker_session(session_key: str) -> dict[str, Any]:
    return {
        "key": session_key,
        "sessionKey": session_key,
        "title": session_key.rsplit(":", 1)[-1] or session_key,
        "messages": [],
        "messageCount": 0,
        "createdAt": None,
        "updatedAt": None,
        "runtime": "hermes",
        "pending": True,
    }


def _legacy_ms_to_iso(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, str) and value:
        return value
    return None


def _clean_legacy_comworker_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("Sender (untrusted metadata):") and "\n\n[" in cleaned:
        cleaned = "[" + cleaned.rsplit("\n\n[", 1)[1]
    if cleaned.startswith("["):
        close = cleaned.find("] ")
        if 0 <= close < 120:
            cleaned = cleaned[close + 2 :]
    return cleaned.strip()


def _legacy_comworker_message_content(content: Any) -> str:
    if isinstance(content, str):
        return _clean_legacy_comworker_text(content)
    if not isinstance(content, list):
        return ""
    text_parts = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
    return _clean_legacy_comworker_text("\n".join(text_parts))


def _legacy_comworker_session_id(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    session_id = record.get("sessionId")
    if isinstance(session_id, str) and session_id:
        return session_id
    session_file = record.get("sessionFile")
    if isinstance(session_file, str) and session_file.endswith(".jsonl"):
        return session_file.rsplit("/", 1)[-1].removesuffix(".jsonl")
    return ""


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


class DedicatedHermesBackend:
    def __init__(self, base_url: str | None = None):
        self._base_url_override = base_url
        self._api_ready_keys: set[tuple[str, str]] = set()
        self._api_ready_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._clients: dict[tuple[str, str, int, float], HermesClient] = {}
        self._pending_titles_by_run: dict[str, tuple[str, str]] = {}
        self._agent_id_by_run: dict[str, str | None] = {}

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def _wait_for_api_ready(
        self,
        ctx: RuntimeContext,
        base_url: str,
        runtime_id: str = "",
    ) -> None:
        ready_key = (base_url, runtime_id)
        if ready_key in self._api_ready_keys:
            return
        lock = self._api_ready_locks.setdefault(ready_key, asyncio.Lock())
        async with lock:
            if ready_key in self._api_ready_keys:
                return
            started_at = time.perf_counter()
            client = HermesClient(
                base_url=base_url,
                api_key=settings.dedicated_hermes_api_key,
                connect_retries=settings.hermes_connect_retries,
                retry_delay_seconds=settings.hermes_retry_delay_seconds,
            )
            await client.get_models()
            self._api_ready_keys.add(ready_key)
            logger.info(
                "hermes_api_ready scope=%s user_id=%s elapsed_ms=%.1f base_url=%s runtime_id=%s",
                RUNTIME_SCOPE,
                ctx.user.id,
                _elapsed_ms(started_at),
                base_url,
                runtime_id,
            )

    async def _resolve_base_url(self, ctx: RuntimeContext) -> str:
        if self._base_url_override:
            return self._base_url_override.rstrip("/")
        if settings.dev_comworker_url:
            return settings.dev_comworker_url.rstrip("/")
        started_at = time.perf_counter()
        async with async_session() as db:
            container = await ensure_running(db, ctx.user.id)
        if not container.internal_host or not container.internal_port:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hermes runtime address is unavailable",
            )
        logger.info(
            "hermes_runtime_ready scope=%s user_id=%s elapsed_ms=%.1f host=%s port=%s",
            RUNTIME_SCOPE,
            ctx.user.id,
            _elapsed_ms(started_at),
            container.internal_host,
            container.internal_port,
        )
        base_url = f"http://{container.internal_host}:{container.internal_port}"
        runtime_id = str(getattr(container, "docker_id", "") or "")
        await self._wait_for_api_ready(ctx, base_url, runtime_id)
        return base_url

    async def _client(self, ctx: RuntimeContext) -> HermesClient:
        base_url = await self._resolve_base_url(ctx)
        key = (
            base_url,
            settings.dedicated_hermes_api_key,
            settings.hermes_connect_retries,
            settings.hermes_retry_delay_seconds,
        )
        client = self._clients.get(key)
        if client is None:
            client = HermesClient(
                base_url=base_url,
                api_key=settings.dedicated_hermes_api_key,
                connect_retries=settings.hermes_connect_retries,
                retry_delay_seconds=settings.hermes_retry_delay_seconds,
            )
            self._clients[key] = client
        return client

    async def _request(self, ctx: RuntimeContext, method: str, path: str, **kwargs) -> Any:
        kwargs.pop("agent_id", None)
        client = await self._client(ctx)
        return await client.request(method, path, **kwargs)

    async def prewarm(self, ctx: RuntimeContext) -> dict:
        await self._resolve_base_url(ctx)
        return {"ok": True, "status": "ready", "runtime": "hermes"}

    async def _session_record(self, ctx: RuntimeContext, session_key: str) -> dict[str, Any]:
        payload = await self._request(ctx, "GET", f"/api/hermes/sessions/{session_key}")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=500, detail="Unexpected Hermes session response")
        return payload

    def _session_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        message_count = payload.get("message_count")
        created_at = payload.get("created_at")
        updated_at = payload.get("updated_at") or payload.get("last_message_at") or payload.get("created_at")
        session_id = payload.get("session_id", "")
        raw_title = payload.get("title") or session_id.rsplit(":", 1)[-1] or session_id
        if not raw_title or _looks_like_raw_session_id(raw_title):
            raw_title = _title_from_messages(payload.get("messages") or []) or raw_title
        return {
            "key": session_id,
            "sessionKey": session_id,
            "title": "" if _looks_like_raw_session_id(raw_title) else raw_title,
            "created_at": created_at,
            "createdAt": created_at,
            "updated_at": updated_at,
            "updatedAt": updated_at,
            "messageCount": message_count if isinstance(message_count, int) else len(payload.get("messages") or []),
        }

    async def _read_legacy_data_file(self, ctx: RuntimeContext, path: str) -> str:
        async with async_session() as db:
            container = await ensure_running(db, ctx.user.id)
        data = read_data_file_from_hermes_container(container.docker_id, path)
        return data.decode("utf-8")

    async def _legacy_comworker_session_index(self, ctx: RuntimeContext) -> dict[str, Any]:
        try:
            raw = await self._read_legacy_data_file(ctx, LEGACY_COMWORKER_SESSIONS_INDEX)
            payload = json.loads(raw)
        except Exception as exc:
            logger.debug("legacy_comworker_sessions_unavailable user_id=%s error=%s", ctx.user.id, exc)
            return {}
        return payload if isinstance(payload, dict) else {}

    def _legacy_comworker_session_summary(self, session_key: str, record: Any) -> dict[str, Any] | None:
        if not isinstance(record, dict) or not _legacy_comworker_session_id(record):
            return None
        updated_at = _legacy_ms_to_iso(record.get("updatedAt"))
        created_at = _legacy_ms_to_iso(record.get("createdAt"))
        raw_title = str(record.get("title") or session_key)
        return {
            "key": session_key,
            "sessionKey": session_key,
            "title": "" if _looks_like_raw_session_id(raw_title) else raw_title,
            "created_at": created_at,
            "createdAt": created_at,
            "updated_at": updated_at,
            "updatedAt": updated_at,
            "messageCount": record.get("messageCount") if isinstance(record.get("messageCount"), int) else None,
            "runtime": "legacy-comworker",
            "readonly": True,
        }

    def _legacy_comworker_messages_from_jsonl(self, raw: str) -> tuple[list[dict[str, Any]], str | None]:
        messages = []
        created_at = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            timestamp = event.get("timestamp")
            if created_at is None:
                created_at = _legacy_ms_to_iso(timestamp)
            if event.get("type") != "message":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            content = _legacy_comworker_message_content(message.get("content"))
            if role not in {"user", "assistant", "system", "tool"} or not content:
                continue
            messages.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": _legacy_ms_to_iso(message.get("timestamp")) or _legacy_ms_to_iso(timestamp),
                }
            )
        return messages, created_at

    async def _legacy_comworker_session(self, ctx: RuntimeContext, session_key: str) -> dict[str, Any] | None:
        index = await self._legacy_comworker_session_index(ctx)
        record = index.get(session_key)
        session_id = _legacy_comworker_session_id(record)
        if not session_id:
            return None
        try:
            raw = await self._read_legacy_data_file(ctx, f"agents/main/sessions/{session_id}.jsonl")
        except Exception as exc:
            logger.debug("legacy_comworker_session_file_unavailable user_id=%s session_key=%s error=%s", ctx.user.id, session_key, exc)
            raw = ""
        messages, created_at = self._legacy_comworker_messages_from_jsonl(raw)
        updated_at = _legacy_ms_to_iso(record.get("updatedAt")) if isinstance(record, dict) else None
        return {
            "key": session_key,
            "sessionKey": session_key,
            "title": str(record.get("title") or session_key) if isinstance(record, dict) else session_key,
            "messages": messages,
            "messageCount": len(messages),
            "created_at": created_at,
            "createdAt": created_at,
            "updated_at": updated_at,
            "updatedAt": updated_at,
            "runtime": "legacy-comworker",
            "readonly": True,
        }

    async def get_agent_info(self, ctx: RuntimeContext, container_agents: list[dict] | None = None) -> dict:
        payload = await (await self._client(ctx)).get_models()
        models = payload.get("data") if isinstance(payload, dict) else []
        return build_agent_info(
            models if isinstance(models, list) else [],
            scope=ctx.scope,
            runtime_mode=ctx.user.runtime_mode,
            container_agents=container_agents,
        )

    async def list_skills(self, ctx: RuntimeContext) -> list[dict]:
        async with async_session() as db:
            container = await ensure_running(db, ctx.user.id)
        return list_skills_from_hermes_container(container.docker_id)

    async def list_sessions(self, ctx: RuntimeContext) -> list[dict]:
        payload = await self._request(ctx, "GET", "/api/hermes/sessions")
        sessions = payload.get("sessions") if isinstance(payload, dict) else []
        if not isinstance(sessions, list):
            sessions = []
        summaries = [self._session_summary(item) for item in sessions if isinstance(item, dict)]
        # For sessions with no stored title, derive one from the first user
        # message so the sidebar never shows "对话 08-18" when a real question
        # exists.  This only costs an extra fetch for empty-title sessions.
        for summary in summaries:
            if summary.get("title"):
                continue
            session_key = str(summary.get("key") or summary.get("sessionKey") or "")
            if not session_key:
                continue
            try:
                record = await self._session_record(ctx, session_key)
                messages = record.get("messages") if isinstance(record, dict) else []
                derived = _title_from_messages(messages if isinstance(messages, list) else [])
                if derived:
                    summary["title"] = derived
            except Exception:
                pass
        seen_keys = {str(item.get("key") or item.get("sessionKey") or "") for item in summaries}
        legacy_index = await self._legacy_comworker_session_index(ctx)
        for session_key, record in legacy_index.items():
            if not isinstance(session_key, str) or session_key in seen_keys:
                continue
            summary = self._legacy_comworker_session_summary(session_key, record)
            if summary is not None:
                summaries.append(summary)
        return summaries

    async def get_session(self, ctx: RuntimeContext, session_key: str):
        try:
            payload = await self._session_record(ctx, session_key)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            legacy = await self._legacy_comworker_session(ctx, session_key)
            if legacy is not None:
                return legacy
            if _is_generated_comworker_session_key(session_key):
                return _empty_comworker_session(session_key)
            raise
        messages = payload.get("messages")
        if not isinstance(messages, list):
            messages = []
        messages = sanitize_hermes_messages(messages)
        raw_title = payload.get("title") or payload.get("session_id", session_key)
        if not raw_title or _looks_like_raw_session_id(raw_title):
            raw_title = _title_from_messages(messages) or raw_title
        return {
            "key": payload.get("session_id", session_key),
            "sessionKey": payload.get("session_id", session_key),
            "title": "" if _looks_like_raw_session_id(raw_title) else raw_title,
            "messages": messages,
            "messageCount": payload.get("message_count", len(messages)),
            "created_at": payload.get("created_at"),
            "createdAt": payload.get("created_at"),
            "updated_at": payload.get("updated_at") or payload.get("last_message_at") or payload.get("created_at"),
            "updatedAt": payload.get("updated_at") or payload.get("last_message_at") or payload.get("created_at"),
        }

    def _conversation_history_from_messages(self, messages: list[Any]) -> list[dict[str, str]]:
        history = []
        for item in sanitize_hermes_messages(messages):
            role = str(item.get("role") or "").strip()
            content = item.get("content")
            if role not in {"user", "assistant", "system"} or not isinstance(content, str):
                continue
            content = content.strip()
            if not content:
                continue
            history.append({"role": role, "content": content})
        return history

    def _build_context_instruction(self, context: Any | None) -> str | None:
        """Turn a client-selected context binding into an instruction fragment.

        ``context`` is a SessionContextItem-like dict: {"type": "skill"|"connector",
        "id": ..., "name": ...}. Experts are bound by switching the active agent
        in the client, so they are never passed here.
        """
        if not isinstance(context, dict):
            return None
        ctype = (context.get("type") or "").strip().lower()
        name = (context.get("name") or context.get("id") or "").strip()
        if not ctype or not name:
            return None
        if ctype == "skill":
            return (
                f"上下文绑定（技能）：用户已选择使用技能「{name}」。\n"
                f"- 请优先使用「{name}」技能来完成本次任务。\n"
                f"- 若用户的需求与「{name}」技能的能力匹配，直接调用该技能，不要忽略它。"
            )
        if ctype == "connector":
            return (
                f"上下文绑定（连接器）：用户已选择使用连接器「{name}」。\n"
                f"- 若该连接器提供了可用的工具（MCP 工具）或命令行（CLI）能力，请优先使用它来完成与「{name}」相关的任务。\n"
                f"- 例如用户提到「{name}」相关的操作时，直接调用对应工具/命令行，不要当作普通对话处理。"
            )
        return None

    async def _conversation_history(self, ctx: RuntimeContext, session_key: str) -> list[dict[str, str]]:
        if not session_key:
            return []
        try:
            payload = await self._session_record(ctx, session_key)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                logger.debug(
                    "hermes_session_history_unavailable scope=%s user_id=%s session_key=%s status=%s",
                    RUNTIME_SCOPE,
                    ctx.user.id,
                    session_key,
                    exc.status_code,
                )
            return []
        except Exception as exc:
            logger.debug(
                "hermes_session_history_unavailable scope=%s user_id=%s session_key=%s error=%s",
                RUNTIME_SCOPE,
                ctx.user.id,
                session_key,
                exc,
            )
            return []
        messages = payload.get("messages") if isinstance(payload, dict) else []
        return self._conversation_history_from_messages(messages if isinstance(messages, list) else [])

    async def send_message(
        self,
        ctx: RuntimeContext,
        session_key: str,
        message: str,
        model: str | None = None,
        title: str | None = None,
        context: Any | None = None,
    ) -> dict:
        started_at = time.perf_counter()
        agent_id = agent_id_from_session_key(session_key)
        conversation_history = await self._conversation_history(ctx, session_key)
        is_first_turn = not conversation_history
        if is_first_turn:
            title = (title or _fallback_title(message)) if (title or message) else None
        else:
            title = None
        async with async_session() as db:
            container = await ensure_running(db, ctx.user.id)
            # hermes 直连型 custom provider（如用户自建的火山 Ark 端点）期望纯模型名
            # （glm-5.2），而 platform 内部统一用 "provider/model" 路由格式。若把
            # "huoshan/glm-5.2" 原样透传给 hermes，hermes 会把它当作模型名发给上游，
            # 火山返回 404 "does not support the coding plan feature"。
            # 仅当 provider 段属于 gateway 托管的 provider（platform-gateway 路由，
            # 例如 openai/gpt-5）时保留前缀。
            effective_model = model
            if model and "/" in model:
                _prefix = model.split("/", 1)[0]
                _gw_ids = set()
                try:
                    from app.db.models import ModelProviderConfig
                    from sqlalchemy import select
                    _rows = (await db.execute(select(ModelProviderConfig.id))).scalars().all()
                    _gw_ids = {str(x) for x in _rows}
                except Exception:
                    _gw_ids = set()
                if _prefix not in _gw_ids:
                    effective_model = model.split("/", 1)[1]
        instructions = agent_identity_prompt_from_hermes_container(container.docker_id, agent_id)
        knowledge_write_instructions = (
            "Knowledge base write policy:\n"
            f"- This Agent's knowledge base root is /opt/data/profiles/{agent_id}/workspace/knowledge/.\n"
            "- When the user asks to create, edit, or organize knowledge-base content, write Markdown files under that knowledge root.\n"
            "- Do not create or modify skills for ordinary knowledge-base content unless the user explicitly asks to create a reusable skill.\n"
            "- Do not ask the user to run sudo/chown for knowledge-base writes; use the writable knowledge root path."
        )
        instructions = f"{instructions}\n\n{knowledge_write_instructions}" if instructions else knowledge_write_instructions
        knowledge_context = build_knowledge_context(container.docker_id, agent_id, message)
        if knowledge_context:
            instructions = f"{instructions}\n\n{knowledge_context}" if instructions else knowledge_context
        # 语言指令置顶： reasoning 模型有时会忽略后置指令，置顶并额外插入 system
        # message 可最大限度约束思考过程使用中文。
        instructions = f"{_LANGUAGE_INSTRUCTION}\n\n{instructions}" if instructions else _LANGUAGE_INSTRUCTION
        if conversation_history and conversation_history[0].get("role") != "system":
            conversation_history = [{"role": "system", "content": _LANGUAGE_INSTRUCTION}, *conversation_history]
        elif not conversation_history:
            conversation_history = [{"role": "system", "content": _LANGUAGE_INSTRUCTION}]
        # 会话级上下文绑定（客户端上下文选择器）：技能/连接器被选中后，明确告诉模型
        # 优先使用对应能力。每次发送都注入，确保会话中途切换上下文仍生效。
        context_instruction = self._build_context_instruction(context)
        if context_instruction:
            instructions = f"{instructions}\n\n{context_instruction}" if instructions else context_instruction
        payload = await (await self._client(ctx)).create_run(
            message=message,
            session_id=session_key or None,
            session_key=session_key or None,
            # When the caller did not pick an explicit model (model is None or the
            # "hermes-agent" placeholder), pass None so the engine falls back to its
            # configured default model instead of a literal placeholder id.
            model=effective_model if (effective_model and effective_model != "hermes-agent") else None,
            conversation_history=conversation_history,
            instructions=instructions,
        )
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        effective_session_key = payload.get("session_id") if isinstance(payload, dict) else None
        if title and (effective_session_key or session_key):
            if run_id:
                self._pending_titles_by_run[run_id] = (effective_session_key or session_key, title)
            try:
                await self.rename_session(ctx, effective_session_key or session_key, title)
            except Exception as exc:
                logger.debug(
                    "hermes_session_title_persist_failed scope=%s user_id=%s session_key=%s error=%s",
                    RUNTIME_SCOPE,
                    ctx.user.id,
                    effective_session_key or session_key,
                    exc,
                )
        if run_id:
            self._agent_id_by_run[run_id] = agent_id
        logger.info(
            "hermes_run_started scope=%s user_id=%s session_key=%s run_id=%s elapsed_ms=%.1f",
            RUNTIME_SCOPE,
            ctx.user.id,
            effective_session_key or session_key,
            run_id or "",
            _elapsed_ms(started_at),
        )
        return {
            "ok": True,
            "run_id": run_id or "",
            "runId": run_id or "",
            "session_key": effective_session_key or session_key,
            "sessionKey": effective_session_key or session_key,
            "title": title,
            "raw": payload if isinstance(payload, dict) else {},
        }

    async def wait_run(self, ctx: RuntimeContext, run_id: str, timeout_ms: int):
        started_at = time.perf_counter()
        timing = HermesRunTimingTracker(lambda: _elapsed_ms(started_at))
        events: list[dict[str, Any]] = []

        try:
            events = await (await self._client(ctx)).collect_run_events(
                run_id,
                timeout_ms=timeout_ms,
                on_event=timing.record,
            )
            events = sanitize_run_events(events)
            status_text, final_message = summarize_run_events(events)
        except Exception as exc:
            # The hermes events endpoint returns 401 (missing key) or 404
            # ("Run not found" for an already-completed run), so collecting
            # events can fail even though the run is done. Fall back to the
            # pollable run-status endpoint, which always returns the real
            # status + the specific failure reason (e.g. "token plan quota
            # exhausted"). Without this, the failure is silently swallowed and
            # the user never sees why the agent failed.
            logger.warning(
                "hermes_collect_run_events_failed scope=%s user_id=%s run_id=%s error=%s",
                RUNTIME_SCOPE,
                ctx.user.id,
                run_id,
                exc,
            )
            status_text, final_message = await self._resolve_run_failure(ctx, run_id, exc)
        pending_title = self._pending_titles_by_run.pop(run_id, None)
        if pending_title:
            session_key, title = pending_title
            try:
                await self.rename_session(ctx, session_key, title)
            except Exception as exc:
                logger.debug(
                    "hermes_session_title_persist_after_run_failed scope=%s user_id=%s session_key=%s run_id=%s error=%s",
                    RUNTIME_SCOPE,
                    ctx.user.id,
                    session_key,
                    run_id,
                    exc,
                )
        logger.info(
            "hermes_run_finished scope=%s user_id=%s run_id=%s status=%s first_event_ms=%s first_delta_ms=%s first_visible_delta_ms=%s elapsed_ms=%.1f event_count=%d",
            RUNTIME_SCOPE,
            ctx.user.id,
            run_id,
            status_text,
            format_latency_ms(timing.first_event_ms),
            format_latency_ms(timing.first_delta_ms),
            format_latency_ms(timing.first_visible_delta_ms),
            _elapsed_ms(started_at),
            len(events),
        )
        return {
            "run_id": run_id,
            "status": status_text,
            "message": final_message,
            "events": events,
        }

    async def _resolve_run_failure(
        self, ctx: RuntimeContext, run_id: str, original_exc: Exception
    ) -> tuple[str, dict[str, Any]]:
        """Best-effort recovery of the real run status + failure reason.

        Used when `collect_run_events` fails (401/404/timeouts on the SSE
        endpoint). The run-status endpoint (`GET /v1/runs/{run_id}`) is
        pollable and returns `status` + `error` even for completed runs, so it
        is the authoritative fallback for surfacing the specific reason a run
        failed.
        """
        try:
            status = await (await self._client(ctx)).request("GET", f"/v1/runs/{run_id}")
        except Exception as inner:
            logger.warning(
                "hermes_run_status_fallback_failed scope=%s user_id=%s run_id=%s error=%s",
                RUNTIME_SCOPE,
                ctx.user.id,
                run_id,
                inner,
            )
            return "failed", {
                "role": "system",
                "content": f"Agent 执行出错：{original_exc}",
            }

        if not isinstance(status, dict):
            return "failed", {
                "role": "system",
                "content": f"Agent 执行出错：{original_exc}",
            }

        st = status.get("status")
        err = status.get("error")
        if isinstance(err, str) and err.strip():
            # Surface the upstream-specific error verbatim (e.g. "HTTP 500:
            # token plan quota exhausted" or "No provider configured for
            # model 'X'"), so the UI can show the exact reason instead of a
            # generic message.
            return (st or "failed"), {"role": "system", "content": err.strip()}
        return (st or "failed"), {}

    async def rename_session(self, ctx: RuntimeContext, session_key: str, title: str):
        payload = await self._request(
            ctx,
            "PUT",
            f"/api/hermes/sessions/{session_key}/title",
            json={"title": title},
        )
        if isinstance(payload, dict):
            return payload
        return {"ok": True, "session_key": session_key, "title": title}

    async def delete_session(self, ctx: RuntimeContext, session_key: str):
        payload = await self._request(
            ctx,
            "DELETE",
            f"/api/hermes/sessions/{session_key}",
        )
        if isinstance(payload, dict):
            return payload
        return {"ok": True, "session_key": session_key}

    async def abort_run(self, ctx: RuntimeContext, run_id: str, session_key: str = "") -> dict:
        """Stop a running agent via POST /v1/runs/{run_id}/stop."""
        try:
            payload = await (await self._client(ctx)).request(
                "POST", f"/v1/runs/{run_id}/stop", timeout=10.0,
            )
            return {
                "ok": True,
                "aborted": True,
                "runIds": [run_id],
            }
        except Exception as exc:
            logger.warning("abort_run failed run_id=%s: %s", run_id, exc)
            return {"ok": False, "aborted": False, "runIds": []}

    async def abort_active_session(self, ctx: RuntimeContext, session_key: str) -> dict:
        """Best-effort abort: no direct hermes API for session-level abort."""
        return {"ok": True, "aborted": False, "runIds": []}

    async def respond_run_approval(
        self,
        ctx: RuntimeContext,
        run_id: str,
        choice: str,
        resolve_all: bool = False,
    ) -> dict | list | str:
        payload = await (await self._client(ctx)).request(
            "POST",
            f"/v1/runs/{run_id}/approval",
            json={"choice": choice, "resolve_all": resolve_all},
            timeout=10.0,
        )
        return payload if isinstance(payload, (dict, list, str)) else {"ok": True}

    async def list_commands(self, ctx: RuntimeContext, agent_id: str = "") -> dict:
        async with async_session() as db:
            container = await ensure_running(db, ctx.user.id)
        return list_hermes_commands_from_container(container.docker_id, agent_id or "main")

    async def upload_file(
        self,
        ctx: RuntimeContext,
        file: UploadFile,
        target_dir: str | None = None,
    ) -> dict:
        async with async_session() as db:
            container = await ensure_running(db, ctx.user.id)

        payload = await write_upload_to_hermes_container(
            container.docker_id,
            file,
            target_dir or DEFAULT_HERMES_UPLOAD_DIR,
        )
        payload["url"] = f"/api/comworker/filemanager/serve?path=/{payload['path']}"
        return payload

    def _map_event_to_compat_block(self, event: dict[str, Any]) -> str | None:
        event_type = str(event.get("type", ""))
        session_key = event.get("session_id") or event.get("session_key")
        payload: dict[str, Any]

        if event_type == "message.delta":
            delta = event.get("delta")
            if not delta:
                return None
            payload = {
                "event": "chat",
                "payload": {
                    "state": "delta",
                    "sessionKey": session_key,
                    "message": {"content": delta},
                },
            }
        elif event_type == "message.completed":
            message = event.get("message")
            if not isinstance(message, dict):
                return None
            message = sanitize_hermes_message(message)
            payload = {
                "event": "chat",
                "payload": {
                    "state": "final",
                    "sessionKey": session_key,
                    "message": message,
                },
            }
        elif event_type == "run.failed":
            payload = {
                "event": "chat",
                "payload": {
                    "state": "error",
                    "sessionKey": session_key,
                    "detail": event.get("error") or event.get("message") or "run failed",
                },
            }
        else:
            return None
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def stream_events(self, ctx: RuntimeContext, request: Request, token: str):
        payload = decode_token(token)
        if payload is None or payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        async with async_session() as db:
            user = await get_user_by_id(db, payload["sub"])
            if user is None or not user.is_active:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")

        stream_ctx = RuntimeContext(user=user, scope=ctx.scope)
        base_url = await self._resolve_base_url(stream_ctx)
        target_url = f"{base_url}/api/hermes/events/stream"
        headers = {}
        if settings.dedicated_hermes_api_key:
            headers["Authorization"] = f"Bearer {settings.dedicated_hermes_api_key}"

        async def _stream_sse():
            sanitizer = HermesEventSanitizer()
            async with httpx.AsyncClient(timeout=None) as client:
                try:
                    async with client.stream("GET", target_url, headers=headers) as resp:
                        if resp.status_code >= 400:
                            yield b'data: {"error":"dedicated hermes upstream error"}\n\n'
                            return
                        buffer = ""
                        async for chunk in resp.aiter_text():
                            if await request.is_disconnected():
                                break
                            buffer += chunk
                            while "\n\n" in buffer:
                                block, buffer = buffer.split("\n\n", 1)
                                data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
                                if not data_lines:
                                    continue
                                try:
                                    event = json.loads("\n".join(data_lines))
                                except json.JSONDecodeError:
                                    continue
                                if not isinstance(event, dict):
                                    continue
                                event = sanitizer.sanitize_event(event)
                                if event is None:
                                    continue
                                mapped = self._map_event_to_compat_block(event)
                                if mapped:
                                    yield mapped.encode("utf-8")
                except (httpx.ConnectError, httpx.RemoteProtocolError):
                    yield b'data: {"error":"dedicated hermes upstream disconnected"}\n\n'

        return StreamingResponse(
            _stream_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def stream_run_events(self, ctx: RuntimeContext, request: Request, token: str, run_id: str):
        payload = decode_token(token)
        if payload is None or payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        async with async_session() as db:
            user = await get_user_by_id(db, payload["sub"])
            if user is None or not user.is_active:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")

        stream_ctx = RuntimeContext(user=user, scope=ctx.scope)
        target_url = f"{await self._resolve_base_url(stream_ctx)}/v1/runs/{run_id}/events"
        headers = {}
        if settings.dedicated_hermes_api_key:
            headers["Authorization"] = f"Bearer {settings.dedicated_hermes_api_key}"

        async def _stream_sse():
            sanitizer = HermesEventSanitizer()
            async with httpx.AsyncClient(timeout=None) as client:
                try:
                    async with client.stream("GET", target_url, headers=headers) as resp:
                        if resp.status_code >= 400:
                            # The hermes events endpoint returns 404 ("Run not
                            # found") for an already-completed run, which is the
                            # common case when a run fails fast. Recover the real
                            # failure reason from the pollable run-status
                            # endpoint so the client shows the specific error
                            # (e.g. "token plan quota exhausted") instead of a
                            # generic one.
                            try:
                                status_resp = await client.get(
                                    f"{await self._resolve_base_url(stream_ctx)}/v1/runs/{run_id}",
                                    headers=headers,
                                )
                                if status_resp.status_code < 400:
                                    st = status_resp.json()
                                    if isinstance(st, dict):
                                        run_status = str(st.get("status") or "")
                                        if run_status in {"ok", "completed", "succeeded", "success"}:
                                            # 已成功结束的 run 重连 events：发 run.completed 而非误报 failed
                                            yield b'data: {"event":"run.completed"}\n\n'
                                            return
                                        err = st.get("error")
                                        if isinstance(err, str) and err.strip():
                                            yield (
                                                'data: {"event":"run.failed","error":'
                                                f"{json.dumps(err.strip())}}}\n\n"
                                            ).encode("utf-8")
                                            return
                            except Exception:
                                logger.debug(
                                    "hermes_run_status_fallback_in_sse_failed run_id=%s", run_id
                                )
                            yield b'data: {"event":"run.failed","error":"dedicated hermes upstream error"}\n\n'
                            return
                        buffer = ""
                        async for chunk in resp.aiter_bytes():
                            if await request.is_disconnected():
                                break
                            buffer += chunk.decode("utf-8", errors="ignore")
                            while "\n\n" in buffer:
                                block, buffer = buffer.split("\n\n", 1)
                                sanitized = sanitize_sse_block(block, sanitizer)
                                if sanitized:
                                    yield sanitized.encode("utf-8")
                except (httpx.ConnectError, httpx.RemoteProtocolError):
                    yield b'data: {"event":"run.failed","error":"dedicated hermes upstream disconnected"}\n\n'

        return StreamingResponse(
            _stream_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
