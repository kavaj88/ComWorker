"""Capability registry + plan builder for per-user agent container capabilities.

A capability is a named agent feature (e.g. web search) that maps to a set of
container toolsets, an env-var key, a platform pool key source, and a config.yaml
section. ``build_capability_plan`` resolves a user's effective plan.

Precedence for *enabled*: per-user grant (``UserCapability.enabled``) >
platform default (``PlatformCapabilityDefault.default_inject``) > off.

Precedence for *key*: a user-provided key (``user_keys``) > the platform pool
key (``settings.<platform_key_attr>``). A capability's toolsets are mounted
only when a usable key exists (conditional mounting).

Backward-compatibility invariant: with empty ``user_caps``/``platform_defaults``
and no platform pool key configured, the plan reproduces the pre-capability
container config (no capability env/config, toolsets straight from settings).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import CapabilityDefinition


@dataclass
class CapabilityPlan:
    enabled_toolsets: list[str]
    env: dict[str, str]
    config_overrides: dict


# Registry of known capabilities. Each entry declares how the capability maps
# into the container: toolsets to mount, the env var carrying its key, the
# settings attr holding the platform pool key, and any config.yaml section.
#
# This dict is the *legacy fallback* used when no DB session is available (e.g.
# unit tests, the synchronous container-config helpers' ``plan=None`` fallback).
# In normal runtime the authoritative source is the ``capability_definitions``
# DB table, loaded via :func:`load_capability_definitions`.
_CAPABILITIES: dict[str, dict] = {
    "web_search": {
        "label": "联网搜索",
        "kind": "toolset",
        "category": "工具",
        "description": "调用外部搜索服务获取实时网页结果（如 Tavily）。",
        "toolsets": ["web"],
        "env_key": "TAVILY_API_KEY",
        "platform_key_attr": "tavily_api_key",
        "config": {"web": {"backend": "tavily"}},
        "requires_key": True,
        "placeholder": False,
    },
    # Placeholder capabilities: declared for the UI roadmap but NOT implemented
    # (no toolsets, no key, no config). build_capability_plan skips them, so a
    # grant/default against them is inert and never affects a container. Adding
    # a placeholder must not change any existing capability's behavior.
    "browser": {
        "label": "浏览器",
        "kind": "toolset",
        "category": "工具",
        "description": "浏览器自动化能力（工具集尚未接入）。",
        "placeholder": True,
        "toolsets": [],
        "env_key": "",
        "platform_key_attr": "",
        "config": {},
        "requires_key": False,
    },
    "image_gen": {
        "label": "图像生成",
        "kind": "model",
        "category": "模型",
        "description": "由底层模型直接生成图像（模型原生能力，无需外部密钥）。",
        "placeholder": True,
        "toolsets": [],
        "env_key": "",
        "platform_key_attr": "",
        "config": {},
        "requires_key": False,
    },
}


async def load_capability_definitions(
    db, include_disabled: bool = False
) -> dict[str, dict]:
    """Load active capability definitions from the DB as a keyed dict.

    Returns ``{}`` when the table is empty (e.g. before seeding). Callers should
    fall back to ``_CAPABILITIES`` in that case so behaviour is unchanged until
    the first seed runs.
    """
    stmt = select(CapabilityDefinition)
    if not include_disabled:
        stmt = stmt.where(CapabilityDefinition.enabled.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    out: dict[str, dict] = {}
    for r in rows:
        out[r.key] = {
            "capability": r.key,
            "label": r.label,
            "kind": r.kind,
            "category": r.category,
            "description": r.description or "",
            "toolsets": list(r.toolsets or []),
            "env_key": r.env_key or "",
            "platform_key_attr": r.platform_key_attr or "",
            "platform_key_value": r.platform_key_value or "",
            "config": dict(r.config or {}),
            "requires_key": bool(r.requires_key),
            "has_platform_key": bool(r.platform_key_value),
            "placeholder": bool(r.placeholder),
        }
    return out


async def seed_capability_definitions(db) -> int:
    """Idempotently upsert the built-in capability definitions.

    Only inserts missing rows; never overwrites admin edits to existing rows.
    Returns the number of newly inserted rows.
    """
    added = 0
    for cap_key, spec in _CAPABILITIES.items():
        exists = (
            await db.execute(
                select(CapabilityDefinition).where(CapabilityDefinition.key == cap_key)
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(
            CapabilityDefinition(
                key=cap_key,
                label=spec["label"],
                kind=spec.get("kind", "toolset"),
                category=spec.get("category", "未分类"),
                description=spec.get("description", ""),
                toolsets=spec.get("toolsets", []),
                env_key=spec.get("env_key", ""),
                platform_key_attr=spec.get("platform_key_attr", ""),
                config=spec.get("config", {}),
                requires_key=spec.get("requires_key", True),
                placeholder=spec.get("placeholder", False),
                builtin=True,
                enabled=True,
            )
        )
        added += 1
    if added:
        await db.commit()
    return added


def parse_hermes_api_toolsets(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw or raw.lower() in {"none", "off", "false", "0"}:
        return []
    if raw.lower() in {"full", "default", "hermes-api-server"}:
        return ["hermes-api-server"]
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def build_capability_plan(
    settings,
    user_caps: dict | None = None,
    platform_defaults: dict | None = None,
    user_keys: dict | None = None,
    defs: dict | None = None,
) -> CapabilityPlan:
    user_caps = user_caps or {}
    platform_defaults = platform_defaults or {}
    user_keys = user_keys or {}
    caps = defs if defs is not None else _CAPABILITIES

    toolsets = parse_hermes_api_toolsets(getattr(settings, "hermes_api_toolsets", ""))
    env: dict[str, str] = {}
    config: dict = {}

    for cap_key, cap in caps.items():
        if cap.get("placeholder"):
            # Placeholders are roadmap-only and never mounted, even if granted.
            continue
        if cap_key in user_caps:
            enabled = bool(getattr(user_caps[cap_key], "enabled", False))
        elif cap_key in platform_defaults:
            enabled = bool(getattr(platform_defaults[cap_key], "default_inject", False))
        else:
            enabled = False
        if not enabled:
            continue

        # Key resolution precedence for mounting:
        #   1. user-provided key inside the container (user_keys)
        #   2. admin-managed key from the capability definition (platform_key_value)
        #   3. platform pool key from settings.<platform_key_attr>
        # This lets an admin configure the key entirely from the management UI
        # without touching gateway environment variables.
        admin_key = cap.get("platform_key_value") or ""
        platform_key = getattr(settings, cap.get("platform_key_attr", ""), "") or ""
        key = user_keys.get(cap_key) or admin_key or platform_key
        if not key:
            # Conditional mounting: no usable key -> don't mount the toolset.
            continue

        for ts in cap["toolsets"]:
            if ts not in toolsets:
                toolsets.append(ts)
        env[cap["env_key"]] = key
        config.update(cap["config"])

    return CapabilityPlan(enabled_toolsets=toolsets, env=env, config_overrides=config)


def capability_registry(settings, defs: dict | None = None) -> list[dict]:
    """Return capability metadata for admin UI rendering.

    Exposes each capability's display info plus whether its platform pool key
    is configured. Never returns the key value itself. When ``defs`` is provided
    (DB-backed), it is used; otherwise the legacy hardcoded registry is used.
    """
    caps = defs if defs is not None else _CAPABILITIES
    out: list[dict] = []
    for cap_key, cap in caps.items():
        is_placeholder = bool(cap.get("placeholder"))
        platform_key = (
            "" if is_placeholder else (getattr(settings, cap.get("platform_key_attr", ""), "") or "")
        )
        admin_key = cap.get("platform_key_value") or ""
        out.append(
            {
                "capability": cap_key,
                "label": cap.get("label", cap_key),
                "kind": cap.get("kind", "toolset"),
                "category": cap.get("category", "未分类"),
                "description": cap.get("description", ""),
                "toolsets": list(cap.get("toolsets", [])),
                "env_key": cap.get("env_key", ""),
                "platform_key_attr": cap.get("platform_key_attr", ""),
                "config": dict(cap.get("config", {})),
                "requires_key": bool(cap.get("requires_key", not is_placeholder)),
                "platform_key_configured": bool(platform_key or admin_key),
                "has_platform_key": bool(admin_key),
                "placeholder": is_placeholder,
            }
        )
    return out


def capability_states(
    settings,
    user_caps: dict | None = None,
    platform_defaults: dict | None = None,
    defs: dict | None = None,
) -> list[dict]:
    """Resolve per-capability effective state for a user.

    Single source of truth for the precedence rule (per-user > default > off).
    ``source`` explains *why* a capability is on/off: ``user`` (explicit
    override), ``default`` (platform default inject), or ``off``.
    """
    user_caps = user_caps or {}
    platform_defaults = platform_defaults or {}
    caps = defs if defs is not None else _CAPABILITIES
    out: list[dict] = []
    for cap_key, cap in caps.items():
        is_placeholder = bool(cap.get("placeholder"))
        if is_placeholder:
            # Placeholders are never effective, regardless of grants/defaults.
            out.append(
                {
                    "capability": cap_key,
                    "label": cap.get("label", cap_key),
                    "user_override": False,
                    "user_enabled": None,
                    "default_inject": False,
                    "effective_enabled": False,
                    "source": "placeholder",
                    "platform_key_configured": False,
                    "placeholder": True,
                }
            )
            continue
        default_inject = bool(
            getattr(platform_defaults[cap_key], "default_inject", False)
        ) if cap_key in platform_defaults else False
        if cap_key in user_caps:
            user_enabled = bool(getattr(user_caps[cap_key], "enabled", False))
            effective = user_enabled
            source = "user"
            user_override = True
        elif cap_key in platform_defaults:
            effective = default_inject
            source = "default"
            user_override = False
            user_enabled = None
        else:
            effective = False
            source = "off"
            user_override = False
            user_enabled = None
        platform_key = getattr(settings, cap.get("platform_key_attr", ""), "") or ""
        admin_key = cap.get("platform_key_value") or ""
        out.append(
            {
                "capability": cap_key,
                "label": cap.get("label", cap_key),
                "kind": cap.get("kind", "toolset"),
                "category": cap.get("category", "未分类"),
                "description": cap.get("description", ""),
                "user_override": user_override,
                "user_enabled": user_enabled,
                "default_inject": default_inject,
                "effective_enabled": effective,
                "source": source,
                "platform_key_configured": bool(platform_key or admin_key),
                "has_platform_key": bool(admin_key),
                "placeholder": False,
            }
        )
    return out


def known_capabilities(defs: dict | None = None) -> list[str]:
    """Return the capability keys known to the registry."""
    caps = defs if defs is not None else _CAPABILITIES
    return list(caps)


def capability_by_env_key(defs: dict | None = None) -> dict[str, str]:
    """Map each capability's env-var key to its capability key.

    Used by the hot-patch path to read user-provided capability keys (e.g. a
    ``TAVILY_API_KEY`` the user set inside their container) back into a
    capability-keyed dict that ``build_capability_plan`` consumes via
    ``user_keys``. Keeping this mapping in the registry means new capabilities
    are supported without touching the container manager.
    """
    caps = defs if defs is not None else _CAPABILITIES
    out: dict[str, str] = {}
    for cap_key, cap in caps.items():
        if cap.get("placeholder"):
            continue
        env_key = cap.get("env_key")
        if env_key and env_key not in out:
            out[env_key] = cap_key
    return out
