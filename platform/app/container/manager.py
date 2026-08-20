"""Docker container lifecycle management for per-user dedicated runtime instances."""

from __future__ import annotations

import io
import json
import re
import logging
import os
import secrets
import socket
import tarfile
import time

import docker
import yaml
from docker.errors import APIError as DockerAPIError
from docker.errors import NotFound as DockerNotFound
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities import CapabilityPlan, build_capability_plan, capability_by_env_key, parse_hermes_api_toolsets
from app.config import settings
from app.db.models import (
    Container,
    DEFAULT_INSTALL_FLAG,
    McpConnector,
    PlatformCapabilityDefault,
    SystemFlag,
    User,
    UserCapability,
    UserMcpConnector,
    UserPortBinding,
)

logger = logging.getLogger(__name__)

# SystemFlag key holding the per-skill default-install overrides as a JSON map
# {skill_name: true|false}. A present override always wins over the master
# "default install all" switch (DEFAULT_INSTALL_FLAG).
DEFAULT_OVERRIDE_FLAG = "default_install_overrides"

_client: docker.DockerClient | None = None


def _docker() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def get_docker_container(container_id_or_name: str) -> docker.models.containers.Container:
    return _docker().containers.get(container_id_or_name)


def _ensure_network() -> None:
    """Create the internal Docker network if it doesn't exist."""
    client = _docker()
    try:
        client.networks.get(settings.container_network)
    except DockerNotFound:
        client.networks.create(
            settings.container_network,
            driver="bridge",
            internal=False,  # allow internet access for tool downloads
        )


def _published_binding(container: docker.models.containers.Container, container_port: str) -> tuple[str, str]:
    """Return (host_ip, host_port) for a published container port."""
    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
    bindings = ports.get(container_port) or []
    if not bindings:
        return "", ""
    host_ip = bindings[0].get("HostIp", "") or ""
    host_port = bindings[0].get("HostPort", "") or ""
    return host_ip, host_port


def _is_host_port_in_use(client: docker.DockerClient, host_port: int) -> bool:
    """Return True if any container currently publishes the given host port."""
    port_str = str(host_port)
    for c in client.containers.list(all=True):
        ports = c.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        for bindings in ports.values():
            for binding in (bindings or []):
                if (binding.get("HostPort") or "") == port_str:
                    return True
    return False


def _runtime_backend() -> str:
    return (settings.dedicated_runtime_backend or "hermes").strip().lower()


def _container_name(short_id: str) -> str:
    prefix = (settings.dedicated_runtime_container_name_prefix or "hermes-user").strip() or "hermes-user"
    return f"{prefix}-{short_id}"


def _data_volume_name(short_id: str) -> str:
    prefix = (settings.dedicated_runtime_data_volume_prefix or "hermes-data").strip() or "hermes-data"
    return f"{prefix}-{short_id}"


def _hermes_home_volume_name(short_id: str) -> str:
    return f"{_data_volume_name(short_id)}-home"


def _internal_port() -> int:
    return settings.dedicated_hermes_internal_port


def _runtime_image() -> str:
    return settings.hermes_image


def _runtime_mount_target() -> str:
    return "/workspace"


def _resolve_builtin_skills_host_dir() -> str | None:
    """Resolve the host directory holding the bundled built-in skills.

    The gateway is started with ``./hermes-agent/skills`` bind-mounted at
    ``/app/hermes_skills`` (see docker-compose.yml). User containers are siblings
    on the same Docker daemon, so to expose the *same live* skills to them we
    must bind-mount the host absolute path. We resolve it from the gateway's own
    mount (so it stays correct across environments) and fall back to the
    ``BUILTIN_SKILLS_HOST_DIR`` env var.

    Mounting the skills read-only means editing the host ``hermes-agent/skills``
    tree (e.g. adding a category in catalog.json) is picked up on the next
    container start — no image rebuild required.

    Note: the resolved path is a *host* path that is meaningful only to the
    Docker daemon, not to this (gateway) container's own filesystem. So we must
    NOT validate it with ``os.path.exists`` here — the daemon resolves it when
    creating the sibling container.
    """
    env = os.environ.get("BUILTIN_SKILLS_HOST_DIR")
    if env:
        return env
    try:
        cid = socket.gethostname()
        c = _docker().containers.get(cid)
        for m in c.attrs.get("Mounts", []) or []:
            if m.get("Destination") == "/app/hermes_skills":
                src = m.get("Source")
                if src:
                    return src
    except Exception:
        logger.debug("Could not resolve built-in skills host dir from self mount", exc_info=True)
    return None


def _build_runtime_mounts(data_vol: str, short_id: str) -> list:
    """Build volume mounts for the user container.

    Hermes containers get two named volumes:
      - ``/workspace``   — user workspace (skills, files, sessions)
      - ``/opt/data``    — HERMES_HOME (profiles, config, skills cache)

    They also get the bundled built-in skills mounted read-only at
    ``/opt/hermes/skills`` from the host (see ``_resolve_builtin_skills_host_dir``)
    so the live skill tree (incl. catalog.json) is used by skills_sync and the
    skill listing API without rebuilding the hermes image.
    """
    mounts = [
        docker.types.Mount(_runtime_mount_target(), data_vol, type="volume"),
    ]
    if _runtime_backend() == "hermes":
        home_vol = _hermes_home_volume_name(short_id)
        mounts.append(docker.types.Mount("/opt/data", home_vol, type="volume"))
        host_skills = _resolve_builtin_skills_host_dir()
        if host_skills:
            mounts.append(
                docker.types.Mount("/opt/hermes/skills", host_skills, type="bind", read_only=True)
            )
    return mounts


def _runtime_command() -> list[str]:
    return ["gateway", "run"]


