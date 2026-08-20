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


@dataclass
class CapabilityPlan:
    enabled_toolsets: list[str]
    env: dict[str, str]
    config_overrides: dict


# Registry of known capabilities. Each entry declares how the capability maps
# into the container: toolsets to mount, the env var carrying its key, the
# settings attr holding the platform pool key, and any config.yaml section.
_CAPABILITIES: dict[str, dict] = {
    "web_search": {
        "label": "联网搜索",
        "toolsets": ["web"],
        "env_key": "TAVILY_API_KEY",
        "platform_key_attr": "tavily_api_key",
        "config": {"web": {"backend": "tavily"}},
    },
    # Placeholder capabilities: declared for the UI roadmap but NOT implemented
    # (no toolsets, no key, no config). build_capability_plan skips them, so a
    # grant/default against them is inert and never affects a container. Adding
    # a placeholder must not change any existing capability's behavior.
    "browser": {
        "label": "浏览器",
        "placeholder": True,
        "toolsets": [],
        "env_key": "",
        "platform_key_attr": "",
        "config": {},
    },
    "image_gen": {
        "label": "图像生成",
        "placeholder": True,
        "toolsets": [],
        "env_key": "",
        "platform_key_attr": "",
        "config": {},
    },
}


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
) -> CapabilityPlan:
    user_caps = user_caps or {}
    platform_defaults = platform_defaults or {}
    user_keys = user_keys or {}

    toolsets = parse_hermes_api_toolsets(getattr(settings, "hermes_api_toolsets", ""))
    env: dict[str, str] = {}
    config: dict = {}

    for cap_key, cap in _CAPABILITIES.items():
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

        platform_key = getattr(settings, cap["platform_key_attr"], "") or ""
        key = user_keys.get(cap_key) or platform_key
        if not key:
            # Conditional mounting: no usable key -> don't mount the toolset.
            continue

        for ts in cap["toolsets"]:
            if ts not in toolsets:
                toolsets.append(ts)
        env[cap["env_key"]] = key
        config.update(cap["config"])

    return CapabilityPlan(enabled_toolsets=toolsets, env=env, config_overrides=config)


def capability_registry(settings) -> list[dict]:
    """Return capability metadata for admin UI rendering.

    Exposes each capability's display info plus whether its platform pool key
    is configured. Never returns the key value itself.
    """
    out: list[dict] = []
    for cap_key, cap in _CAPABILITIES.items():
        is_placeholder = bool(cap.get("placeholder"))
        platform_key = (
            "" if is_placeholder else (getattr(settings, cap["platform_key_attr"], "") or "")
        )
        out.append(
            {
                "capability": cap_key,
                "label": cap.get("label", cap_key),
                "toolsets": list(cap["toolsets"]),
                "env_key": cap["env_key"],
                "requires_key": not is_placeholder,
                "platform_key_configured": bool(platform_key),
                "placeholder": is_placeholder,
            }
        )
    return out


def capability_states(
    settings,
    user_caps: dict | None = None,
    platform_defaults: dict | None = None,
) -> list[dict]:
    """Resolve per-capability effective state for a user.

    Single source of truth for the precedence rule (per-user > default > off).
    ``source`` explains *why* a capability is on/off: ``user`` (explicit
    override), ``default`` (platform default inject), or ``off``.
    """
    user_caps = user_caps or {}
    platform_defaults = platform_defaults or {}
    out: list[dict] = []
    for cap_key, cap in _CAPABILITIES.items():
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
        platform_key = getattr(settings, cap["platform_key_attr"], "") or ""
        out.append(
            {
                "capability": cap_key,
                "label": cap.get("label", cap_key),
                "user_override": user_override,
                "user_enabled": user_enabled,
                "default_inject": default_inject,
                "effective_enabled": effective,
                "source": source,
                "platform_key_configured": bool(platform_key),
                "placeholder": False,
            }
        )
    return out


def known_capabilities() -> list[str]:
    """Return the capability keys known to the registry."""
    return list(_CAPABILITIES)


def capability_by_env_key() -> dict[str, str]:
    """Map each capability's env-var key to its capability key.

    Used by the hot-patch path to read user-provided capability keys (e.g. a
    ``TAVILY_API_KEY`` the user set inside their container) back into a
    capability-keyed dict that ``build_capability_plan`` consumes via
    ``user_keys``. Keeping this mapping in the registry means new capabilities
    are supported without touching the container manager.
    """
    return {
        cap["env_key"]: cap_key
        for cap_key, cap in _CAPABILITIES.items()
        if not cap.get("placeholder") and cap.get("env_key")
    }
