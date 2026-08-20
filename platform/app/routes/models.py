"""GET/PUT /api/comworker/models — per-user model provider configuration.

Reads and writes the hermes container's /opt/data/config.yaml so each
user can add providers, configure API keys, and switch default models.
"""

from __future__ import annotations

import io
import logging
import tarfile
import time

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.container.manager import ensure_running, get_docker_container
from app.db.engine import get_db
from app.db.models import Container, User
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/comworker", tags=["models"])


# ---------------------------------------------------------------------------
# Helpers — read/write container config.yaml
# ---------------------------------------------------------------------------

def _read_container_config(container_name: str) -> dict:
    container = get_docker_container(container_name)
    result = container.exec_run(["cat", "/opt/data/config.yaml"], user="hermes")
    if result.exit_code != 0:
        return {}
    try:
        return yaml.safe_load(result.output.decode("utf-8")) or {}
    except Exception:
        return {}


def _write_container_config(container_name: str, config: dict) -> None:
    content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        info = tarfile.TarInfo(name="config.yaml")
        info.size = len(content)
        info.mode = 0o644
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(content))
    tar_buffer.seek(0)
    container = get_docker_container(container_name)
    ok = container.put_archive("/opt/data", tar_buffer.read())
    if not ok:
        raise RuntimeError("failed to write config.yaml into container")
    container.exec_run(["chown", "hermes:hermes", "/opt/data/config.yaml"], user="root")


def _container_name(user_id: str) -> str:
    return f"hermes-user-{user_id[:8]}"


# ---------------------------------------------------------------------------
# Format conversion: hermes config.yaml <-> frontend providers format
# ---------------------------------------------------------------------------

async def _hermes_to_frontend(config: dict, db=None, user_id=None) -> dict:
    """Convert hermes config.yaml to frontend format."""
    from app.config import settings

    # Admin-disabled providers (DB is the source of truth, persisted across
    # rebuilds). Merged here so the client reflects the disabled state even
    # before the container is rebuilt.
    disabled_set = set()
    if db is not None and user_id:
        try:
            _uc = await _load_user_config(db, user_id)
            disabled_set = set((_uc or {}).get("disabled_providers") or [])
        except Exception:  # noqa: BLE001
            pass

    def _prov_of(model_id: str) -> str:
        return model_id.split("/", 1)[0] if "/" in model_id else "platform"

    default_model = ""
    model_section = config.get("model") or {}
    if isinstance(model_section, dict):
        default_model = model_section.get("default", "")
        provider = model_section.get("provider", "")
        if provider and default_model and "/" not in default_model:
            default_model = f"{provider}/{default_model}"

    providers: dict = {}

    # Platform default model — read-only, no API key exposed
    if settings.default_model:
        providers["platform"] = {
            "baseUrl": "",
            "api": "openai-completions",
            "apiKey": "",
            "models": [{"id": settings.default_model, "name": settings.default_model}],
            "_system": True,
            "disabled": "platform" in disabled_set,
        }

    # User-added providers
    custom_providers = config.get("custom_providers") or []
    flat_models: list[dict] = []
    for cp in custom_providers:
        if not isinstance(cp, dict):
            continue
        name = cp.get("name", "")
        if not name or name in ("platform", "platform-gateway"):
            continue
        cp_models = cp.get("models") or []
        providers[name] = {
            "baseUrl": cp.get("base_url", ""),
            "api": _API_MODE_TO_API.get(cp.get("api_mode", ""), "openai-completions"),
            "apiKey": cp.get("api_key", ""),
            "models": cp_models,
            "disabled": name in disabled_set,
        }
        for m in cp_models:
            if isinstance(m, dict) and m.get("id"):
                flat_models.append({
                    "id": f"{name}/{m['id']}",
                    "name": m.get("name") or m["id"],
                    "disabled": name in disabled_set,
                })

    # Merge admin-configured platform models (DB: model_provider_configs) so the
    # client can surface them. These route through platform-gateway, which resolves
    # the provider from the DB by the "provider/model" id — so we keep the full
    # prefixed id (e.g. "tx/hy3-preview") and never strip it.
    if db is not None:
        try:
            from app.model_config import flatten_enabled_models, get_default_model, list_enabled_providers
            platform_models = flatten_enabled_models(
                await list_enabled_providers(db, user_id=user_id), user_id=user_id
            )
            seen = {m["id"] for m in flat_models}
            for pm in platform_models:
                if pm["id"] not in seen:
                    pm = dict(pm)
                    pm["disabled"] = _prov_of(pm["id"]) in disabled_set
                    flat_models.append(pm)
                    seen.add(pm["id"])
        except Exception as e:  # pragma: no cover - degrade gracefully
            logger.warning("Failed to merge platform models: %s", e)

    # If the user has no own model selection, fall back to the per-user admin
    # default (or platform default restricted to what this user may access).
    if not default_model and db is not None:
        try:
            default_model = await get_default_model(db, user_id=user_id)
        except Exception as e:  # pragma: no cover
            logger.warning("Failed to compute default model: %s", e)

    return {
        "models": flat_models,
        "configuredModel": default_model,
        "configuredProviders": providers,
    }