def _runtime_environment(
    container_token: str,
    sso_token: str | None,
    plan: CapabilityPlan | None = None,
) -> dict[str, str]:
    if plan is None:
        plan = build_capability_plan(settings)
    env = {
        "COMWORKER_PROXY__URL": "http://gateway:8080/llm/v1",
        "COMWORKER_PROXY__TOKEN": container_token,
        "TZ": settings.container_tz,
    }
    env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "API_SERVER_ENABLED": "true",
                "API_SERVER_HOST": "0.0.0.0",
                "API_SERVER_PORT": str(settings.dedicated_hermes_internal_port),
                "API_SERVER_KEY": settings.dedicated_hermes_api_key,
                "GATEWAY_ALLOW_ALL_USERS": "true",
                "OPENAI_API_KEY": settings.dedicated_hermes_default_api_key,
                # NOTE: HERMES_API_TOOLSETS is intentionally NOT set here. The
                # hermes entrypoint rewrites platform_toolsets.api_server in
                # config.yaml from this env var on every start, which would
                # overwrite capability-driven toolsets (e.g. "web") and defeat
                # hot-patch. config.yaml (written by _write_hermes_runtime_files)
                # is the single source of truth for api_server.
                # NOTE: COMWORKER_AGENTS__DEFAULTS__MODEL is also intentionally
                # NOT set here. The entrypoint overwrites config.yaml's
                # model.default from this env var on every start, which would
                # clobber a user-chosen model (e.g. "glm-5.2") and replace it
                # with the platform default. config.yaml (whose model.default is
                # preserved by _write_hermes_runtime_files) is the single source
                # of truth for the default model.
                "HERMES_REASONING_EFFORT": settings.hermes_reasoning_effort,
                "HERMES_SERVICE_TIER": settings.hermes_service_tier,
                "HERMES_YOLO_MODE": "true",
            }
        )
    env.update(plan.env)
    if sso_token:
        env["INFOX_MED_TOKEN"] = sso_token
    return env


def _container_config(container: docker.models.containers.Container) -> dict:
    config = container.attrs.get("Config", {}) or {}
    return config if isinstance(config, dict) else {}


def _container_matches_runtime(container: docker.models.containers.Container) -> bool:
    """Return whether an existing user container matches the configured runtime backend."""
    config = _container_config(container)
    env = set(config.get("Env") or [])
    entrypoint = " ".join(str(part) for part in (config.get("Entrypoint") or []))
    command = " ".join(str(part) for part in (config.get("Cmd") or []))

    return (
        "API_SERVER_ENABLED=true" in env
        and "/opt/hermes/docker/entrypoint.sh" in entrypoint
        and "gateway" in command
    )


def _runtime_published_ports() -> dict[str, tuple[str, int | None]]:
    return {
        f"{_internal_port()}/tcp": (settings.user_container_bind_ip, None),
    }


def _runtime_preferred_ports(browser_port: int | None, service_port: int | None) -> dict[str, tuple[str, int | None]] | None:
    return None


def _published_port_bindings(container: docker.models.containers.Container) -> tuple[tuple[str, str], tuple[str, str]]:
    return ("", ""), _published_binding(container, f"{_internal_port()}/tcp")


