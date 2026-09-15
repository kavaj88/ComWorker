"""Agent 协议网关路由纯函数测试（不依赖容器 / DB / 网络）。"""

import json

import pytest
from fastapi import HTTPException

from app.routes import agent_gateway as ag
from app.routes.agent_protocol import ProtocolError


class _FakeReq:
    def __init__(self, headers=None, agent_id="main"):
        self.headers = headers or {}
        self.agent_id = agent_id


def test_final_text_from_message():
    assert ag._final_text({"status": "completed", "message": {"role": "assistant", "content": "hi"}}) == "hi"


def test_final_text_failed_surface_error():
    res = {"status": "failed", "message": {"role": "system", "content": "boom"}}
    assert ag._final_text(res) == "boom"


def test_final_text_empty():
    assert ag._final_text({}) == ""
    assert ag._final_text(None) == ""


def test_openai_sse_format():
    chunk = {"object": "chat.completion.chunk", "choices": []}
    out = ag._openai_sse(chunk)
    assert out.startswith("data: ") and out.endswith("\n\n")
    assert "chat.completion.chunk" in out


def test_make_session_key_explicit_header():
    req = _FakeReq(agent_id="insurance")
    assert ag._make_session_key(req, _FakeReq(headers={"x-comworker-session-key": "agent:insurance:session-xyz"})) == "agent:insurance:session-xyz"


def test_make_session_key_generated():
    req = _FakeReq(agent_id="main")
    key = ag._make_session_key(req, _FakeReq())
    assert key.startswith("agent:main:session-")


def test_assert_protocol_blocks():
    auth = ag.AgentAuth(user=None, allowed_agents=[], allowed_protocols=["openai"])
    with pytest.raises(HTTPException) as exc:
        ag._assert_protocol(auth, "anthropic")
    assert exc.value.status_code == 403


def test_assert_protocol_allows_when_empty():
    auth = ag.AgentAuth(user=None, allowed_agents=[], allowed_protocols=[])
    ag._assert_protocol(auth, "anthropic")  # 不抛


def test_assert_agent_blocks():
    auth = ag.AgentAuth(user=None, allowed_agents=["main"], allowed_protocols=[])
    with pytest.raises(HTTPException) as exc:
        ag._assert_agent(auth, "insurance")
    assert exc.value.status_code == 403


def test_protocol_error_response_mapping():
    err = ProtocolError(400, "invalid_request_error", "model required", "invalid_request_error")
    oa = ag._protocol_error_response("openai", err)
    assert oa.status_code == 400
    assert json.loads(oa.body)["error"]["type"] == "invalid_request_error"
    an = ag._protocol_error_response("anthropic", err)
    assert an.status_code == 400
    assert json.loads(an.body)["type"] == "error"