# Frontend uses hyphenated values (anthropic-messages, openai-completions);
# hermes uses snake_case api_mode values (anthropic_messages, chat_completions).
_API_TO_API_MODE = {
    "anthropic-messages": "anthropic_messages",
    "openai-completions": "chat_completions",
    "google-generative-ai": "google_ai",
}
_API_MODE_TO_API = {v: k for k, v in _API_TO_API_MODE.items()}


def _frontend_to_hermes_providers(providers: dict) -> list[dict]:
    """Convert frontend providers format to hermes custom_providers list."""
    result = []
    for name, p in providers.items():
        if not isinstance(p, dict):
            continue
        entry = {
            "name": name,
            "base_url": p.get("baseUrl", ""),
            "api_key": p.get("apiKey", ""),
        }
        api = (p.get("api") or "").strip()
        if api:
            api_mode = _API_TO_API_MODE.get(api, api.replace("-", "_"))
            entry["api_mode"] = api_mode
        models = p.get("models")
        if models:
            entry["models"] = models
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# DB-backed durable user config (source of truth for rebuilds)
# ---------------------------------------------------------------------------

async def _load_user_config(db, user_id: str) -> dict | None:
    """Load a user's persisted Hermes config from Postgres (None if absent)."""
    from sqlalchemy import select
    from app.db.models import Container
    rec = (await db.execute(select(Container).where(Container.user_id == user_id))).scalar_one_or_none()
    if rec is None:
        return None
    uc = rec.user_config
    return uc if isinstance(uc, dict) else None


async def _persist_user_config(db, user_id: str, config: dict, providers_provided: bool, default_provided: bool) -> None:
    """Persist the user-managed portions of config.yaml into Postgres.

    Primary write path: the moment the user saves a provider/key or picks a
    default model, it is durably stored in DB so a container rebuild or volume
    loss can never wipe it. Explicit clears (empty providers / default) are
    honored, so a deletion sticks across rebuilds too.
    """
    rec = (await db.execute(select(Container).where(Container.user_id == user_id))).scalar_one_or_none()
    if rec is None:
        return
    desired = dict(rec.user_config or {})
    if providers_provided:
        existing_providers = config.get("custom_providers") or []
        user_cps = [
            p for p in existing_providers
            if isinstance(p, dict) and p.get("name") not in ("platform", "platform-gateway")
        ]
        if user_cps:
            desired["custom_providers"] = user_cps
        else:
            desired.pop("custom_providers", None)
    if default_provided:
        model = config.get("model") or {}
        md = model.get("default")
        mp = model.get("provider")
        if md:
            desired["model_default"] = md
        else:
            desired.pop("model_default", None)
        if mp and mp != "platform-gateway":
            desired["model_provider"] = mp
        else:
            desired.pop("model_provider", None)
    rec.user_config = desired
    await db.commit()


