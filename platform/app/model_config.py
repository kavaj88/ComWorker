from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ModelProviderConfig
from app.model_keys import get_api_key, set_api_key


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    provider_type: str
    api_base: str | None
    models: tuple[tuple[str, str], ...]
    key_attr: str
    base_attr: str | None = None


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        provider_type="deepseek",
        api_base="https://api.deepseek.com/v1",
        models=(("deepseek-chat", "DeepSeek Chat"), ("deepseek-reasoner", "DeepSeek Reasoner")),
        key_attr="deepseek_api_key",
    ),
    "minimax": ProviderPreset(
        id="minimax",
        name="MiniMax 中国区",
        provider_type="minimax-cn",
        api_base="https://api.minimaxi.com/anthropic",
        models=(("MiniMax-M2.7", "MiniMax M2.7"),),
        key_attr="minimax_api_key",
        base_attr="minimax_api_base",
    ),
    "openai": ProviderPreset(
        id="openai",
        name="OpenAI",
        provider_type="openai",
        api_base=None,
        models=(("gpt-5.4", "GPT-5.4"), ("gpt-5.4-mini", "GPT-5.4 Mini")),
        key_attr="openai_api_key",
        base_attr="openai_api_base",
    ),
    "anthropic": ProviderPreset(
        id="anthropic",
        name="Claude",
        provider_type="anthropic",
        api_base=None,
        models=(("claude-sonnet-4-5", "Claude Sonnet 4.5"), ("claude-opus-4-6", "Claude Opus 4.6")),
        key_attr="anthropic_api_key",
    ),
    "openrouter": ProviderPreset(
        id="openrouter",
        name="OpenRouter",
        provider_type="openrouter",
        api_base=None,
        models=(("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5"), ("openai/gpt-5.4", "GPT-5.4")),
        key_attr="openrouter_api_key",
    ),
    "zhipu": ProviderPreset(
        id="zhipu",
        name="Zhipu GLM",
        provider_type="zhipu",
        api_base="https://open.bigmodel.cn/api/paas/v4",
        models=(("glm-4-plus", "GLM-4 Plus"), ("glm-4.5", "GLM-4.5")),
        key_attr="zhipu_api_key",
    ),
    "kimi": ProviderPreset(
        id="kimi",
        name="Kimi",
        provider_type="kimi",
        api_base="https://api.moonshot.cn/v1",
        models=(("kimi-k2.5", "Kimi K2.5"), ("moonshot-v1-128k", "Moonshot 128K")),
        key_attr="kimi_api_key",
    ),
    "doubao": ProviderPreset(
        id="doubao",
        name="Doubao",
        provider_type="doubao",
        api_base="https://ark.cn-beijing.volces.com/api/v3",
        models=(("doubao-seed-1-6", "Doubao Seed 1.6"),),
        key_attr="doubao_api_key",
    ),
    "evolink": ProviderPreset(
        id="evolink",
        name="Evolink",
        provider_type="evolink",
        api_base="https://direct.evolink.ai/v1",
        models=(("gpt-5.2", "GPT-5.2"), ("deepseek-chat", "DeepSeek V4"), ("deepseek-reasoner", "DeepSeek Reasoner")),
        key_attr="evolink_api_key",
    ),
}


def _models_from_preset(preset: ProviderPreset) -> list[dict[str, Any]]:
    return [{"id": model_id, "name": name, "enabled": True} for model_id, name in preset.models]


def _provider_model_ref(provider_id: str, model_id: str) -> str:
    return f"{provider_id}/{model_id}"


