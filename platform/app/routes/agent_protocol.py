"""Agent 协议网关的纯函数协议转换层。

职责：
- 把 OpenAI Chat Completions / Anthropic Messages 的**请求体**解析为内部表示 ``InternalChatRequest``；
- 把内部输出（文本增量、工具事件、最终文本）构造为符合 OpenAI / Anthropic 规范的响应
  （chunk dict、SSE event tuple、非流式 JSON、错误 JSON）。

设计约束（重要）：
- 本模块**不含任何 IO**（不连数据库、不连 hermes、不读请求头、不碰 ``request`` 对象）。
  这样它可以被纯单元测试覆盖，且路由层 ``agent_gateway.py`` 的任何改动都不会波及这里。
- run events 的**内部 schema** 隔离在将来写的 ``agent_gateway.py``：那里负责把
  ``GET /api/comworker/runs/{id}/events`` 的 SSE 提取成「文本增量 / 工具事件 / 最终文本」，
  再调用本模块的构造器产出协议格式。本模块只认「已提取好的文本」。
- 协议细节依据（已查证，2026-09-14）：
  - OpenAI SDK：``base_url`` 作前缀，请求 ``<base_url>/chat/completions``、``/models``；
    响应为 ``chat.completion`` / ``chat.completion.chunk``。
  - Anthropic SDK：``base_url`` 作根，自动追加 ``/v1/messages``；
    SSE 事件顺序 ``message_start → content_block_start → content_block_delta → content_block_stop
    → message_delta → message_stop``。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

SUPPORTED_PROTOCOLS = ("openai", "anthropic")


class ProtocolError(Exception):
    """请求体不合法时由解析函数抛出；路由层捕获后翻译成 HTTP 错误响应。"""

    def __init__(self, status_code: int, openai_type: str, message: str, anthropic_type: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.openai_type = openai_type
        self.anthropic_type = anthropic_type or openai_type
        self.message = message


# --------------------------------------------------------------------------
# 内部请求表示
# --------------------------------------------------------------------------

@dataclass
class InternalChatRequest:
    agent_id: str
    input: str
    history: list[dict] = field(default_factory=list)  # [{"role": "user"|"assistant", "content": str}]
    system: str = ""
    stream: bool = True
    max_tokens: Optional[int] = None
    session_key: Optional[str] = None  # 显式复用既有 session（X-ComWorker-Session-Key）
    session_hint: Optional[str] = None  # 规范化复用（X-ComWorker-Session）
    model_override: Optional[str] = None  # 覆盖底层 LLM（X-ComWorker-Model 头）
    show_reasoning: bool = False  # X-ComWorker-Reasoning 头


# --------------------------------------------------------------------------
# 内容扁平化辅助
# --------------------------------------------------------------------------

def _as_text(content: Any) -> str:
    """把 OpenAI/Anthropic 的 content（str 或 block 数组）压成纯文本。

    v1 忽略 image / tool_use / tool_result 之外的非文本块；tool_result 的内容递归扁平。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_result":
                    parts.append(_as_text(block.get("content", "")))
                # input_text / 其它块在 v1 不进入正文
        return "\n".join(p for p in parts if p)
    return str(content)


_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL)


def strip_think(text: str) -> str:
    """剥离 ``<think>...</think>`` 块（与 ComWorker 客户端默认一致；默认不透出思考过程）。"""
    return _THINK_RE.sub("", text).strip()


# --------------------------------------------------------------------------
# OpenAI 请求解析
# --------------------------------------------------------------------------

def parse_openai_chat_request(body: dict) -> InternalChatRequest:
    model = body.get("model")
    if not model or not isinstance(model, str):
        raise ProtocolError(400, "invalid_request_error", "model is required")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProtocolError(400, "invalid_request_error", "messages is required")
    system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if not non_system:
        raise ProtocolError(400, "invalid_request_error", "at least one non-system message is required")
    history = [
        {"role": m.get("role"), "content": _as_text(m.get("content", ""))}
        for m in non_system[:-1]
    ]
    last = non_system[-1]
    if last.get("role") != "user":
        raise ProtocolError(400, "invalid_request_error", "last message must be from user")
    return InternalChatRequest(
        agent_id=model,
        input=_as_text(last.get("content", "")),
        history=history,
        system="\n\n".join(p for p in system_parts if p),
        stream=bool(body.get("stream", False)),
        max_tokens=body.get("max_tokens"),
    )


# --------------------------------------------------------------------------
# Anthropic 请求解析
# --------------------------------------------------------------------------