def _build_runtime_metadata_markdown(user_id: str, container_name: str, runtime_backend: str) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())
    payload = {
        "user_id": user_id,
        "container": container_name,
        "runtime_backend": runtime_backend,
        "generated_at": now,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _build_hermes_config_yaml(plan: CapabilityPlan | None = None) -> str:
    if plan is None:
        plan = build_capability_plan(settings)
    config = {
        "model": {
            "default": settings.default_model,
            "provider": settings.dedicated_hermes_default_provider,
            "base_url": settings.dedicated_hermes_default_base_url,
        },
        "platform_toolsets": {
            "api_server": plan.enabled_toolsets,
        },
        "agent": {
            "reasoning_effort": settings.hermes_reasoning_effort,
            "service_tier": settings.hermes_service_tier,
            # ComWorker exposes hermes as a user-facing conversational UI,
            # not an interactive coding terminal. The turn-end verification nudge
            # ("[System: You edited code in this turn...]") is internal guidance
            # meant for CLI/local/gateway surfaces; surfacing it here leaks engine
            # internals into the chat. Disable it (hermes' own "auto" default turns
            # this OFF on recognized messaging platforms — we pin it explicitly).
            "verify_on_stop": False,
        },
    }
    config.update(plan.config_overrides)
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def _hermes_api_toolsets() -> list[str]:
    return parse_hermes_api_toolsets(settings.hermes_api_toolsets)


def _build_hermes_env_file(
    preserve_vars: dict | None = None,
    plan: CapabilityPlan | None = None,
) -> str:
    if plan is None:
        plan = build_capability_plan(settings)
    lines = [
        f"API_SERVER_KEY={settings.dedicated_hermes_api_key}",
        "GATEWAY_ALLOW_ALL_USERS=true",
        f"HERMES_API_TOOLSETS={settings.hermes_api_toolsets}",
        f"HERMES_REASONING_EFFORT={settings.hermes_reasoning_effort}",
        f"HERMES_SERVICE_TIER={settings.hermes_service_tier}",
        # Belt-and-suspenders: env overrides config. See agent.verify_on_stop
        # above — hermes runs as a conversational surface here, so the internal
        # edit->verify nudge must never reach the chat transcript.
        "HERMES_VERIFY_ON_STOP=false",
    ]
    default_api_key = (settings.dedicated_hermes_default_api_key or "").strip()
    if default_api_key:
        lines.append(f"OPENAI_API_KEY={default_api_key}")
    for key, value in plan.env.items():
        lines.append(f"{key}={value}")
    # Preserve messaging-channel vars (FEISHU_*, TELEGRAM_*, ...) configured via
    # the web onboarding flow / `hermes gateway setup`, so rebuilding the user
    # container does not wipe channel credentials.
    for key, value in (preserve_vars or {}).items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"



def _write_runtime_metadata(container: docker.models.containers.Container, markdown: str) -> None:
    content = markdown.encode("utf-8")
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        workspace_dir = tarfile.TarInfo(name="workspace")
        workspace_dir.type = tarfile.DIRTYPE
        workspace_dir.mode = 0o755
        workspace_dir.mtime = int(time.time())
        tar.addfile(workspace_dir)

        metadata_file = tarfile.TarInfo(name="workspace/platform-runtime.json")
        metadata_file.size = len(content)
        metadata_file.mode = 0o644
        metadata_file.mtime = int(time.time())
        tar.addfile(metadata_file, io.BytesIO(content))

    tar_buffer.seek(0)
    ok = container.put_archive("/", tar_buffer.read())
    if not ok:
        raise RuntimeError("failed to write platform-runtime.json into container workspace")


def _repair_hermes_data_ownership(container: docker.models.containers.Container) -> None:
    """Make files injected into the Hermes data volume readable/writable by the hermes user.

    NOTE (F8): the relay (mcp_oauth_relay) runs as the ``hermes`` runtime user and
    MUST be able to READ ``/opt/data/config.yaml`` to resolve mcp_servers / cli
    connectors. If that file ends up owned by another uid with mode 0600 (e.g. the
    volume-chown branch below silently fails on some Docker setups), every OAuth/CLI
    relay request 404s because ``_server_entry``/``_cli_entry`` hit PermissionError.
    The ``chmod 0644`` fallback at the end guarantees readability regardless of the
    chown outcome. See docs/connector-adversarial-review.md.
    """
    data_volume = ""
    for mount in container.attrs.get("Mounts", []) or []:
        if mount.get("Destination") == "/opt/data" and mount.get("Type") == "volume":
            data_volume = str(mount.get("Name") or "").strip()
            break

    if data_volume:
        try:
            _docker().containers.run(
                image=_runtime_image(),
                entrypoint="chown",
                command=["-R", "hermes:hermes", "/opt/data"],
                mounts=[docker.types.Mount("/opt/data", data_volume, type="volume")],
                remove=True,
            )
        except Exception as exc:  # best-effort; chmod below is the guaranteed fix
            logger.warning("hermes data chown (volume) failed: %s", exc)
    else:
        result = container.exec_run(["chown", "-R", "hermes:hermes", "/opt/data"], user="root")
        exit_code = getattr(result, "exit_code", result[0] if isinstance(result, tuple) else 0)
        if exit_code != 0:
            output = getattr(result, "output", result[1] if isinstance(result, tuple) and len(result) > 1 else b"")
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            raise RuntimeError(f"failed to repair Hermes data ownership: {output}")

    # Guaranteed-readable fallback: chown alone is unreliable on some volume/bind
    # setups (and the volume branch cannot verify success), so explicitly make
    # config.yaml readable by the hermes runtime user. Single-tenant container,
    # so world-readable is an acceptable trade-off versus a broken relay.
    container.exec_run(["chmod", "0644", "/opt/data/config.yaml"], user="root")


def _read_existing_hermes_config(container: docker.models.containers.Container) -> dict:
    """Read existing config.yaml from container, return {} if not found."""
    try:
        result = container.exec_run(["cat", "/opt/data/config.yaml"], user="root")
        if result.exit_code == 0 and result.output:
            return yaml.safe_load(result.output.decode("utf-8")) or {}
    except Exception:
        pass
    return {}


# Messaging-channel env prefixes managed by the web onboarding flow / hermes
# gateway setup (not by the platform). These must survive container rebuilds or
# channel credentials configured by the user are lost.
_CHANNEL_ENV_PREFIXES = (
    "FEISHU_", "LARK_", "TELEGRAM_", "DISCORD_", "SLACK_", "WHATSAPP_",
    "MATRIX_", "MATTERMOST_", "WEIXIN_", "WECOM_", "DINGTALK_", "SIGNAL_",
    "IRC_", "NOSTR_", "TWITCH_", "ZALO_", "QQBOT_", "GOOGLECHAT_", "MSTEAMS_",
)


def _read_existing_hermes_env_channel_vars(container: docker.models.containers.Container) -> dict:
    """Read channel-related env vars from existing /opt/data/.env so they survive a rebuild."""
    try:
        result = container.exec_run(["cat", "/opt/data/.env"], user="root")
    except Exception:
        return {}
    if getattr(result, "exit_code", 1) != 0 or not result.output:
        return {}
    preserved: dict = {}
    for raw_line in result.output.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith(_CHANNEL_ENV_PREFIXES):
            preserved[key] = value.strip()
    return preserved


def _read_existing_capability_keys(container: docker.models.containers.Container) -> dict[str, str]:
    """Read user-provided capability keys from the existing container ``.env``.

    Returns a *capability-keyed* dict (e.g. ``{"web_search": "tvly-..."}``) so
    it can be fed straight to ``build_capability_plan`` as ``user_keys``. This
    lets a user-set key satisfy conditional mounting even when no platform pool
    key is configured, and keeps the user's key instead of the platform key on
    hot-patch. Only known capability env keys are captured; unrelated vars are
    ignored (they are not capability-managed).
    """
    env_to_cap = capability_by_env_key()
    if not env_to_cap:
        return {}
    try:
        result = container.exec_run(["cat", "/opt/data/.env"], user="root")
    except Exception:
        return {}
    if getattr(result, "exit_code", 1) != 0 or not result.output:
        return {}
    out: dict[str, str] = {}
    for raw_line in result.output.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in env_to_cap:
            out[env_to_cap[key]] = value.strip()
    return out



_ENV_VAR_PATTERN = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# A connector can only be injected into a container if its config carries the
# fields the MCP client needs for the chosen transport. An HTTP/SSE server
# without ``url`` (or a stdio server without ``command``) would crash the MCP
# client on startup, so we hard-guard against it.
_REQUIRED_CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "stdio": ("command",),
    "streamable_http": ("url",),
    "sse": ("url",),
}


def _missing_required_config_fields(transport: str, config_json: dict | None) -> list[str]:
    """Return required config fields absent for ``transport`` (empty == valid)."""
    cfg = config_json or {}
    required = _REQUIRED_CONFIG_FIELDS.get(transport, ())
    return [f for f in required if not cfg.get(f)]


def _inject_shared_credential(cfg: dict, secret: str) -> dict:
    """Inject a platform-held shared secret into an MCP server config.

    Replaces ``${...}`` placeholders (e.g. ``${MCP_X_API_KEY}``) in headers/env
    with the real secret; if no placeholder exists, falls back to a Bearer header.
    """
    cfg = dict(cfg)

    def _sub(v):
        if isinstance(v, str):
            return _ENV_VAR_PATTERN.sub(lambda m: secret, v)
        return v

    headers = cfg.get("headers")
    if isinstance(headers, dict) and any(
        isinstance(v, str) and "${" in v for v in headers.values()
    ):
        cfg["headers"] = {k: _sub(v) for k, v in headers.items()}
    elif not isinstance(headers, dict):
        cfg["headers"] = {"Authorization": f"Bearer {secret}"}
    if isinstance(cfg.get("env"), dict):
        cfg["env"] = {k: _sub(v) for k, v in cfg["env"].items()}
    return cfg


