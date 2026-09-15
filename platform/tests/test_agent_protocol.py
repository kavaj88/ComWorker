"""agent_protocol.py 的纯函数契约测试（无 IO、无容器依赖）。"""

import os
import sys

# 保底把 platform/ 加入 path，使 `import app...` 在任何运行环境下都可行。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routes.agent_protocol import (  # noqa: E402
    InternalChatRequest,
    ProtocolError,
    anthropic_content_block_delta,
    anthropic_content_block_start,
    anthropic_content_block_stop,
    anthropic_error,
    anthropic_message_delta,
    anthropic_message_start,
    anthropic_message_stop,
    anthropic_nonstream_response,
    format_sse,
    openai_delta_chunk,
    openai_error,
    openai_nonstream_response,
    openai_start_chunk,
    openai_stop_chunk,
    parse_anthropic_messages_request,
    parse_openai_chat_request,
    strip_think,
)


# --- OpenAI 请求解析 ---

def test_parse_openai_basic():
    req = parse_openai_chat_request({"model": "main", "messages": [{"role": "user", "content": "hi"}]})
    assert req.agent_id == "main"
    assert req.input == "hi"
    assert req.history == []
    assert req.stream is False


def test_parse_openai_system_and_history():
    req = parse_openai_chat_request({
        "model": "insurance",
        "messages": [
            {"role": "system", "content": "你是保险助手"},
            {"role": "user", "content": "之前问了啥"},
            {"role": "assistant", "content": "你问了价格"},
            {"role": "user", "content": "再问一次"},
        ],
        "stream": True,
    })
    assert req.system == "你是保险助手"
    assert req.history == [
        {"role": "user", "content": "之前问了啥"},
        {"role": "assistant", "content": "你问了价格"},
    ]
    assert req.input == "再问一次"
    assert req.stream is True


def test_parse_openai_requires_model():
    try:
        parse_openai_chat_request({"messages": [{"role": "user", "content": "x"}]})
        assert False, "should raise"
    except ProtocolError as e:
        assert e.status_code == 400


def test_parse_openai_last_must_be_user():
    try:
        parse_openai_chat_request({"model": "m", "messages": [{"role": "assistant", "content": "x"}]})
        assert False, "should raise"
    except ProtocolError as e:
        assert e.status_code == 400


def test_parse_openai_content_blocks():
    req = parse_openai_chat_request({
        "model": "m",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "你好"}]}],
    })
    assert req.input == "你好"


# --- Anthropic 请求解析 ---

def test_parse_anthropic_basic():
    req = parse_anthropic_messages_request({
        "model": "main",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert req.agent_id == "main"
    assert req.input == "hi"
    assert req.stream is False


def test_parse_anthropic_system_blocks():
    req = parse_anthropic_messages_request({
        "model": "m",
        "max_tokens": 100,
        "system": [{"type": "text", "text": "系统提示"}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "问题"}]}],
    })
    assert req.system == "系统提示"
    assert req.input == "问题"


def test_parse_anthropic_max_tokens_passthrough():
    req = parse_anthropic_messages_request({"model": "m", "messages": [{"role": "user", "content": "x"}]})
    assert req.max_tokens is None


# --- OpenAI 响应构造 ---

def test_openai_chunk_sequence():
    rid = "chatcmpl-test"
    start = openai_start_chunk("main", rid)
    assert start["object"] == "chat.completion.chunk"
    assert start["choices"][0]["delta"] == {"role": "assistant"}
    delta = openai_delta_chunk("main", "你", rid)
    assert delta["choices"][0]["delta"] == {"content": "你"}
    assert delta["id"] == rid
    stop = openai_stop_chunk("main", rid)
    assert stop["choices"][0]["finish_reason"] == "stop"


def test_openai_nonstream():
    resp = openai_nonstream_response(
        "main", "你好", session_key="agent:main:session-1", run_id="r1", duration_ms=123
    )
    assert resp["object"] == "chat.completion"
    assert resp["choices"][0]["message"]["content"] == "你好"
    assert resp["comworker"]["run_id"] == "r1"
    assert resp["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_openai_error():
    status, body = openai_error(401, "invalid_api_key", "bad")
    assert status == 401
    assert body["error"]["type"] == "invalid_api_key"


# --- Anthropic 响应构造（事件顺序严格）---

def test_anthropic_sse_sequence():
    rid = "msg-test"
    seq = [
        anthropic_message_start("main", rid),
        anthropic_content_block_start(0, rid),
        anthropic_content_block_delta("你", 0),
        anthropic_content_block_stop(0),
        anthropic_message_delta(),
        anthropic_message_stop(),
    ]
    types = [ev[1]["type"] for ev in seq]
    assert types == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert seq[0][1]["message"]["id"] == rid


def test_anthropic_nonstream():
    resp = anthropic_nonstream_response("main", "你好")
    assert resp["type"] == "message"
    assert resp["content"][0]["text"] == "你好"
    assert resp["stop_reason"] == "end_turn"


def test_anthropic_error():
    status, body = anthropic_error(401, "authentication_error", "bad")
    assert status == 401
    assert body["type"] == "error"
    assert body["error"]["type"] == "authentication_error"


# --- SSE 序列化 ---

def test_format_sse():
    s = format_sse("message_stop", {"type": "message_stop"})
    assert s == 'event: message_stop\ndata: {"type": "message_stop"}\n\n'


# --- 思考块剥离 ---

def test_strip_think():
    assert strip_think("正常<think>思考过程</think>结尾") == "正常结尾"
    assert strip_think("<think>只思考</think>") == ""  # 全文都是思考块 → 剥离后为空
    assert strip_think("没有思考") == "没有思考"
