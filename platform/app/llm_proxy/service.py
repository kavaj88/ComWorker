"""LLM Proxy — the security core of the multi-tenant platform.

Receives OpenAI-compatible requests from user containers (authenticated
by container token), injects the real API key, records usage, enforces
quotas, and forwards to the actual LLM provider.

Design: pass-through proxy with platform defaults. We extract `model` for
routing and `stream` for response handling. Other parameters (messages, tools,
temperature, max_tokens, max_completion_tokens, reasoning_effort, thinking,
top_p, response_format, etc.) are forwarded as-is to litellm/provider, with
platform reasoning/speed defaults filled only when the request omits them.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from functools import lru_cache
from typing import Any

import httpx
import litellm
from fastapi import HTTPException, status
from litellm import acompletion
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit_log
from app.auth.service import decode_token
from app.config import settings
from app.db.models import Container, ModelProviderConfig, UsageRecord, User
from app.model_config import resolve_model_provider
from app.model_keys import get_api_key

logger = logging.getLogger("platform.llm_proxy")


# ---------------------------------------------------------------------------
# Model → provider mapping
# ---------------------------------------------------------------------------

_PROVIDERS: list[dict] = [
    {
        "prefix": "claude",
        "key_attr": "anthropic_api_key",
        "litellm_fmt": "{model}",
        "api_base": None,
        "keywords": ["claude"],
    },
    {
        "prefix": "openai",
        "key_attr": "openai_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base_attr": "openai_api_base",
        "keywords": ["gpt", "o1", "o3", "o4"],
    },
    {
        "prefix": "deepseek",
        "key_attr": "deepseek_api_key",
        "litellm_fmt": "deepseek/{model}",
        "api_base": None,
        "keywords": ["deepseek"],
    },
    {
        "prefix": "dashscope",
        "key_attr": "dashscope_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "keywords": ["qwen"],
    },
    {
        "prefix": "minimax",
        "key_attr": "minimax_api_key",
        "litellm_fmt": "minimax/{model}",
        "api_base_attr": "minimax_api_base",
        "keywords": ["minimax"],
    },
    {
        "prefix": "kimi",
        "key_attr": "kimi_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base": "https://api.moonshot.cn/v1",
        "keywords": ["kimi", "moonshot"],
    },
    {
        "prefix": "zhipu",
        "key_attr": "zhipu_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "keywords": ["glm"],
    },
    {
        "prefix": "doubao",
        "key_attr": "doubao_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "keywords": ["doubao"],
    },
    {
        "prefix": "aihubmix",
        "key_attr": "aihubmix_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base": "https://aihubmix.com/v1",
        "keywords": ["aihubmix"],
    },
    {
        "prefix": "evolink",
        "key_attr": "evolink_api_key",
        "litellm_fmt": "openai/{model}",
        "api_base": "https://direct.evolink.ai/v1",
        "keywords": ["evolink"],
    },
    {
        "prefix": "vllm",
        "key_attr": "hosted_vllm_api_key",
        "litellm_fmt": "hosted_vllm/{model}",
        "api_base_attr": "hosted_vllm_api_base",
        "keywords": [],
    },
]

_PREFIX_MAP: dict[str, dict] = {p["prefix"]: p for p in _PROVIDERS}

_KEYWORD_MAP: dict[str, dict] = {}
for _p in _PROVIDERS:
    for _kw in _p.get("keywords", []):
        _KEYWORD_MAP[_kw] = _p


def _normalize_minimax_openai_api_base(api_base: str | None) -> str | None:
    if not api_base:
        return api_base
    normalized = api_base.strip().rstrip("/")
    if normalized.endswith("/anthropic/v1/messages"):
        return normalized[: -len("/anthropic/v1/messages")] + "/v1"
    if normalized.endswith("/anthropic/v1"):
        return normalized[: -len("/anthropic/v1")] + "/v1"
    if normalized.endswith("/anthropic"):
        return normalized[: -len("/anthropic")] + "/v1"
    return normalized


def _normalize_anthropic_api_base(api_base: str | None) -> str | None:
    if not api_base:
        return api_base
    normalized = api_base.strip().rstrip("/")
    if normalized.endswith("/v1/messages"):
        return normalized[: -len("/v1/messages")]
    if normalized.endswith("/v1") and "/anthropic" in normalized.lower():
        return normalized[: -len("/v1")]
    return normalized


def _get_provider_key_base_and_headers(provider: dict) -> tuple[str, str | None, dict[str, str] | None]:
    api_key = getattr(settings, provider["key_attr"], "") or ""
    if "api_base_attr" in provider:
        api_base = getattr(settings, provider["api_base_attr"], "") or None
    else:
        api_base = provider.get("api_base")
    if provider["prefix"] == "minimax":
        api_base = _normalize_minimax_openai_api_base(api_base)
    if not api_key and provider["prefix"] == "vllm":
        api_key = "dummy"
    return api_key, api_base, None


def _maybe_use_minimax_highspeed(model: str) -> str:
    if settings.minimax_m27_use_highspeed and model.lower() == "minimax-m2.7":
        return "MiniMax-M2.7-highspeed"
    return model


def _resolve_provider(model: str) -> tuple[str, str, str | None, dict[str, str] | None]:
    """Return (litellm_model_name, api_key, api_base_or_None, extra_headers_or_None)."""
    model_lower = model.lower()

    # 1. Explicit prefix
    if "/" in model:
        prefix = model_lower.split("/", 1)[0]
        if prefix in _PREFIX_MAP:
            provider = _PREFIX_MAP[prefix]
            actual_model = model.split("/", 1)[1]
            api_key, api_base, extra_headers = _get_provider_key_base_and_headers(provider)
            if api_key or extra_headers:
                if provider["prefix"] == "minimax":
                    actual_model = _maybe_use_minimax_highspeed(actual_model)
                litellm_model = provider["litellm_fmt"].format(model=actual_model)
                logger.info("模型路由: %s → %s (litellm=%s)", model, prefix, litellm_model)
                return litellm_model, api_key, api_base, extra_headers

    # 2. Keyword match
    for keyword, provider in _KEYWORD_MAP.items():
        if keyword in model_lower:
            api_key, api_base, extra_headers = _get_provider_key_base_and_headers(provider)
            if api_key or extra_headers:
                actual_model = model.split("/", 1)[1] if "/" in model else model
                if provider["prefix"] == "minimax":
                    actual_model = _maybe_use_minimax_highspeed(actual_model)
                litellm_model = provider["litellm_fmt"].format(model=actual_model)
                logger.info("模型路由: %s → %s (keyword=%r, litellm=%s)", model, provider["prefix"], keyword, litellm_model)
                return litellm_model, api_key, api_base, extra_headers

    # 3. Fallback: use the platform's default model
    default_model = (settings.default_model or "").strip()
    if default_model and default_model != model:
        logger.info("模型路由: %s → 默认模型 %s", model, default_model)
        return _resolve_provider(default_model)

    # 4. Fallback: vLLM
    if settings.hosted_vllm_api_base:
        return f"hosted_vllm/{model}", settings.hosted_vllm_api_key or "dummy", settings.hosted_vllm_api_base, None

    # 5. Fallback: OpenRouter
    if settings.openrouter_api_key:
        return f"openrouter/{model}", settings.openrouter_api_key, None, None

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"No provider configured for model '{model}'",
    )


def _resolve_configured_provider(provider: ModelProviderConfig, model_id: str) -> tuple[str, str, str | None, dict[str, str] | None]:
    api_key = get_api_key(provider) or ""
    api_base = provider.api_base or None
    provider_type = (provider.provider_type or provider.id or "").strip().lower()
    if provider_type == "anthropic":
        litellm_model = model_id if model_id.startswith("claude") else f"anthropic/{model_id}"
    elif provider_type == "openai":
        litellm_model = f"openai/{model_id}"
    elif provider_type == "deepseek":
        litellm_model = f"deepseek/{model_id}"
    elif provider_type == "minimax":
        litellm_model = f"minimax/{_maybe_use_minimax_highspeed(model_id)}"
        api_base = _normalize_minimax_openai_api_base(api_base)
    elif provider_type == "minimax-cn":
        litellm_model = model_id
        api_base = _normalize_anthropic_api_base(api_base or "https://api.minimaxi.com/anthropic")
    elif provider_type == "openrouter":
        litellm_model = f"openrouter/{model_id}"
        api_base = None
    elif provider_type in {"kimi", "moonshot", "zhipu", "doubao", "dashscope", "aihubmix", "evolink", "custom"}:
        litellm_model = f"openai/{model_id}"
    elif provider_type == "vllm":
        litellm_model = f"hosted_vllm/{model_id}"
        api_key = api_key or "dummy"
    else:
        litellm_model = f"openai/{model_id}"
    logger.info("模型路由: %s/%s -> %s (litellm=%s)", provider.id, model_id, provider_type, litellm_model)
    return litellm_model, api_key, api_base, None


def _provider_uses_anthropic_messages(provider: ModelProviderConfig | None, api_base: str | None) -> bool:
    # An explicitly configured api_mode is authoritative and overrides the
    # provider_type / base_url heuristics. Accepts both hyphenated and
    # snake_case spellings.
    if provider is not None and provider.api_mode:
        return "anthropic" in provider.api_mode.lower()
    provider_type = ((provider.provider_type if provider is not None else "") or "").strip().lower()
    normalized_base = (api_base or "").strip().rstrip("/").lower()
    return provider_type in {"minimax-cn", "anthropic_messages"} or normalized_base.endswith("/anthropic")


# ---------------------------------------------------------------------------
# Quota check
# ---------------------------------------------------------------------------

_TIER_LIMITS = {
    "free": settings.quota_free,
    "basic": settings.quota_basic,
    "pro": settings.quota_pro,
}


async def _check_quota(db: AsyncSession, user: User) -> None:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(func.coalesce(func.sum(UsageRecord.total_tokens), 0)).where(
            UsageRecord.user_id == user.id,
            UsageRecord.created_at >= today_start,
        )
    )
    used_today: int = result.scalar_one()
    limit = _TIER_LIMITS.get(user.quota_tier, _TIER_LIMITS["free"])

    if used_today >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily token quota exceeded ({used_today:,}/{limit:,}). Resets at midnight UTC.",
        )


# ---------------------------------------------------------------------------
# Keys that litellm accepts directly from OpenAI-compatible requests.
# We pass these through as-is. Keys NOT in this set are stripped to avoid
# litellm errors on unknown parameters.
# ---------------------------------------------------------------------------

_LITELLM_PASSTHROUGH_KEYS = {
    "messages",
    "temperature",
    "top_p",
    "n",
    "stop",
    "max_tokens",
    "max_completion_tokens",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "response_format",
    "seed",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "user",
    "reasoning_effort",
    "thinking",
    "service_tier",
    "store",
    "metadata",
    "logprobs",
    "top_logprobs",
    "stream_options",
}


def _nonempty_setting(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


@lru_cache(maxsize=128)
def _supported_openai_params_for_model(litellm_model: str) -> tuple[str, ...]:
    return tuple(litellm.get_supported_openai_params(model=litellm_model) or [])


def _litellm_model_supports_param(litellm_model: str, api_base: str | None, param: str) -> bool:
    try:
        supported = _supported_openai_params_for_model(litellm_model)
    except Exception as exc:
        logger.debug("Could not resolve supported LiteLLM params for %s: %s", litellm_model, exc)
        return False

    if param not in supported:
        return False

    # The proxy maps several OpenAI-compatible providers through `openai/<model>`
    # with a custom api_base. LiteLLM's OpenAI param list can be broader than
    # those upstream-compatible endpoints, so don't add reasoning defaults there
    # unless the caller explicitly sent them.
    if param == "reasoning_effort" and litellm_model.lower().startswith("openai/") and api_base:
        return False

    return True


def _apply_platform_generation_defaults(kwargs: dict, litellm_model: str, api_base: str | None) -> None:
    """Apply platform runtime defaults without overriding explicit request knobs."""
    reasoning_effort = _nonempty_setting(settings.hermes_reasoning_effort)
    if (
        reasoning_effort
        and "reasoning_effort" not in kwargs
        and "thinking" not in kwargs
        and _litellm_model_supports_param(litellm_model, api_base, "reasoning_effort")
    ):
        kwargs["reasoning_effort"] = reasoning_effort

    service_tier = _nonempty_setting(settings.hermes_service_tier)
    if (
        service_tier
        and "service_tier" not in kwargs
        and _litellm_model_supports_param(litellm_model, api_base, "service_tier")
    ):
        kwargs["service_tier"] = service_tier


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text" and item.get("text") is not None:
                parts.append(str(item.get("text")))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(part for part in parts if part)


def _openai_messages_to_anthropic(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = _content_to_text(message.get("content"))
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            anthropic_messages.append({"role": role, "content": content})
        elif role == "tool":
            anthropic_messages.append({"role": "user", "content": content})
    return ("\n\n".join(system_parts) if system_parts else None), anthropic_messages


def _anthropic_usage_tokens(usage: Any) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    return (
        int(input_tokens) if isinstance(input_tokens, (int, float)) else 0,
        int(output_tokens) if isinstance(output_tokens, (int, float)) else 0,
    )


def _anthropic_content_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if text is not None:
                parts.append(str(text))
    return "".join(parts)


def _chat_completion_payload_from_anthropic(payload: dict[str, Any], model: str) -> dict[str, Any]:
    input_tokens, output_tokens = _anthropic_usage_tokens(payload.get("usage"))
    return {
        "id": payload.get("id") or f"chatcmpl-platform-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": _anthropic_content_text(payload),
            },
            "finish_reason": "stop" if payload.get("stop_reason") in {None, "end_turn"} else payload.get("stop_reason"),
        }],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


async def _single_chunk_stream(payload: dict[str, Any], model: str):
    import json

    text = _anthropic_content_text(payload)
    created = int(time.time())
    chunk_id = payload.get("id") or f"chatcmpl-platform-{created}"
    if text:
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": text},
                "finish_reason": None,
            }],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

    input_tokens, output_tokens = _anthropic_usage_tokens(payload.get("usage"))
    final_chunk = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop" if payload.get("stop_reason") in {None, "end_turn"} else payload.get("stop_reason"),
        }],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


async def _call_anthropic_messages(
    *,
    model: str,
    api_key: str,
    api_base: str,
    raw_request: dict,
) -> dict[str, Any]:
    system_prompt, messages = _openai_messages_to_anthropic(raw_request.get("messages") or [])
    if not messages:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Anthropic messages")
    max_tokens = raw_request.get("max_tokens") or raw_request.get("max_completion_tokens") or 4096
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if system_prompt:
        body["system"] = system_prompt
    for key in ("temperature", "top_p", "stop"):
        if key in raw_request:
            body[key] = raw_request[key]

    url = f"{_normalize_anthropic_api_base(api_base).rstrip('/')}/v1/messages"
    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, headers=headers, json=body)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": resp.text}
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Anthropic-compatible provider error: HTTP {resp.status_code}: {payload}",
        )
    return payload if isinstance(payload, dict) else {"content": []}


async def _record_usage(
    db: AsyncSession,
    *,
    user: User | None,
    model: str,
    provider_id: str | None,
    upstream_model: str | None,
    input_tokens: int,
    output_tokens: int,
    stream: bool,
) -> None:
    total = input_tokens + output_tokens
    if user is None or total <= 0:
        return
    db.add(UsageRecord(
        user_id=user.id,
        model=model,
        provider_id=provider_id,
        upstream_model=upstream_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
    ))
    await write_audit_log(
        db,
        action="llm_call",
        user_id=user.id,
        resource=model,
        detail={
            "stream": stream,
            "provider_id": provider_id,
            "upstream_model": upstream_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
        },
    )
    await db.commit()


async def proxy_chat_completion(
    db: AsyncSession,
    container_token: str,
    raw_request: dict,
):
    """Pass-through proxy: authenticate, check quota, forward to LLM, record usage.

    All OpenAI-compatible parameters are forwarded as-is. We only modify:
    - model: routed to the correct litellm model name
    - api_key: injected from platform settings
    - api_base: injected for providers that need it
    - stream_options: injected for streaming usage tracking
    """
    model = raw_request.get("model", "")
    stream = raw_request.get("stream", False)
    messages = raw_request.get("messages", [])

    logger.info("收到 LLM 请求: model=%s, stream=%s, 消息数=%d", model, stream, len(messages))

    # 1. Authenticate
    container = None
    user = None

    if settings.dev_comworker_url:
        pass  # Skip auth in dev mode
    else:
        # Try JWT API token first
        jwt_payload = decode_token(container_token)
        if jwt_payload and jwt_payload.get("type") == "access":
            user_id = jwt_payload.get("sub")
            if user_id:
                user_result = await db.execute(select(User).where(User.id == user_id))
                user = user_result.scalar_one_or_none()

        # Dedicated per-user container token
        if user is None:
            result = await db.execute(
                select(Container).where(Container.container_token == container_token)
            )
            container = result.scalar_one_or_none()
            if container is not None:
                user_result = await db.execute(select(User).where(User.id == container.user_id))
                user = user_result.scalar_one_or_none()

        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        if user is not None:
            if not user.is_active:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account disabled")
            await _check_quota(db, user)

    # 2. Resolve provider. Admin-managed DB config is authoritative; legacy
    # env-var routing remains as a fallback for older deployments.
    provider_config: ModelProviderConfig | None = None
    configured_model_id: str | None = None
    try:
        provider_config, configured_model_id = await resolve_model_provider(db, model, user_id=user.id if user else None)
        litellm_model, api_key, api_base, extra_headers = _resolve_configured_provider(provider_config, configured_model_id)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_400_BAD_REQUEST:
            raise
        litellm_model, api_key, api_base, extra_headers = _resolve_provider(model)
    except Exception:
        litellm_model, api_key, api_base, extra_headers = _resolve_provider(model)

    if provider_config is not None and _provider_uses_anthropic_messages(provider_config, api_base):
        original_stream = bool(stream)
        if original_stream:
            raw_request = dict(raw_request)
            raw_request["stream"] = False
        try:
            anthropic_payload = await _call_anthropic_messages(
                model=litellm_model,
                api_key=api_key,
                api_base=api_base or "",
                raw_request=raw_request,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Anthropic-compatible LLM 调用失败: model=%s, 错误=%s", model, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM provider error: {e}",
            )
        input_tokens, output_tokens = _anthropic_usage_tokens(anthropic_payload.get("usage"))
        await _record_usage(
            db,
            user=user,
            model=model,
            provider_id=provider_config.id,
            upstream_model=litellm_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stream=original_stream,
        )
        if original_stream:
            return _single_chunk_stream(anthropic_payload, model)
        return _chat_completion_payload_from_anthropic(anthropic_payload, model)

    # 3. Build kwargs — pass through all known OpenAI-compatible params
    kwargs: dict = {
        "model": litellm_model,
        "api_key": api_key,
        "stream": stream,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    # Pass through all supported parameters from the original request
    for key in _LITELLM_PASSTHROUGH_KEYS:
        if key in raw_request:
            kwargs[key] = raw_request[key]

    _apply_platform_generation_defaults(kwargs, litellm_model, api_base)

    # Ensure streaming usage is reported
    if stream:
        if "stream_options" not in kwargs:
            kwargs["stream_options"] = {"include_usage": True}
        elif isinstance(kwargs["stream_options"], dict):
            kwargs["stream_options"].setdefault("include_usage", True)

    # Log extra keys we're NOT forwarding (for debugging)
    ignored_keys = set(raw_request.keys()) - _LITELLM_PASSTHROUGH_KEYS - {"model", "stream"}
    if ignored_keys:
        logger.debug("未转发的请求字段: %s", ignored_keys)

    # 4. Call LLM
    try:
        response = await acompletion(**kwargs)
    except Exception as e:
        msg_count = len(messages)
        logger.error("LLM 调用失败: model=%s, litellm=%s, 错误=%s, 消息数=%d", model, litellm_model, e, msg_count)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM provider error: {e}",
        )

    # 5. Streaming response
    if stream:
        import json

        async def _stream_generator():
            total_input = 0
            total_output = 0
            try:
                async for chunk in response:
                    data = chunk.model_dump()
                    chunk_usage = data.get("usage")
                    if chunk_usage:
                        total_input = chunk_usage.get("prompt_tokens") or 0
                        total_output = chunk_usage.get("completion_tokens") or 0
                    yield f"data: {json.dumps(data)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(
                    "Streaming LLM response interrupted: model=%s, litellm=%s, error=%s",
                    model, litellm_model, e, exc_info=True,
                )
                err_text = f"LLM provider stream error: {str(e)}"
                if len(err_text) > 300:
                    err_text = err_text[:300] + "..."
                error_chunk = {
                    "id": f"platform-error-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": f"\n\n[Error] {err_text}"},
                        "finish_reason": "stop",
                    }],
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                total = total_input + total_output
                if user is not None and total > 0:
                    try:
                        await _record_usage(
                            db,
                            user=user,
                            model=model,
                            provider_id=provider_config.id if provider_config is not None else None,
                            upstream_model=litellm_model,
                            input_tokens=total_input,
                            output_tokens=total_output,
                            stream=True,
                        )
                        await write_audit_log(
                            db,
                            action="llm_call",
                            user_id=user.id,
                            resource=model,
                            detail={
                                "stream": True,
                                "input_tokens": total_input,
                                "output_tokens": total_output,
                                "total_tokens": total,
                            },
                        )
                        await db.commit()
                    except Exception as e:
                        logger.warning("Failed to record streaming usage: %s", e)

        return _stream_generator()

    # 6. Record usage (non-streaming)
    usage = getattr(response, "usage", None)
    if user is not None and usage:
        await _record_usage(
            db,
            user=user,
            model=model,
            provider_id=provider_config.id if provider_config is not None else None,
            upstream_model=litellm_model,
            input_tokens=usage.prompt_tokens or 0,
            output_tokens=usage.completion_tokens or 0,
            stream=False,
        )
        await write_audit_log(
            db,
            action="llm_call",
            user_id=user.id,
            resource=model,
            detail={
                "stream": False,
                "input_tokens": usage.prompt_tokens or 0,
                "output_tokens": usage.completion_tokens or 0,
                "total_tokens": usage.total_tokens or 0,
            },
        )
        await db.commit()

    # 7. Update container last_active_at
    if container is not None:
        container.last_active_at = datetime.utcnow()
        await db.commit()

    return response.model_dump()