def _resolve_env_vars(cfg: dict, values: dict) -> dict:
    """Substitute ``${KEY}`` placeholders (headers/env/url) from a value dict.

    Used by the ``api_key`` strategy: the connector's config carries
    ``${PATSNAP_API_KEY}``-style placeholders and the user (or admin, via
    shared) supplies the actual values. Unknown placeholders are left intact.
    """
    cfg = dict(cfg)

    def _sub(v: object) -> object:
        if isinstance(v, str):
            return _ENV_VAR_PATTERN.sub(
                lambda m: values.get(m.group(0)[2:-1], m.group(0)), v
            )
        return v

    if isinstance(cfg.get("headers"), dict):
        cfg["headers"] = {k: _sub(v) for k, v in cfg["headers"].items()}
    if isinstance(cfg.get("env"), dict):
        cfg["env"] = {k: _sub(v) for k, v in cfg["env"].items()}
    if isinstance(cfg.get("url"), str):
        cfg["url"] = _sub(cfg["url"])
    return cfg


async def _collect_mcp_servers(db: AsyncSession, user_id: str) -> dict:
    """Build the platform-managed ``mcp_servers`` dict for a user's container.

    Precedence (mirrors the skills default-install logic):
      - is_mandatory connectors are always included (locked on)
      - a user override (UserMcpConnector) wins for is_default connectors
      - is_default connectors are included unless the user disabled them
      - a non-default connector the user explicitly enabled is included
      - a connector the user explicitly disabled is excluded
    Pure-custom user servers (connector_id is NULL) are also included.
    """
    result = await db.execute(select(McpConnector).where(McpConnector.status == "active"))
    connectors = result.scalars().all()

    ur = await db.execute(
        select(UserMcpConnector).where(UserMcpConnector.user_id == user_id)
    )
    overrides = {row.name: row for row in ur.scalars().all()}

    servers: dict[str, dict] = {}
    for c in connectors:
        name = c.name
        override = overrides.get(name)
        if c.is_mandatory:
            effective = True
        elif override is not None:
            effective = override.enabled
        elif c.is_default:
            effective = True
        else:
            effective = False
        if not effective:
            continue
        # CLI connectors are NOT injected as MCP servers; their install/auth is
        # handled in-container via the relay's /cli/* endpoints (see
        # _collect_cli_specs -> config["mcp_cli_connectors"]). Skip explicitly
        # rather than relying on the missing-field guard below.
        if c.credential_strategy == "cli":
            continue
        if override is not None and override.personal_config_json:
            cfg = dict(override.personal_config_json)
        else:
            cfg = dict(c.config_json or {})
        # Defense-in-depth: never inject a server missing its transport's
        # required fields (e.g. an HTTP connector with no url). This guards
        # against an admin toggling default/mandatory or a user enabling a
        # connector whose config is still a placeholder.
        missing = _missing_required_config_fields(c.transport, cfg)
        if missing:
            logger.warning(
                "skipping connector %s (transport=%s): missing required config %s",
                name, c.transport, missing,
            )
            continue
        if c.credential_strategy == "shared" and c.shared_credential:
            cfg = _inject_shared_credential(cfg, c.shared_credential)
        elif c.credential_strategy == "api_key":
            # Per-user keys win; fall back to admin-provided shared secret.
            user_creds = (override.credential_json or {}) if override else {}
            if user_creds:
                cfg = _resolve_env_vars(cfg, user_creds)
            elif c.shared_credential:
                cfg = _inject_shared_credential(cfg, c.shared_credential)
        elif c.credential_strategy == "oauth":
            cfg.setdefault("auth", "oauth")
        # strategy == "cli": the container installs + authorizes the CLI in
        # place (see hermes mcp_oauth_relay cli endpoints); the platform does
        # not inject a server here. strategy == "none": plain injection.
        cfg["enabled"] = True
        servers[name] = cfg

    # pure-custom user servers (not in the platform catalog)
    for name, row in overrides.items():
        if row.connector_id is None and row.enabled and row.personal_config_json:
            cfg = dict(row.personal_config_json)
            cfg["enabled"] = True
            servers[name] = cfg

    cli_specs = await _collect_cli_specs(db, user_id)
    return {"servers": servers, "cli_specs": cli_specs}


async def _collect_cli_specs(db: AsyncSession, user_id: str) -> dict:
    """Surface enabled CLI-connector install/auth specs to the container.

    CLI connectors are NOT injected as MCP servers by the platform (the
    container installs the native CLI and authorizes it in-place). We ship
    only the *enabled* connectors' ``cli_config_json`` to the container under
    the top-level ``mcp_cli_connectors`` config key so the relay's
    /cli/install|auth|status endpoints know which commands to run, while
    respecting the user's enable/disable choices.
    """
    result = await db.execute(
        select(McpConnector).where(
            McpConnector.credential_strategy == "cli",
            McpConnector.status == "active",
        )
    )
    connectors = result.scalars().all()

    ur = await db.execute(
        select(UserMcpConnector).where(UserMcpConnector.user_id == user_id)
    )
    overrides = {row.name: row for row in ur.scalars().all()}

    specs: dict[str, dict] = {}
    for c in connectors:
        override = overrides.get(c.name)
        if c.is_mandatory:
            effective = True
        elif override is not None:
            effective = override.enabled
        elif c.is_default:
            effective = True
        else:
            effective = False
        if effective and c.cli_config_json:
            specs[c.name] = c.cli_config_json
    return specs


async def _apply_mcp_servers_to_container(
    container_record: Container,
    db: AsyncSession,
    user_id: str,
) -> None:
    """Merge platform-managed mcp_servers into the container config.yaml.

    Preserves user-added servers that are NOT managed by the platform (i.e. keys
    absent from the platform set) so a rebuild / push does not wipe them.
    """
    docker_container = get_docker_container(container_record.docker_id)
    collected = await _collect_mcp_servers(db, user_id)
    platform_servers = collected["servers"]
    existing = _read_existing_hermes_config(docker_container)
    existing_servers = existing.get("mcp_servers") or {}
    user_only = {k: v for k, v in existing_servers.items() if k not in platform_servers}
    merged = {**user_only, **platform_servers}
    config = dict(existing)
    if merged:
        config["mcp_servers"] = merged
    else:
        config.pop("mcp_servers", None)
    # CLI connector specs (install/auth commands) for the container relay.
    if collected["cli_specs"]:
        config["mcp_cli_connectors"] = collected["cli_specs"]
    else:
        config.pop("mcp_cli_connectors", None)
    content = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        f = tarfile.TarInfo(name="config.yaml")
        f.size = len(content)
        f.mode = 0o644
        f.mtime = int(time.time())
        tar.addfile(f, io.BytesIO(content))
    tar_buffer.seek(0)
    ok = docker_container.put_archive("/opt/data", tar_buffer.read())
    if not ok:
        raise RuntimeError("failed to write mcp_servers into container config.yaml")
    _repair_hermes_data_ownership(docker_container)


