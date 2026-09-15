"""Agent 协议网关：把 ComWorker Agent 以 OpenAI / Anthropic 协议对外暴露。

为什么自建这一层（而非直接透传 hermes 的 /v1/chat/completions）：
- 复用 ComWorker 自己的 run 通道（send_message + collect_run_events），从而保留
  agent 身份注入、知识库上下文、中文思考指令、上下文选择器、会话持久化等全部价值；
- ``model`` 字段即 agent id，客户端零改造即可切换不同 agent；
- 审批自动批准（最大权限、零人工介入），可直接嵌入无人值守工作流；
- 统一到 GatewayToken 认证体系（可吊销、限协议、限 agent），与 /gateway 页同源。

协议细节（已查证）：
- OpenAI SDK：``base_url`` 作前缀 → ``<base_url>/chat/completions``、``/models``；
  流式用 ``data: {json}\\n\\n``（无 event: 字段）。
- Anthropic SDK：``base_url`` 作根，自动追加 ``/v1/messages``；
  流式事件顺序 message_start → content_block_start → content_block_delta →
  content_block_stop → message_delta → message_stop。
客户端配置：OpenAI ``base_url=http://host:8080/api/agent/v1``；Anthropic
``base_url=http://host:8080/api/agent``（不要自加 /v1）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.container.manager import ensure_running
from app.db.engine import async_session
from app.db.models import User
from app.gateway.access import authenticate
from app.runtime_backends.hermes_agents import list_agents_from_container
from app.runtime_backends.hermes_run import HermesEventSanitizer
from app.runtime_backend import RuntimeContext
from app.runtime_router import get_runtime_backend
from app.routes.agent_protocol import (
    SSE_DONE,
    ProtocolError,
    anthropic_content_block_delta,
    anthropic_content_block_start,
    anthropic_content_block_stop,
    anthropic_message_delta,
    anthropic_message_start,
    anthropic_message_stop,
    anthropic_error,
    anthropic_nonstream_response,
    format_sse,
    openai_delta_chunk,
    openai_error,
    openai_nonstream_response,
    openai_start_chunk,
    openai_stop_chunk,
    parse_anthropic_messages_request,
    parse_openai_chat_request,
)

router = APIRouter(prefix="/api/agent/v1", tags=["agent-protocol"])

# 单次 run 的流式收集超时（ms）。coding agent 等长任务可能跑数分钟，给足余量。
RUN_TIMEOUT_MS = 300_000


# ---------------------------------------------------------------------------
# 认证与授权
# ---------------------------------------------------------------------------

@dataclass
class AgentAuth:
    user: User
    allowed_agents: list[str]
    allowed_protocols: list[str]


async def require_agent_token(request: Request) -> AgentAuth:
    """校验 GatewayToken（Bearer 或 x-api-key），返回解析后的授权上下文。

    复用 gateway.access.authenticate：它做 SHA256 比对 + 启用/吊销/过期校验，
    并返回已 join User 的 GatewayToken（含 user_id、allowed_agents、
    allowed_protocols）。
    """
    async with async_session() as db:
        token = await authenticate(db, request.headers)
        user = await db.scalar(select(User).where(User.id == token.user_id))
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="令牌所属用户不存在或已停用")
        allowed_agents = list(token.allowed_agents or [])
        allowed_protocols = list(token.allowed_protocols or [])
    return AgentAuth(user=user, allowed_agents=allowed_agents, allowed_protocols=allowed_protocols)


def _assert_protocol(auth: AgentAuth, protocol: str) -> None:
    if auth.allowed_protocols and protocol not in auth.allowed_protocols:
        raise HTTPException(status_code=403, detail=f"该令牌未授权使用 {protocol} 协议")


def _assert_agent(auth: AgentAuth, agent_id: str) -> None:
    if auth.allowed_agents and agent_id not in auth.allowed_agents:
        raise HTTPException(status_code=403, detail=f"该令牌未授权访问 agent: {agent_id}")


# ---------------------------------------------------------------------------
# 请求辅助
# ---------------------------------------------------------------------------

def _make_session_key(req: Any, request: Request) -> str:
    """每个请求默认新建会话；可借 X-ComWorker-Session-Key 头或 ?session= 查询参数复用既有会话做多轮对话。

    优先用头（更安全，不落日志），查询参数作为对标准 OpenAI/Anthropic SDK 友好的等价替代
    （SDK 不支持任意自定义头，但支持 default_query 追加查询参数）。
    """
    explicit = request.headers.get("x-comworker-session-key") or request.query_params.get("session")
    if explicit:
        return explicit
    return f"agent:{req.agent_id}:session-{uuid.uuid4().hex}"


def _parse_context_header(request: Request) -> Any | None:
    """X-ComWorker-Context: JSON，透传给 send_message 的上下文选择器（skills/connectors/agents）。"""
    raw = request.headers.get("x-comworker-context")
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def _openai_sse(chunk: dict) -> str:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def _final_text(result: Any) -> str:
    """从 wait_run 的返回里提取最终助手文本（已 sanitize、思考块已剥离）。"""
    if not isinstance(result, dict):
        return ""
    msg = result.get("message") or {}
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
    if result.get("status") == "failed":
        inner = result.get("message") or {}
        return str(inner.get("content", "Agent 执行失败") if isinstance(inner, dict) else "Agent 执行失败")
    return ""


def _protocol_error_response(protocol: str, exc: ProtocolError) -> JSONResponse:
    if protocol == "openai":
        status_code, body = openai_error(exc.status_code, exc.openai_type, exc.message)
    else:
        status_code, body = anthropic_error(exc.status_code, exc.anthropic_type, exc.message)
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------------------
# 流式编排：把 hermes run 事件实时翻译成协议 chunk
# ---------------------------------------------------------------------------

async def _stream_run(protocol: str, req: Any, ctx: RuntimeContext, backend: Any,
                      run_id: str, session_key: str):
    sanitizer = HermesEventSanitizer()
    queue: asyncio.Queue = asyncio.Queue()
    model = req.agent_id
    # emitted: 是否真的把增量 delta 推给了客户端；final_text: run 结束时兜底用的完整文本。
    # hermes 某些 agent/run 模式不发增量 message.delta，只在 message.completed /
    # run.completed 给最终文本——此时用 final_text 补发一段，保证流式也有内容。
    state: dict = {"emitted": False, "final_text": None}

    async def on_event(ev: dict) -> None:
        sanitized = sanitizer.sanitize_event(ev)
        if sanitized is None:
            return
        etype = sanitized.get("type") or sanitized.get("event")
        if etype == "message.delta":
            delta = sanitized.get("delta")
            if isinstance(delta, dict):
                delta = delta.get("text") or delta.get("content") or ""
            if isinstance(delta, str) and delta:
                state["emitted"] = True
                await queue.put(delta)
        elif etype == "message.completed":
            msg = sanitized.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                c = msg.get("content")
                if isinstance(c, str) and c:
                    state["final_text"] = c
        elif etype == "run.completed":
            out = sanitized.get("output")
            if isinstance(out, str) and out:
                state["final_text"] = out
            elif isinstance(out, dict):
                c = out.get("content")
                if isinstance(c, str) and c:
                    state["final_text"] = c
        elif etype == "approval.request":
            # 最大权限、零人工介入：自动批准，run 继续；失败不影响主流程。
            try:
                await backend.respond_run_approval(ctx, run_id, "approve", resolve_all=True)
            except Exception:
                pass

    async def runner() -> None:
        try:
            await backend.collect_run_events_stream(ctx, run_id, on_event, timeout_ms=RUN_TIMEOUT_MS)
        except Exception:
            # 事件流异常（如 run 已完成被 404）：回退取最终文本，至少给客户端一个完整结果。
            try:
                res = await backend.wait_run(ctx, run_id, RUN_TIMEOUT_MS)
                text = _final_text(res)
                if text:
                    state["final_text"] = state["final_text"] or text
            except Exception:
                pass
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())

    if protocol == "openai":
        rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        yield _openai_sse(openai_start_chunk(model, rid))
        while True:
            item = await queue.get()
            if item is None:
                break
            state["emitted"] = True
            yield _openai_sse(openai_delta_chunk(model, item, rid))
        if not state["emitted"] and state["final_text"]:
            yield _openai_sse(openai_delta_chunk(model, state["final_text"], rid))
        yield _openai_sse(openai_stop_chunk(model, rid))
        yield SSE_DONE
    else:  # anthropic
        rid = f"msg_{uuid.uuid4().hex[:12]}"
        yield format_sse(*anthropic_message_start(model, rid))
        yield format_sse(*anthropic_content_block_start())
        while True:
            item = await queue.get()
            if item is None:
                break
            state["emitted"] = True
            yield format_sse(*anthropic_content_block_delta(item))
        if not state["emitted"] and state["final_text"]:
            yield format_sse(*anthropic_content_block_delta(state["final_text"]))
        yield format_sse(*anthropic_content_block_stop())
        yield format_sse(*anthropic_message_delta())
        yield format_sse(*anthropic_message_stop())


def _common_headers(run_id: str, session_key: str) -> dict:
    return {
        "Cache-Control": "no-cache",
        "X-ComWorker-Run-Id": run_id,
        "X-ComWorker-Session-Key": session_key,
    }


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/models")
async def list_models(request: Request):
    """OpenAI 风格的模型列表；id 即 agent id。"""
    auth = await require_agent_token(request)
    ids: list[str] = []
    if auth.allowed_agents:
        ids = list(auth.allowed_agents)
    else:
        try:
            async with async_session() as db:
                container = await ensure_running(db, auth.user.id)
            agents = list_agents_from_container(container.docker_id)
            ids = [a.get("id") for a in agents if isinstance(a, dict) and a.get("id")]
        except Exception:
            ids = []
    return {
        "object": "list",
        "data": [
            {
                "id": i,
                "object": "model",
                "created": 0,
                "owned_by": "comworker",
                "permission": [],
                "root": i,
                "parent": None,
            }
            for i in ids
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: Request):
    auth = await require_agent_token(request)
    _assert_protocol(auth, "openai")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "invalid JSON body", "type": "invalid_request_error"}})
    try:
        req = parse_openai_chat_request(body)
    except ProtocolError as e:
        return _protocol_error_response("openai", e)
    _assert_agent(auth, req.agent_id)

    backend = get_runtime_backend()
    ctx = RuntimeContext(user=auth.user, scope="dedicated")
    session_key = _make_session_key(req, request)
    context = _parse_context_header(request)
    model_override = request.headers.get("x-comworker-model") or None
    try:
        start = await backend.send_message(
            ctx, session_key, req.input, model=model_override, context=context
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent 启动失败: {e}")
    run_id = start.get("run_id")
    if not run_id:
        raise HTTPException(status_code=502, detail="Agent 未返回 run_id")

    if req.stream:
        return StreamingResponse(
            _stream_run("openai", req, ctx, backend, run_id, session_key),
            media_type="text/event-stream",
            headers=_common_headers(run_id, session_key),
        )
    result = await backend.wait_run(ctx, run_id, RUN_TIMEOUT_MS)
    return JSONResponse(
        openai_nonstream_response(req.agent_id, _final_text(result), session_key=session_key, run_id=run_id)
    )


@router.post("/messages")
async def anthropic_messages(request: Request):
    auth = await require_agent_token(request)
    _assert_protocol(auth, "anthropic")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"type": "error", "error": {"type": "invalid_request_error", "message": "invalid JSON body"}})
    try:
        req = parse_anthropic_messages_request(body)
    except ProtocolError as e:
        return _protocol_error_response("anthropic", e)
    _assert_agent(auth, req.agent_id)

    backend = get_runtime_backend()
    ctx = RuntimeContext(user=auth.user, scope="dedicated")
    session_key = _make_session_key(req, request)
    context = _parse_context_header(request)
    model_override = request.headers.get("x-comworker-model") or None
    try:
        start = await backend.send_message(
            ctx, session_key, req.input, model=model_override, context=context
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent 启动失败: {e}")
    run_id = start.get("run_id")
    if not run_id:
        raise HTTPException(status_code=502, detail="Agent 未返回 run_id")

    if req.stream:
        return StreamingResponse(
            _stream_run("anthropic", req, ctx, backend, run_id, session_key),
            media_type="text/event-stream",
            headers=_common_headers(run_id, session_key),
        )
    result = await backend.wait_run(ctx, run_id, RUN_TIMEOUT_MS)
    return JSONResponse(anthropic_nonstream_response(req.agent_id, _final_text(result)))
