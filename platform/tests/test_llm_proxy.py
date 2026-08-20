from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.llm_proxy import service
from fastapi import HTTPException


def test_minimax_m27_routes_to_highspeed_by_default(monkeypatch):
    monkeypatch.setattr(service.settings, "minimax_api_key", "minimax-key")
    monkeypatch.setattr(service.settings, "minimax_m27_use_highspeed", True, raising=False)

    litellm_model, _api_key, _api_base, _extra_headers = service._resolve_provider(
        "minimax/MiniMax-M2.7"
    )

    assert litellm_model == "minimax/MiniMax-M2.7-highspeed"


def test_minimax_m27_highspeed_alias_can_be_disabled(monkeypatch):
    monkeypatch.setattr(service.settings, "minimax_api_key", "minimax-key")
    monkeypatch.setattr(service.settings, "minimax_m27_use_highspeed", False, raising=False)

    litellm_model, _api_key, _api_base, _extra_headers = service._resolve_provider(
        "minimax/MiniMax-M2.7"
    )

    assert litellm_model == "minimax/MiniMax-M2.7"


def test_unconfigured_explicit_provider_fails_as_non_retryable_client_error(monkeypatch):
    for attr in (
        "anthropic_api_key",
        "openai_api_key",
        "deepseek_api_key",
        "openrouter_api_key",
        "dashscope_api_key",
        "minimax_api_key",
        "aihubmix_api_key",
        "evolink_api_key",
        "moonshot_api_key",
        "kimi_api_key",
        "zhipu_api_key",
        "doubao_api_key",
        "hosted_vllm_api_key",
        "hosted_vllm_api_base",
    ):
        monkeypatch.setattr(service.settings, attr, "")

    with pytest.raises(HTTPException) as exc_info:
        service._resolve_provider("dashscope/qwen3-coder-plus")

    assert exc_info.value.status_code == 400
    assert "No provider configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_proxy_injects_platform_default_reasoning_effort(monkeypatch):
    captured_kwargs = {}

    monkeypatch.setattr(service.settings, "dev_comworker_url", "http://dev-comworker")
    monkeypatch.setattr(service.settings, "hermes_reasoning_effort", "none")
    monkeypatch.setattr(service.settings, "hermes_service_tier", "")
    monkeypatch.setattr(
        service,
        "_resolve_provider",
        lambda _model: ("openai/gpt-5.4", "openai-key", None, None),
    )
    monkeypatch.setattr(service, "_litellm_model_supports_param", lambda *args: True)

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(model_dump=lambda: {"ok": True}, usage=None)

    monkeypatch.setattr(service, "acompletion", fake_acompletion)

    await service.proxy_chat_completion(
        db=AsyncMock(),
        container_token="container-token",
        raw_request={
            "model": "openai/gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )

    assert captured_kwargs["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_proxy_skips_platform_default_reasoning_effort_when_provider_rejects_it(monkeypatch):
    captured_kwargs = {}

    monkeypatch.setattr(service.settings, "dev_comworker_url", "http://dev-comworker")
    monkeypatch.setattr(service.settings, "hermes_reasoning_effort", "none")
    monkeypatch.setattr(service.settings, "hermes_service_tier", "")
    monkeypatch.setattr(
        service,
        "_resolve_provider",
        lambda _model: ("minimax/MiniMax-M2.7-highspeed", "minimax-key", "https://api.minimax.io/v1", None),
    )

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(model_dump=lambda: {"ok": True}, usage=None)

    monkeypatch.setattr(service, "acompletion", fake_acompletion)

    await service.proxy_chat_completion(
        db=AsyncMock(),
        container_token="container-token",
        raw_request={
            "model": "minimax/MiniMax-M2.7",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )

    assert "reasoning_effort" not in captured_kwargs


@pytest.mark.asyncio
async def test_proxy_skips_reasoning_effort_for_custom_openai_compatible_base(monkeypatch):
    captured_kwargs = {}

    monkeypatch.setattr(service.settings, "dev_comworker_url", "http://dev-comworker")
    monkeypatch.setattr(service.settings, "hermes_reasoning_effort", "none")
    monkeypatch.setattr(service.settings, "hermes_service_tier", "")
    monkeypatch.setattr(
        service,
        "_resolve_provider",
        lambda _model: (
            "openai/qwen3-coder-plus",
            "dashscope-key",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            None,
        ),
    )

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(model_dump=lambda: {"ok": True}, usage=None)

    monkeypatch.setattr(service, "acompletion", fake_acompletion)

    await service.proxy_chat_completion(
        db=AsyncMock(),
        container_token="container-token",
        raw_request={
            "model": "dashscope/qwen3-coder-plus",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )

    assert "reasoning_effort" not in captured_kwargs


@pytest.mark.asyncio
async def test_proxy_keeps_explicit_request_reasoning_effort(monkeypatch):
    captured_kwargs = {}

    monkeypatch.setattr(service.settings, "dev_comworker_url", "http://dev-comworker")
    monkeypatch.setattr(service.settings, "hermes_reasoning_effort", "none")
    monkeypatch.setattr(service.settings, "hermes_service_tier", "priority")
    monkeypatch.setattr(
        service,
        "_resolve_provider",
        lambda _model: ("openai/gpt-5.4", "openai-key", None, None),
    )
    monkeypatch.setattr(service, "_litellm_model_supports_param", lambda *args: True)

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(model_dump=lambda: {"ok": True}, usage=None)

    monkeypatch.setattr(service, "acompletion", fake_acompletion)

    await service.proxy_chat_completion(
        db=AsyncMock(),
        container_token="container-token",
        raw_request={
            "model": "openai/gpt-5.4",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
            "stream": False,
        },
    )

    assert captured_kwargs["reasoning_effort"] == "high"
    assert captured_kwargs["service_tier"] == "priority"


@pytest.mark.asyncio
async def test_proxy_does_not_add_reasoning_effort_when_thinking_is_explicit(monkeypatch):
    captured_kwargs = {}

    monkeypatch.setattr(service.settings, "dev_comworker_url", "http://dev-comworker")
    monkeypatch.setattr(service.settings, "hermes_reasoning_effort", "none")
    monkeypatch.setattr(service.settings, "hermes_service_tier", "")
    monkeypatch.setattr(
        service,
        "_resolve_provider",
        lambda _model: ("anthropic/claude-opus-4-6", "anthropic-key", None, None),
    )

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(model_dump=lambda: {"ok": True}, usage=None)

    monkeypatch.setattr(service, "acompletion", fake_acompletion)

    await service.proxy_chat_completion(
        db=AsyncMock(),
        container_token="container-token",
        raw_request={
            "model": "anthropic/claude-opus-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "stream": False,
        },
    )

    assert "reasoning_effort" not in captured_kwargs
    assert captured_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 1024}