# ---------------------------------------------------------------------------
# DB-backed durable user config (source of truth for rebuilds)
# ---------------------------------------------------------------------------

def _extract_user_config(config: dict) -> dict:
    """Pull the user-managed portions of a hermes config.yaml into a portable dict."""
    out: dict = {}
    cps = config.get("custom_providers") or []
    user_cps = [
        p for p in cps
        if isinstance(p, dict) and p.get("name") not in ("platform", "platform-gateway")
    ]
    if user_cps:
        out["custom_providers"] = user_cps
    model = config.get("model") or {}
    default = model.get("default")
    provider = model.get("provider")
    if default:
        out["model_default"] = default
    if provider and provider != "platform-gateway":
        out["model_provider"] = provider
    return out


def _apply_user_config(platform_config: dict, user_config: dict | None) -> dict:
    """Overlay DB user_config (source of truth) onto a freshly built platform config."""
    if not user_config or not isinstance(user_config, dict):
        return platform_config
    uc = user_config
    user_cps = uc.get("custom_providers") or []
    if user_cps:
        gateway = [
            p for p in platform_config.get("custom_providers", [])
            if isinstance(p, dict) and p.get("name") == "platform-gateway"
        ]
        existing_names = {p.get("name") for p in gateway}
        merged = list(gateway)
        for p in user_cps:
            if isinstance(p, dict) and p.get("name") and p["name"] not in existing_names:
                merged.append(p)
                existing_names.add(p["name"])
        if merged:
            platform_config["custom_providers"] = merged
        else:
            platform_config.pop("custom_providers", None)
    default = uc.get("model_default")
    provider = uc.get("model_provider")
    if default:
        platform_config.setdefault("model", {})["default"] = default
        if provider:
            platform_config["model"]["provider"] = provider
            platform_config["model"].pop("base_url", None)
    # Apply admin-managed per-provider disable toggles (survives rebuilds
    # because user_config is the authoritative source of truth).
    disabled = uc.get("disabled_providers") or []
    if disabled and isinstance(disabled, list):
        for p in platform_config.get("custom_providers") or []:
            if isinstance(p, dict) and p.get("name") in disabled:
                p["disabled"] = True
    return platform_config


async def _load_user_config(db, user_id: str) -> dict | None:
    """Load a user's persisted Hermes config from Postgres (None if absent)."""
    from sqlalchemy import select
    from app.db.models import Container
    rec = (await db.execute(select(Container).where(Container.user_id == user_id))).scalar_one_or_none()
    if rec is None:
        return None
    uc = rec.user_config
    return uc if isinstance(uc, dict) else None


async def _capture_user_config_to_db(db, user_id: str, existing_config: dict) -> None:
    """Merge user data read from the volume into Postgres (never downgrades DB).

    Safety net for data that exists in the volume but not yet in DB (legacy data
    or edits made inside the container). The primary write path is the models
    route, which persists to DB the moment the user saves.
    """
    extracted = _extract_user_config(existing_config)
    if not extracted:
        return
    from sqlalchemy import select
    from app.db.models import Container
    rec = (await db.execute(select(Container).where(Container.user_id == user_id))).scalar_one_or_none()
    if rec is None:
        return
    existing = rec.user_config or {}
    merged = dict(existing)
    for k, v in extracted.items():
        if k not in merged or not merged[k]:
            merged[k] = v
    # Dedupe custom_providers by name
    cps = merged.get("custom_providers") or []
    if isinstance(cps, list) and cps:
        seen = set()
        deduped = []
        for p in cps:
            n = p.get("name") if isinstance(p, dict) else None
            if n in seen:
                continue
            if n is not None:
                seen.add(n)
            deduped.append(p)
        merged["custom_providers"] = deduped
    rec.user_config = merged
    await db.commit()


def _write_hermes_runtime_files(
    container: docker.models.containers.Container,
    plan: CapabilityPlan | None = None,
    mcp_servers: dict | None = None,
    db=None,
    user_id: str | None = None,
    user_config: dict | None = None,
) -> dict:
    platform_config = yaml.safe_load(_build_hermes_config_yaml(plan)) or {}
    existing_config = _read_existing_hermes_config(container)

    if existing_config.get("custom_providers"):
        user_providers = [
            p for p in existing_config["custom_providers"]
            if isinstance(p, dict) and p.get("name") not in ("platform-gateway", "platform")
        ]
        if user_providers:
            gateway = [
                p for p in platform_config.get("custom_providers", [])
                if isinstance(p, dict) and p.get("name") == "platform-gateway"
            ]
            platform_config["custom_providers"] = gateway + user_providers
    if (existing_config.get("model") or {}).get("default"):
        platform_config.setdefault("model", {})["default"] = existing_config["model"]["default"]
    # Preserve user's model.provider (e.g. "deepseek") so user-added
    # custom_providers with their own API keys are routed directly rather
    # than being forced through platform-gateway on container restart.
    existing_provider = (existing_config.get("model") or {}).get("provider", "")
    if existing_provider and existing_provider != "platform-gateway":
        platform_config.setdefault("model", {})["provider"] = existing_provider
        platform_config["model"].pop("base_url", None)

    # Preserve user-customized capability config sections (e.g. web.backend)
    # only while the capability stays enabled in the new plan. A revoked
    # capability is absent from plan.config_overrides, so its section is dropped
    # instead of lingering without the matching toolset. User values win per-key.
    # plan=None is the settings-only baseline (no DB-driven capabilities), so
    # there is nothing capability-specific to preserve.
    if plan is not None:
        for section, section_cfg in plan.config_overrides.items():
            if not isinstance(section_cfg, dict):
                continue
            existing_section = existing_config.get(section)
            if isinstance(existing_section, dict):
                platform_config[section] = {**section_cfg, **existing_section}

    # Merge platform-managed MCP servers, preserving user-added servers that are
    # NOT managed by the platform (so a container rebuild does not wipe them).
    if mcp_servers is not None:
        servers = mcp_servers.get("servers", {}) if isinstance(mcp_servers, dict) else mcp_servers
        cli_specs = mcp_servers.get("cli_specs", {}) if isinstance(mcp_servers, dict) else {}
        existing_servers = existing_config.get("mcp_servers") or {}
        user_only = {k: v for k, v in existing_servers.items() if k not in servers}
        merged = {**user_only, **servers}
        if merged:
            platform_config["mcp_servers"] = merged
        if cli_specs:
            platform_config["mcp_cli_connectors"] = cli_specs
        else:
            platform_config.pop("mcp_cli_connectors", None)

    # Apply DB user_config as the authoritative source of truth so a rebuild
    # never loses user data even if the volume was fresh/empty. This runs AFTER
    # the volume-merge above, so user edits persisted in Postgres always win.
    if user_config:
        platform_config = _apply_user_config(platform_config, user_config)

    config_content = yaml.safe_dump(platform_config, allow_unicode=True, sort_keys=False).encode("utf-8")
    env_content = _build_hermes_env_file(
        _read_existing_hermes_env_channel_vars(container),
        plan,
    ).encode("utf-8")
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        config_file = tarfile.TarInfo(name="config.yaml")
        config_file.size = len(config_content)
        config_file.mode = 0o644
        config_file.mtime = int(time.time())
        tar.addfile(config_file, io.BytesIO(config_content))

        env_file = tarfile.TarInfo(name=".env")
        env_file.size = len(env_content)
        env_file.mode = 0o600
        env_file.mtime = int(time.time())
        tar.addfile(env_file, io.BytesIO(env_content))

    tar_buffer.seek(0)
    ok = container.put_archive("/opt/data", tar_buffer.read())
    if not ok:
        raise RuntimeError("failed to write Hermes config.yaml/.env into container data volume")
    _repair_hermes_data_ownership(container)
    return existing_config





