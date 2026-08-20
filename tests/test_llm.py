"""Tests for LLM proxy endpoint.

Covers:
- POST /llm/v1/chat/completions
- _resolve_provider model routing logic

Note: Requires a valid container token (api_token) to authenticate.
Tests verify authentication, request structure handling, and model routing.
"""

from conftest import admin_token, api_url, json_request


def _get_api_token() -> str:
    """Get a long-lived API token for the admin user."""
    token = admin_token()
    from conftest import auth_headers

    result = json_request(
        api_url("/api/auth/api-token"),
        method="POST",
        headers=auth_headers(token),
    )
    return result["api_token"]


# ---------------------------------------------------------------------------
# Auth / validation
# ---------------------------------------------------------------------------


def test_chat_completions_missing_auth():
    """Missing Bearer token should return 401."""
    try:
        json_request(
            api_url("/llm/v1/chat/completions"),
            method="POST",
            payload={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert False, "Expected 401"
    except RuntimeError as exc:
        assert any(str(code) in str(exc) for code in ("401", "403", "422"))


def test_chat_completions_missing_model():
    """Missing model field should return 400."""
    api_token = _get_api_token()
    try:
        json_request(
            api_url("/llm/v1/chat/completions"),
            method="POST",
            payload={"messages": [{"role": "user", "content": "hello"}]},
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert False, "Expected 400"
    except RuntimeError as exc:
        assert "400" in str(exc)


def test_chat_completions_invalid_json():
    """Invalid JSON should be rejected."""
    from urllib.request import Request

    try:
        import json

        req = Request(
            api_url("/llm/v1/chat/completions"),
            data=b"not json",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_get_api_token()}",
            },
            method="POST",
        )
        from urllib.request import urlopen

        urlopen(req, timeout=10)
        assert False, "Expected error"
    except Exception as exc:
        assert "400" in str(exc) or "HTTP" in str(type(exc).__name__)


# ---------------------------------------------------------------------------
# Model routing
# ---------------------------------------------------------------------------


def test_chat_completions_hermes_agent_is_routed():
    """hermes-agent (the internal Hermes model name) should not 400.
    It should be routed to the platform default model."""
    api_token = _get_api_token()
    try:
        result = json_request(
            api_url("/llm/v1/chat/completions"),
            method="POST",
            payload={
                "model": "hermes-agent",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert result is not None
    except RuntimeError as exc:
        err = str(exc)
        assert "No provider configured" not in err, (
            f"hermes-agent was NOT routed to default model: {err}"
        )
        assert "400" not in err or "provider" not in err.lower(), (
            f"Unexpected 400 during model routing: {err}"
        )


def test_chat_completions_vllm_routing():
    """vllm/Qwen3-0.6B should route via the vllm prefix, not dashscope/qwen keyword."""
    api_token = _get_api_token()
    try:
        result = json_request(
            api_url("/llm/v1/chat/completions"),
            method="POST",
            payload={
                "model": "vllm/Qwen3-0.6B",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 10,
            },
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert result is not None
    except RuntimeError as exc:
        err = str(exc)
        # Must NOT be a routing error
        assert "No provider configured" not in err, (
            f"vllm model routing failed: {err}"
        )
        # Must NOT be routed to dashscope (which would fail with auth error)
        assert "dashscope" not in err.lower(), (
            f"vllm/Qwen3-0.6B was misrouted to dashscope: {err}"
        )