def parse_anthropic_messages_request(body: dict) -> InternalChatRequest:
    model = body.get("model")
    if not model or not isinstance(model, str):
        raise ProtocolError(400, "invalid_request_error", "model is required")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ProtocolError(400, "invalid_request_error", "messages is required")
    system = _as_text(body.get("system", "")) if body.get("system") else ""
    non_system = list(messages)
    if not non_system:
        raise ProtocolError(400, "invalid_request_error", "at least one message is required")
    history = [
        {"role": m.get("role"), "content": _as_text(m.get("content", ""))}
        for m in non_system[:-1]
    ]
    last = non_system[-1]
    if last.get("role") != "user":
        raise ProtocolError(400, "invalid_request_error", "last message must be from user")
    return InternalChatRequest(
        agent_id=model,
        input=_as_text(last.get("content", "")),
        history=history,
        system=system,
        stream=bool(body.get("stream", False)),
        max_tokens=body.get("max_tokens"),
    )


# --------------------------------------------------------------------------
# OpenAI 响应构造
# --------------------------------------------------------------------------

def _openai_id() -> str:
    return uuid.uuid4().hex[:12]


def openai_start_chunk(model: str, rid: str | None = None) -> dict:
    rid = rid or f"chatcmpl-{_openai_id()}"
    return {
        "id": rid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }


def openai_delta_chunk(model: str, text: str, rid: str | None = None) -> dict:
    rid = rid or f"chatcmpl-{_openai_id()}"
    return {
        "id": rid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }


def openai_stop_chunk(model: str, rid: str | None = None) -> dict:
    rid = rid or f"chatcmpl-{_openai_id()}"
    return {
        "id": rid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


def openai_tool_event(tool: str, status: str, label: str | None = None,
                      tool_call_id: str | None = None, emoji: str | None = None) -> tuple[str, dict]:
    """自定义扩展事件（event: comworker.tool），不污染标准 chunk。返回 (event, data)。"""
    return ("comworker.tool", {
        "tool": tool,
        "status": status,
        "label": label,
        "emoji": emoji,
        "toolCallId": tool_call_id,
    })


def openai_nonstream_response(model: str, text: str, session_key: str | None = None,
                              run_id: str | None = None, duration_ms: int | None = None) -> dict:
    return {
        "id": f"chatcmpl-{_openai_id()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "comworker": {
            "session_key": session_key,
            "run_id": run_id,
            "duration_ms": duration_ms,
        },
    }


def openai_error(status_code: int, err_type: str, message: str) -> tuple[int, dict]:
    return status_code, {"error": {"message": message, "type": err_type, "param": None, "code": err_type}}


# --------------------------------------------------------------------------
# Anthropic 响应构造
# --------------------------------------------------------------------------

def _anthropic_id() -> str:
    return uuid.uuid4().hex[:12]


def anthropic_message_start(model: str, rid: str | None = None) -> tuple[str, dict]:
    rid = rid or f"msg_{_anthropic_id()}"
    return ("message_start", {
        "type": "message_start",
        "message": {
            "id": rid,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })


def anthropic_content_block_start(index: int = 0, rid: str | None = None) -> tuple[str, dict]:
    return ("content_block_start", {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    })


def anthropic_content_block_delta(text: str, index: int = 0) -> tuple[str, dict]:
    return ("content_block_delta", {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    })


def anthropic_content_block_stop(index: int = 0) -> tuple[str, dict]:
    return ("content_block_stop", {"type": "content_block_stop", "index": index})


def anthropic_message_delta(stop_reason: str = "end_turn") -> tuple[str, dict]:
    return ("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": 0},
    })


def anthropic_message_stop() -> tuple[str, dict]:
    return ("message_stop", {"type": "message_stop"})


def anthropic_tool_event(tool: str, status: str, label: str | None = None,
                         tool_call_id: str | None = None, emoji: str | None = None) -> tuple[str, dict]:
    return ("comworker.tool", {
        "tool": tool,
        "status": status,
        "label": label,
        "emoji": emoji,
        "toolCallId": tool_call_id,
    })


def anthropic_nonstream_response(model: str, text: str, rid: str | None = None) -> dict:
    return {
        "id": rid or f"msg_{_anthropic_id()}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def anthropic_error(status_code: int, err_type: str, message: str) -> tuple[int, dict]:
    return status_code, {"type": "error", "error": {"type": err_type, "message": message}}


# --------------------------------------------------------------------------
# SSE 序列化工具
# --------------------------------------------------------------------------

def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_KEEPALIVE = ": keepalive\n\n"
SSE_DONE = "data: [DONE]\n\n"