async def get_container(db: AsyncSession, user_id: str) -> Container | None:
    result = await db.execute(select(Container).where(Container.user_id == user_id))
    return result.scalar_one_or_none()


async def get_container_by_token(db: AsyncSession, token: str) -> Container | None:
    result = await db.execute(select(Container).where(Container.container_token == token))
    return result.scalar_one_or_none()


async def get_user_port_binding(db: AsyncSession, user_id: str) -> UserPortBinding | None:
    result = await db.execute(select(UserPortBinding).where(UserPortBinding.user_id == user_id))
    return result.scalar_one_or_none()


async def upsert_user_port_binding(
    db: AsyncSession,
    user_id: str,
    host_bind_ip: str,
    host_port_browser: int | None,
    host_port_service: int | None,
) -> None:
    stmt = (
        pg_insert(UserPortBinding)
        .values(
            user_id=user_id,
            host_bind_ip=host_bind_ip,
            host_port_browser=host_port_browser,
            host_port_service=host_port_service,
        )
        .on_conflict_do_update(
            index_elements=[UserPortBinding.__table__.c.user_id],
            set_={
                "host_bind_ip": host_bind_ip,
                "host_port_browser": host_port_browser,
                "host_port_service": host_port_service,
            },
        )
    )
    await db.execute(stmt)


def _write_skill_marker(docker_container, user_set, managed_set) -> None:
    """Persist an explicit installed-skill marker (user + managed sets)."""
    payload = json.dumps(
        {"user": sorted(user_set), "managed": sorted(managed_set)},
        ensure_ascii=False,
    ).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=".skillstore-installed.json")
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    buf.seek(0)


def _write_skill_marker(docker_container, user_set, managed_set) -> None:
    """Persist an explicit installed-skill marker (user + managed sets)."""
    payload = json.dumps(
        {"user": sorted(user_set), "managed": sorted(managed_set)},
        ensure_ascii=False,
    ).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=".skillstore-installed.json")
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    buf.seek(0)
    docker_container.put_archive("/opt/data", buf.read())


def _write_empty_skill_marker(docker_container) -> None:
    """Write an empty installed-skill marker into a freshly created container.

    Used when the global "default install all" switch is OFF (and no per-skill
    overrides), so a brand-new user starts with no installs (otherwise
    read_installed_marker falls back to all catalog skills by default).
    """
    _write_skill_marker(docker_container, [], [])


def _host_catalog_skill_names() -> set[str]:
    """Skill names from the baked built-in catalog on the gateway host."""
    try:
        path = os.path.join(
            os.getenv("BUILTIN_SKILLS_DIR", "/app/hermes_skills"), "catalog.json"
        )
        data = json.loads(open(path, encoding="utf-8").read())
        return set(data.get("skills", {}).keys())
    except Exception:
        logger.debug("_host_catalog_skill_names failed", exc_info=True)
        return set()