def _merge_db_user_config(config: dict, db_uc: dict | None) -> dict:
    """Overlay DB user_config onto a volume-read config so the UI always reflects
    saved data even while the container is mid-rebuild."""
    if not db_uc or not isinstance(db_uc, dict):
        return config
    config = dict(config)
    user_cps = db_uc.get("custom_providers") or []
    if user_cps:
        volume_cps = config.get("custom_providers") or []
        names = {p.get("name") for p in volume_cps if isinstance(p, dict)}
        merged = list(volume_cps)
        for p in user_cps:
            if isinstance(p, dict) and p.get("name") and p["name"] not in names:
                merged.append(p)
                names.add(p["name"])
        config["custom_providers"] = merged
    md = db_uc.get("model_default")
    mp = db_uc.get("model_provider")
    if md and not (config.get("model") or {}).get("default"):
        config.setdefault("model", {})["default"] = md
        if mp:
            config["model"]["provider"] = mp
    return config


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class UpdateModelsConfig(BaseModel):
    providers: dict | None = None
    defaultModel: str | None = None


class TestModelConnection(BaseModel):
    baseUrl: str
    apiKey: str = ""
    api: str = "openai-completions"  # anthropic-messages | openai-completions
    model: str


@router.post("/models/test")
async def test_model_connection(
    body: TestModelConnection,
    user: User = Depends(get_current_user),
):
    """Probe a provider endpoint before saving, so users catch misconfig
    (wrong baseUrl, key, api mode, or model id) immediately — e.g. the
    Volcengine 'does not support the coding plan feature' 404 that the
    'huoshan/glm-5.2' prefix bug produced.

    Supports OpenAI /chat/completions and Anthropic /v1/messages. The probe is
    run server-side (gateway) so it shares the host's outbound network.
    """
    import httpx

    base = (body.baseUrl or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=400, detail="baseUrl 不能为空")
    model = (body.model or "").strip()
    if "/" in model:
        # Strip a stray provider/ prefix (e.g. "huoshan/glm-5.2") so the probe
        # sends the bare model name — mirroring the fix in dedicated_hermes.
        model = model.split("/", 1)[1]
    if not model:
        raise HTTPException(status_code=400, detail="model 不能为空")
    api = (body.api or "openai-completions").strip()
    key = (body.apiKey or "").strip()

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if api == "anthropic-messages":
                url = f"{base}/v1/messages"
                payload = {
                    "model": model,
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "hi"}],
                }
                headers = {"anthropic-version": "2023-06-01"}
                if key:
                    headers["x-api-key"] = key
                    headers["Authorization"] = f"Bearer {key}"
                resp = await client.post(url, json=payload, headers=headers)
            else:
                url = f"{base}/chat/completions"
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 16,
                    "stream": False,
                }
                headers = {}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                resp = await client.post(url, json=payload, headers=headers)
    except httpx.ConnectError as exc:
        return {
            "ok": False,
            "status": 0,
            "message": f"无法连接到 {base}（网络不可达：{exc}）",
            "suggestion": "请检查 baseUrl 是否正确，以及服务端所在网络能否访问该地址。",
        }
    except httpx.TimeoutException:
        return {
            "ok": False,
            "status": 0,
            "message": "连接超时（30s）",
            "suggestion": "上游响应过慢或地址不可达，请检查 baseUrl 与网络连通性。",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "message": f"请求失败：{type(exc).__name__}: {exc}",
            "suggestion": "请检查 baseUrl / api 协议是否匹配（Anthropic 用 /v1/messages，OpenAI 用 /chat/completions）。",
        }

    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    body_text = ""
    try:
        body_text = (resp.text or "")[:600]
    except Exception:
        pass

    if resp.status_code < 300:
        return {"ok": True, "status": resp.status_code, "message": "连接成功", "durationMs": duration_ms}

    msg = body_text or f"HTTP {resp.status_code}"
    suggestion = ""
    if "does not support the coding plan feature" in msg or "coding plan" in msg:
        suggestion = (
            "该模型在此端点（api/coding）不可用。请确认：①模型名填写为纯模型名"
            "（如 glm-5.2，不要带 provider/ 前缀）；②该模型是否已开通 Coding 计划。"
        )
    elif resp.status_code in (401, 403):
        suggestion = "鉴权失败。请检查 apiKey 是否正确，以及该端点要求的鉴权方式（Bearer / x-api-key）。"
    elif resp.status_code == 404:
        suggestion = "404。请检查 baseUrl 与 api 协议是否匹配（Anthropic 需 /v1/messages，OpenAI 需 /chat/completions）。"
    elif resp.status_code == 400:
        suggestion = "请求被拒绝（400）。请检查模型名与协议字段是否符合该端点的要求。"

    return {
        "ok": False,
        "status": resp.status_code,
        "message": msg,
        "suggestion": suggestion,
        "durationMs": duration_ms,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/models")
async def list_models(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    container = await ensure_running(db, user.id)
    container_name = _container_name(user.id)
    try:
        config = _read_container_config(container_name)
    except Exception as e:
        logger.warning("Failed to read config from %s: %s", container_name, e)
        config = {}
    # Overlay DB user_config (source of truth) so saved providers/model survive
    # even while the container volume is mid-rebuild.
    db_uc = await _load_user_config(db, user.id)
    config = _merge_db_user_config(config, db_uc)
    return await _hermes_to_frontend(config, db, user_id=user.id)


@router.put("/models/config")
async def update_models_config(
    body: UpdateModelsConfig,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    container = await ensure_running(db, user.id)
    container_name = _container_name(user.id)

    try:
        config = _read_container_config(container_name)
    except Exception:
        config = {}

    if body.providers is not None:
        existing_providers = config.get("custom_providers") or []
        platform_gateway = [
            p for p in existing_providers
            if isinstance(p, dict) and p.get("name") == "platform-gateway"
        ]
        new_providers = [
            p for p in _frontend_to_hermes_providers(body.providers)
            if p.get("name") != "platform"
        ]
        config["custom_providers"] = platform_gateway + new_providers

    if body.defaultModel is not None:
        if "model" not in config or not isinstance(config.get("model"), dict):
            config["model"] = {}
        config["model"]["default"] = body.defaultModel

        # Resolve model.provider so the hermes agent routes to the correct
        # custom_provider instead of blindly using platform-gateway for everything.
        # e.g. "deepseek/deepseek-chat" → provider="deepseek"
        if "/" in body.defaultModel:
            provider_hint = body.defaultModel.split("/", 1)[0]
        else:
            provider_hint = ""

        custom_providers = config.get("custom_providers") or []
        matching = None
        if provider_hint:
            for cp in custom_providers:
                if isinstance(cp, dict) and cp.get("name") == provider_hint:
                    matching = cp
                    break

        if matching and (matching.get("api_key") or "").strip():
            config["model"]["provider"] = provider_hint
            # Remove platform-gateway's base_url override so the
            # custom_provider's own base_url takes effect.
            config["model"].pop("base_url", None)
            # Strip provider prefix from default model so the hermes agent
            # sends the actual model name (e.g. "kimi-k2.5") not the fully
            # qualified "kimi/kimi-k2.5" which it doesn't parse.
            config["model"]["default"] = body.defaultModel.split("/", 1)[1]
        else:
            config["model"]["provider"] = "platform-gateway"

    try:
        _write_container_config(container_name, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")

    # Persist user-managed config to Postgres (source of truth) so a container
    # rebuild / volume loss never wipes it. Done AFTER the volume write so the
    # two stay consistent for the common case.
    await _persist_user_config(
        db, user.id, config,
        providers_provided=body.providers is not None,
        default_provided=body.defaultModel is not None,
    )

    return {"ok": True}