def _masked(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"


def _provider_visible_to_user(provider: "ModelProviderConfig", user_id: str | None) -> bool:
    """Whether a given user may use this provider, per its user_scope.

    scope is None or {"mode":"all"}            => everyone
    {"mode":"allow","user_ids":[...]}          => only listed users
    {"mode":"deny","user_ids":[...]}           => everyone except listed users
    """
    if not user_id:
        return True
    scope = provider.user_scope
    if not isinstance(scope, dict):
        return True
    mode = (scope.get("mode") or "all")
    ids = scope.get("user_ids") or []
    if mode == "allow":
        return user_id in ids
    if mode == "deny":
        return user_id not in ids
    return True


def serialize_provider(provider: ModelProviderConfig, *, include_secret: bool = False) -> dict[str, Any]:
    data = {
        "id": provider.id,
        "name": provider.display_name,
        "providerType": provider.provider_type,
        "baseUrl": provider.api_base or "",
        "api": provider.api_mode or provider.provider_type,
        "models": provider.models or [],
        "enabled": provider.enabled,
        "isDefault": provider.is_default,
        "configured": bool(get_api_key(provider)),
        "apiKeyMasked": _masked(get_api_key(provider)),
        "scopeMode": (provider.user_scope or {}).get("mode", "all") if isinstance(provider.user_scope, dict) else "all",
        "scopeUserIds": (provider.user_scope or {}).get("user_ids", []) if isinstance(provider.user_scope, dict) else [],
    }
    if include_secret:
        data["apiKey"] = get_api_key(provider)
    return data


def flatten_enabled_models(providers: list[ModelProviderConfig], user_id: str | None = None) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    for provider in providers:
        if not provider.enabled or not get_api_key(provider):
            continue
        if user_id and not _provider_visible_to_user(provider, user_id):
            continue
        for model in provider.models or []:
            if not isinstance(model, dict) or model.get("enabled") is False:
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            full_id = _provider_model_ref(provider.id, model_id)
            models.append({
                "id": full_id,
                "name": model.get("name") or model_id,
                "provider": provider.id,
                "providerName": provider.display_name,
            })
    return models


async def list_enabled_providers(db: AsyncSession, user_id: str | None = None) -> list[ModelProviderConfig]:
    result = await db.execute(
        select(ModelProviderConfig)
        .where(ModelProviderConfig.enabled.is_(True))
        .order_by(ModelProviderConfig.created_at.asc())
    )
    providers = list(result.scalars().all())
    if user_id:
        providers = [p for p in providers if _provider_visible_to_user(p, user_id)]
    return providers


async def get_default_model(db: AsyncSession, user_id: str | None = None) -> str:
    providers = await list_enabled_providers(db, user_id=user_id)
    # 1) Admin-assigned per-user default (if it resolves & is visible to the user)
    if user_id:
        from app.db.models import User
        user = await db.get(User, user_id)
        dm = (user.default_model or "").strip() if user else ""
        if dm and "/" in dm:
            pid, mid = dm.split("/", 1)
            prov = next((p for p in providers if p.id == pid), None)
            if prov and any(
                isinstance(m, dict) and m.get("enabled") is not False and str(m.get("id") or "") == mid
                for m in (prov.models or [])
            ):
                return dm
    # 2) Platform default provider
    for provider in providers:
        if provider.is_default and get_api_key(provider):
            for model in provider.models or []:
                if isinstance(model, dict) and model.get("enabled") is not False and model.get("id"):
                    return _provider_model_ref(provider.id, str(model["id"]))
    # 3) First available model
    models = flatten_enabled_models(providers, user_id=user_id)
    if models:
        return str(models[0]["id"])
    return settings.default_model


async def get_model_config_payload(db: AsyncSession, *, include_secret: bool = False) -> dict[str, Any]:
    result = await db.execute(select(ModelProviderConfig).order_by(ModelProviderConfig.created_at.asc()))
    providers = list(result.scalars().all())
    default_model = await get_default_model(db)
    return {
        "models": flatten_enabled_models(providers),
        "configuredModel": default_model,
        "configuredProviders": {
            provider.id: serialize_provider(provider, include_secret=include_secret)
            for provider in providers
        },
    }


async def resolve_model_provider(db: AsyncSession, model: str, user_id: str | None = None) -> tuple[ModelProviderConfig, str]:
    if "/" not in model:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Model '{model}' must include provider prefix")
    provider_id, model_id = model.split("/", 1)
    provider = await db.get(ModelProviderConfig, provider_id)
    if provider is None or not provider.enabled or not get_api_key(provider):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"No provider configured for model '{model}'")
    if user_id and not _provider_visible_to_user(provider, user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Model '{model}' is not available to this user",
        )
    for item in provider.models or []:
        if isinstance(item, dict) and item.get("enabled") is not False and str(item.get("id") or "") == model_id:
            return provider, model_id
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Model '{model}' is not enabled")


async def seed_model_config_from_env(db: AsyncSession) -> None:
    count = len((await db.execute(select(ModelProviderConfig.id))).scalars().all())
    if count:
        return

    default_model = (settings.default_model or "").strip()
    seeded: list[ModelProviderConfig] = []
    for preset in PROVIDER_PRESETS.values():
        key = (getattr(settings, preset.key_attr, "") or "").strip()
        if not key:
            continue
        api_base = (getattr(settings, preset.base_attr, "") or "").strip() if preset.base_attr else ""
        seeded.append(ModelProviderConfig(
            id=preset.id,
            display_name=preset.name,
            provider_type=preset.provider_type,
            api_base=api_base or preset.api_base,
            models=_models_from_preset(preset),
            enabled=True,
            is_default=default_model.startswith(f"{preset.id}/"),
        ))
        set_api_key(seeded[-1], key)

    if seeded and not any(item.is_default for item in seeded):
        seeded[0].is_default = True

    for provider in seeded:
        db.add(provider)
    if seeded:
        await db.commit()


async def set_default_model(db: AsyncSession, model: str) -> None:
    provider, _model_id = await resolve_model_provider(db, model)
    await db.execute(update(ModelProviderConfig).values(is_default=False))
    provider.is_default = True
    await db.commit()