async def create_container(
    db: AsyncSession,
    user_id: str,
    preset_user_config: dict | None = None,
) -> Container | None:
    """Create a Docker container for a user and record metadata in DB.

    Inserts a DB record first to claim the user_id slot (preventing races),
    then creates the Docker container and updates the record.
    Returns None if another request already claimed the slot.

    ``preset_user_config`` carries the user's durable Hermes config (custom
    providers + model selection) across a recreate so it is never lost when the
    Container row is deleted and re-inserted.
    """
    container_token = secrets.token_urlsafe(32)
    short_id = user_id[:8]
    runtime_backend = _runtime_backend()

    # Resolve the durable user config: prefer an explicit preset (used on
    # recreate to survive the row deletion), else load from DB.
    user_config = (
        preset_user_config
        if preset_user_config is not None
        else await _load_user_config(db, user_id)
    )

    # Insert DB record to claim the unique user_id slot.
    # ON CONFLICT DO NOTHING avoids PostgreSQL ERROR logs on races.
    stmt = (
        pg_insert(Container)
        .values(
            user_id=user_id,
            docker_id="",
            container_token=container_token,
            status="creating",
            internal_host="",
            internal_port=_internal_port(),
            user_config=user_config,
        )
        .on_conflict_do_nothing(index_elements=["user_id"])
        .returning(Container.__table__.c.id)
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        # Another request already claimed this user_id — not an error
        return None

    await db.flush()
    record = await get_container(db, user_id)

    # Now safe to create Docker resources — we hold the DB slot.
    _ensure_network()
    client = _docker()

    data_vol = _data_volume_name(short_id)
    container_name = _container_name(short_id)

    # Remove any stale container with the same name
    try:
        stale = client.containers.get(container_name)
        stale.remove(force=True)
    except DockerNotFound:
        pass

    # Fetch user's SSO token if available (e.g. InfoX-Med)
    user_result = await db.execute(select(User).where(User.id == user_id))
    user_row = user_result.scalar_one_or_none()
    sso_token = user_row.sso_token if user_row else None

    # Load platform-level capability defaults and build the per-container plan
    # so newly built containers honor the admin's "default inject" selections.
    pd_result = await db.execute(select(PlatformCapabilityDefault))
    platform_defaults = {row.capability: row for row in pd_result.scalars().all()}
    uc_result = await db.execute(select(UserCapability).where(UserCapability.user_id == user_id))
    user_caps = {row.capability: row for row in uc_result.scalars().all()}
    container_plan = build_capability_plan(
        settings,
        user_caps=user_caps,
        platform_defaults=platform_defaults,
    )

    container_env = _runtime_environment(container_token, sso_token, container_plan)

    run_kwargs = {
        "image": _runtime_image(),
        "command": _runtime_command(),
        "name": container_name,
        "detach": True,
        "environment": container_env,
        "mounts": _build_runtime_mounts(data_vol, short_id),
        "network": settings.container_network,
        "mem_limit": settings.container_memory_limit,
        "shm_size": settings.container_shm_size,
        "nano_cpus": int(settings.container_cpu_limit * 1e9),
        "pids_limit": settings.container_pids_limit,
        "restart_policy": {"Name": "unless-stopped"},
    }

    if settings.user_container_publish_ports:
        binding = await get_user_port_binding(db, user_id)
        preferred_browser_port = binding.host_port_browser if binding is not None else None
        preferred_service_port = binding.host_port_service if binding is not None else None

        preferred_ports = _runtime_preferred_ports(preferred_browser_port, preferred_service_port)
        preferred_usable = preferred_ports is not None and all(
            not _is_host_port_in_use(client, host_port)
            for _container_port, (_host_ip, host_port) in preferred_ports.items()
            if host_port is not None
        )

        run_kwargs["ports"] = preferred_ports if preferred_usable else _runtime_published_ports()

    try:
        docker_container = client.containers.run(**run_kwargs)
    except DockerAPIError as exc:
        # Preferred ports can race with other creators; fallback to random publish.
        if settings.user_container_publish_ports and "port is already allocated" in str(exc).lower():
            run_kwargs["ports"] = _runtime_published_ports()
            docker_container = client.containers.run(**run_kwargs)
        else:
            await db.rollback()
            raise
    except Exception:
        # Docker creation failed — remove the placeholder DB record
        await db.rollback()
        raise

    # Read container IP on the internal network
    docker_container.reload()
    browser_binding, service_binding = _published_port_bindings(docker_container)
    runtime_metadata = _build_runtime_metadata_markdown(
        user_id=user_id,
        container_name=container_name,
        runtime_backend=runtime_backend,
    )
    _write_runtime_metadata(docker_container, runtime_metadata)
    mcp_servers = await _collect_mcp_servers(db, user_id)
    existing = _write_hermes_runtime_files(
        docker_container, container_plan, mcp_servers,
        db=db, user_id=user_id, user_config=user_config,
    )
    # Safety net: capture any pre-existing volume data into DB so it is never
    # lost on a future rebuild. The primary write path is the models route.
    if user_id:
        await _capture_user_config_to_db(db, user_id, existing)
    try:
        from app.platform_skills import copy_enabled_skills_to_container
        copy_enabled_skills_to_container(docker_container.id)
    except Exception:
        logger.exception("Failed to copy platform skills to container %s", docker_container.id)

    # Honor the global "default install all" switch + per-skill overrides for
    # new users. Precedence: a per-skill override always wins over the master
    # switch. When the master is ON and there are no overrides we intentionally
    # skip writing a marker so read_installed_marker keeps its legacy
    # "absent => all catalog skills installed" behavior (new catalog skills
    # also auto-appear for existing users). Otherwise we compute the explicit
    # initial install set and persist it. Runs for every runtime backend.
    flag = (
        await db.execute(
            select(SystemFlag).where(SystemFlag.key == DEFAULT_INSTALL_FLAG)
        )
    ).scalar_one_or_none()
    master_on = not (flag is not None and flag.value == "false")
    override_row = (
        await db.execute(
            select(SystemFlag).where(SystemFlag.key == DEFAULT_OVERRIDE_FLAG)
        )
    ).scalar_one_or_none()
    overrides: dict[str, bool] = {}
    if override_row is not None and override_row.value:
        try:
            overrides = {k: bool(v) for k, v in json.loads(override_row.value).items()}
        except Exception:
            overrides = {}
    if master_on and not overrides:
        pass  # legacy: no marker => all catalog skills installed by default
    else:
        catalog_names = _host_catalog_skill_names()
        initial = set(catalog_names) if master_on else set()
        for name, on in overrides.items():
            if on:
                initial.add(name)
            else:
                initial.discard(name)
        initial &= catalog_names
        _write_skill_marker(docker_container, initial, [])

    network_settings = docker_container.attrs["NetworkSettings"]["Networks"]
    internal_ip = network_settings.get(settings.container_network, {}).get("IPAddress", "")

    record.docker_id = docker_container.id
    record.status = "running"
    record.internal_host = internal_ip
    await upsert_user_port_binding(
        db=db,
        user_id=user_id,
        host_bind_ip=browser_binding[0] or service_binding[0] or settings.user_container_bind_ip,
        host_port_browser=int(browser_binding[1]) if browser_binding[1] else None,
        host_port_service=int(service_binding[1]) if service_binding[1] else None,
    )
    await db.commit()
    await db.refresh(record)
    return record


async def ensure_running(db: AsyncSession, user_id: str) -> Container:
    """Return a running container for the user, creating or unpausing as needed."""
    import asyncio

    record = await get_container(db, user_id)

    if record is None:
        created = await create_container(db, user_id)
        if created is not None:
            return created
        # Race condition: another request created the container first
        record = await get_container(db, user_id)
        if record is None:
            raise RuntimeError("Failed to create or find container")

    # Another request is still creating the container — wait for it
    if record.status == "creating":
        for _ in range(30):  # wait up to 60s
            await asyncio.sleep(2)
            await db.expire(record)
            record = await get_container(db, user_id)
            if record is None or record.status != "creating":
                break
        if record is None:
            return await create_container(db, user_id)
        if record.status == "creating":
            raise RuntimeError("Container creation timed out")

    client = _docker()

    async def recreate_record(db: AsyncSession, record: Container, docker_container=None) -> Container:
        # Preserve durable per-user data (esp. user_config: custom providers +
        # model selection) across the recreate. Deleting + re-inserting the row
        # would otherwise wipe it and defeat the DB source-of-truth.
        saved_user_config = record.user_config
        if docker_container is not None:
            try:
                docker_container.remove(force=True)
            except DockerNotFound:
                pass
        await db.delete(record)
        await db.commit()
        created = await create_container(db, user_id, preset_user_config=saved_user_config)
        if created is not None:
            return created
        found = await get_container(db, user_id)
        if found is not None:
            if saved_user_config and not (found.user_config or {}):
                found.user_config = saved_user_config
                await db.commit()
            return found
        raise RuntimeError("Failed to recreate container")

    if record.status == "paused":
        try:
            c = client.containers.get(record.docker_id)
            if not _container_matches_runtime(c):
                return await recreate_record(db, record, c)
            c.unpause()
            await db.execute(
                update(Container)
                .where(Container.id == record.id)
                .values(status="running")
            )
            await db.commit()
            record.status = "running"
        except DockerNotFound:
            # Container was removed externally — recreate
            return await recreate_record(db, record)

    elif record.status == "stopped":
        try:
            c = client.containers.get(record.docker_id)
            if not _container_matches_runtime(c):
                return await recreate_record(db, record, c)
            c.start()
            c.reload()
            # Sync internal IP after start
            nets = c.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in nets.values():
                current_ip = net_info.get("IPAddress", "")
                if current_ip:
                    record.internal_host = current_ip
                    break
            await db.execute(
                update(Container)
                .where(Container.id == record.id)
                .values(status="running", internal_host=record.internal_host)
            )
            await db.commit()
            record.status = "running"
        except DockerNotFound:
            # Container was removed externally — recreate
            return await recreate_record(db, record)

    elif record.status == "archived":
        # Recreate from persisted data volumes
        return await recreate_record(db, record)

    elif record.status == "running":
        # Verify it's actually running
        try:
            c = client.containers.get(record.docker_id)
            if not _container_matches_runtime(c):
                return await recreate_record(db, record, c)
            if c.status != "running":
                c.start()
                c.reload()
            # Sync internal IP — it may change after container restart
            nets = c.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_info in nets.values():
                current_ip = net_info.get("IPAddress", "")
                if current_ip and current_ip != record.internal_host:
                    record.internal_host = current_ip
                    await db.execute(
                        update(Container)
                        .where(Container.id == record.id)
                        .values(internal_host=current_ip)
                    )
                    await db.commit()
                break
        except DockerNotFound:
            return await recreate_record(db, record)

    return record


async def pause_container(db: AsyncSession, user_id: str) -> bool:
    """Pause a user's container to save resources."""
    record = await get_container(db, user_id)
    if record is None or record.status != "running":
        return False

    client = _docker()
    try:
        c = client.containers.get(record.docker_id)
        c.pause()
        await db.execute(
            update(Container).where(Container.id == record.id).values(status="paused")
        )
        await db.commit()
        return True
    except DockerNotFound:
        return False


async def resume_container(db: AsyncSession, user_id: str) -> bool:
    """Resume a paused or stopped container to running state."""
    record = await get_container(db, user_id)
    if record is None:
        return False

    if record.status == "running":
        return True  # Already running

    client = _docker()
    try:
        c = client.containers.get(record.docker_id)

        if record.status == "paused":
            c.unpause()
        elif record.status == "stopped":
            c.start()

        # Reload to get latest status
        c.reload()
        await db.execute(
            update(Container).where(Container.id == record.id).values(status="running")
        )
        await db.commit()
        return True
    except DockerNotFound:
        return False


async def destroy_container(db: AsyncSession, user_id: str) -> bool:
    """Stop and remove a user's container (data volumes are preserved)."""
    record = await get_container(db, user_id)
    if record is None:
        return False

    client = _docker()
    try:
        c = client.containers.get(record.docker_id)
        c.stop(timeout=10)
        c.remove()
    except DockerNotFound:
        pass

    await db.delete(record)
    await db.commit()
    return True


async def apply_container_capabilities(db: AsyncSession, user_id: str) -> dict:
    """Hot-patch a running container's capability config without a rebuild.

    Recomputes the user's capability plan from DB grants/defaults, reads any
    user-provided capability key from the existing container ``.env`` (so it
    satisfies conditional mounting and is not overwritten by the platform pool
    key), rewrites ``.env``/``config.yaml`` via ``_write_hermes_runtime_files``,
    then restarts the container so the new config takes effect.

    Requires a *running* container: the preserve logic reads existing files via
    ``exec_run``, which only works while the container is running. Applying to a
    stopped/paused container would silently lose user-set keys, so it is refused
    instead.

    Returns ``{"applied": True, "toolsets": [...]}`` on success, or
    ``{"applied": False, "reason": ...}`` (``no_container`` |
    ``container_missing`` | ``not_running``) when it cannot proceed safely.
    """
    record = await get_container(db, user_id)
    if record is None or not record.docker_id:
        return {"applied": False, "reason": "no_container"}

    try:
        docker_container = get_docker_container(record.docker_id)
    except DockerNotFound:
        return {"applied": False, "reason": "container_missing"}

    docker_container.reload()
    if getattr(docker_container, "status", "") != "running":
        return {"applied": False, "reason": "not_running"}

    pd_result = await db.execute(select(PlatformCapabilityDefault))
    platform_defaults = {row.capability: row for row in pd_result.scalars().all()}
    uc_result = await db.execute(
        select(UserCapability).where(UserCapability.user_id == user_id)
    )
    user_caps = {row.capability: row for row in uc_result.scalars().all()}

    # User-provided keys live inside the container; read them so they satisfy
    # conditional mounting and survive the rewrite (user key > platform key).
    user_keys = _read_existing_capability_keys(docker_container)

    plan = build_capability_plan(
        settings,
        user_caps=user_caps,
        platform_defaults=platform_defaults,
        user_keys=user_keys,
    )

    user_config = await _load_user_config(db, user_id)
    existing = _write_hermes_runtime_files(
        docker_container, plan, await _collect_mcp_servers(db, user_id),
        db=db, user_id=user_id, user_config=user_config,
    )
    if user_id:
        await _capture_user_config_to_db(db, user_id, existing)
    docker_container.restart()
    return {"applied": True, "toolsets": plan.enabled_toolsets}
