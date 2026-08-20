"""User-facing MCP connector API.

Lists the platform connector catalog with per-user enable state, and lets a
user enable/disable a platform connector (writing the resolved mcp_servers into
their container config.yaml). Pure-custom servers are managed inside the
container via /api/comworker/mcp/servers and are preserved on rebuild.
"""
from __future__ import annotations

import asyncio
import httpx
import json
import logging
import os as _os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.container.manager import (
    _apply_mcp_servers_to_container,
    _missing_required_config_fields,
    ensure_running,
    get_docker_container,
)
from app.db.engine import get_db
from app.db.models import McpConnector, User, UserMcpConnector
from app.hermes_client import HermesClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])

import re as _re

_REQUIRED_KEY_RE = _re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _extract_required_keys(config_json: dict | None) -> list[str]:
    """Return the ``${KEY}`` placeholder names in a connector's config.

    Used by the api_key strategy so the client can render a key-entry form.
    Only the placeholder *names* are exposed (never secret values).
    """
    cfg = config_json or {}
    keys: set[str] = set()
    for v in (cfg.get("url"),):
        if isinstance(v, str):
            keys.update(_REQUIRED_KEY_RE.findall(v))
    for section in ("headers", "env"):
        sect = cfg.get(section)
        if isinstance(sect, dict):
            for v in sect.values():
                if isinstance(v, str):
                    keys.update(_REQUIRED_KEY_RE.findall(v))
    return sorted(keys)


async def _get_connector_or_404(db: AsyncSession, connector_id: str) -> McpConnector:
    c = await db.get(McpConnector, connector_id)
    if c is None:
        raise HTTPException(status_code=404, detail="connector not found")
    return c


async def _get_or_create_user_connector(
    db: AsyncSession, user: User, connector: McpConnector
) -> UserMcpConnector:
    row = (
        await db.execute(
            select(UserMcpConnector).where(
                UserMcpConnector.user_id == user.id,
                UserMcpConnector.name == connector.name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = UserMcpConnector(
            user_id=user.id,
            connector_id=connector.id,
            name=connector.name,
            enabled=True,
        )
        db.add(row)
    return row


async def _apply_to_user_container(db: AsyncSession, user_id: str) -> None:
    """Write the resolved mcp_servers into the user's container and restart it.

    Restarting is required so the hermes runtime (which reads mcp_servers at
    startup) actually loads newly enabled HTTP/SSE connectors as callable
    tools. CLI connectors only ship their install/auth spec, which is also
    picked up on restart.
    """
    try:
        container = await ensure_running(db, user_id)
        await _apply_mcp_servers_to_container(container, db, user_id)
        try:
            docker_container = get_docker_container(container.docker_id)
            docker_container.reload()
            if getattr(docker_container, "status", "") == "running":
                await asyncio.to_thread(docker_container.restart)
                # Wait until the runtime's HTTP server is actually accepting
                # connections, not merely until the container process is
                # "running". The hermes app can take a few seconds to bind its
                # port after the container transitions to running, and a follow-up
                # call (e.g. CLI install/auth right after enabling) would
                # otherwise land in that window and fail with "starting up".
                await _wait_for_runtime_ready(container)
        except Exception:
            logger.exception("failed to restart container for user %s after connector change", user_id)
    except Exception:
        logger.exception("failed to apply mcp_servers for user %s", user_id)


async def _wait_for_runtime_ready(container) -> None:
    """Best-effort: block until the user's runtime HTTP server is reachable.

    Called after a connector-triggered container restart. We poll the runtime's
    `/v1/models` endpoint via HermesClient (the same probe used by
    dedicated_hermes), which internally retries ConnectError. Falls through
    after a timeout so a slow/unhealthy runtime never blocks the connector
    enable/disable call forever.
    """
    host = getattr(container, "internal_host", None)
    port = getattr(container, "internal_port", None)
    user_id = getattr(container, "user_id", "?")
    if not host or not port:
        logger.warning("cannot wait for runtime: missing internal_host/port for user %s", user_id)
        return
    base_url = f"http://{host}:{port}"
    api_key = getattr(settings, "dedicated_hermes_api_key", "") or ""
    logger.info("waiting for runtime ready user=%s base_url=%s", user_id, base_url)
    deadline = asyncio.get_event_loop().time() + 150.0
    attempts = 0
    while asyncio.get_event_loop().time() < deadline:
        client: HermesClient | None = None
        attempts += 1
        try:
            client = HermesClient(
                base_url=base_url,
                api_key=api_key,
                timeout=3.0,
                connect_retries=2,
                retry_delay_seconds=0.5,
            )
            await client.get_models()
            logger.info("runtime ready after %s attempts user=%s base_url=%s", attempts, user_id, base_url)
            return  # runtime is up and serving the API
        except Exception as exc:
            # HermesClient turns ConnectError into HTTPException(503) once
            # retries are exhausted; any failure means "not ready yet".
            logger.info(
                "runtime not ready yet (attempt %s) user=%s base_url=%s: %s: %s",
                attempts,
                user_id,
                base_url,
                type(exc).__name__,
                exc,
            )
        finally:
            if client is not None:
                await client.aclose()
        await asyncio.sleep(0.5)
    logger.warning(
        "runtime for user %s did not become reachable within 150s after restart (base_url=%s)",
        user_id,
        base_url,
    )


@router.get("")
async def list_user_connectors(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(McpConnector).where(McpConnector.status == "active"))
    connectors = result.scalars().all()
    ur = await db.execute(
        select(UserMcpConnector).where(UserMcpConnector.user_id == user.id)
    )
    overrides = {r.name: r for r in ur.scalars().all()}

    items = []
    for c in connectors:
        ov = overrides.get(c.name)
        if c.is_mandatory:
            effective, locked = True, True
        elif ov is not None:
            effective, locked = ov.enabled, False
        elif c.is_default:
            effective, locked = True, False
        else:
            effective, locked = False, False
        items.append(
            {
                "id": c.id,
                "name": c.name,
                "display_name": c.display_name,
                "description": c.description,
                "examples": c.examples,
                "icon": c.icon,
                "transport": c.transport,
                "credential_strategy": c.credential_strategy,
                "needs_auth": c.credential_strategy in ("oauth", "cli"),
                "required_keys": _extract_required_keys(c.config_json),
                "enabled": effective,
                "locked": locked,
            }
        )
    return {"connectors": items}


@router.put("/{connector_id}/enable")
async def enable_connector(
    connector_id: str,
    payload: dict | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = await _get_connector_or_404(db, connector_id)
    # CLI connectors are installed/authorized in-container; their "config_json"
    # is empty because the real spec lives in "cli_config_json". Skip the
    # transport-required-field check for them (it would wrongly demand "command").
    if c.credential_strategy != "cli":
        missing = _missing_required_config_fields(c.transport, c.config_json)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"连接器「{c.display_name}」尚未配置接入地址（缺少 {missing}），"
                    "请联系管理员在管理端填写连接器的服务端地址后再启用。"
                ),
            )
    if c.credential_strategy == "api_key" and not c.shared_credential:
        creds = (payload or {}).get("credentials") or {}
        if not creds:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"连接器「{c.display_name}」需要 API Key 才能启用，"
                    "请在客户端填写对应的密钥后再启用。"
                ),
            )
    row = await _get_or_create_user_connector(db, user, c)
    row.enabled = True
    if c.credential_strategy == "api_key" and (payload or {}).get("credentials"):
        row.credential_json = (payload or {}).get("credentials")
    await db.commit()
    await _apply_to_user_container(db, user.id)
    return {"ok": True, "enabled": True}


@router.put("/{connector_id}/disable")
async def disable_connector(
    connector_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = await _get_connector_or_404(db, connector_id)
    if c.is_mandatory:
        raise HTTPException(status_code=400, detail="mandatory connector cannot be disabled")
    row = await _get_or_create_user_connector(db, user, c)
    row.enabled = False
    await db.commit()
    await _apply_to_user_container(db, user.id)
    return {"ok": True, "enabled": False}


# ─── Built-in connector catalog (data-driven, mirrors WorkBuddy packages) ───
# The catalog lives in ``connectors-catalog/<name>/`` as
# ``connector-meta.json`` (+ ``mcp.json`` or ``cli.json``). Adding a connector
# is dropping a directory — no code change. ``seed_builtin_connectors`` scans
# that directory and upserts rows into ``mcp_connectors``.
_CATALOG_DIR = _os.environ.get(
    "CONNECTOR_CATALOG_DIR",
    str(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "connectors-catalog")),
)

# WorkBuddy / hermes transport spelling unification.
_TRANSPORT_ALIASES = {
    "streamable-http": "streamable_http",
    "streamablehttp": "streamable_http",
    "streamable_http": "streamable_http",
    "http": "streamable_http",
    "sse": "sse",
    "stdio": "stdio",
}


def _normalize_transport(raw: str | None) -> str:
    if not raw:
        return "streamable_http"
    return _TRANSPORT_ALIASES.get(raw.strip().lower(), "streamable_http")


def _load_catalog_specs() -> list[dict]:
    """Read every connector directory under the catalog dir into DB-ready specs."""
    root = _os.path.join(_CATALOG_DIR)
    if not _os.path.isdir(root):
        logger.warning("connector catalog dir not found: %s", root)
        return []
    specs: list[dict] = []
    for name in sorted(_os.listdir(root)):
        d = _os.path.join(root, name)
        meta_path = _os.path.join(d, "connector-meta.json")
        if not _os.path.isfile(meta_path):
            continue
        try:
            meta = json.loads(_read_file(meta_path))
        except (OSError, ValueError) as exc:
            logger.warning("skip %s: bad connector-meta.json (%s)", name, exc)
            continue
        strategy = meta.get("auth", "none")
        spec: dict = {
            "name": meta.get("source") or meta.get("name") or name,
            "display_name": meta.get("display_name") or meta.get("name") or name,
            "description": meta.get("description_zh") or meta.get("description_en") or "",
            "examples": "\n".join(meta.get("examples_zh") or []),
            "credential_strategy": strategy,
            "config_json": {},
            "cli_config_json": {},
            "transport": "streamable_http",
        }
        mcp_path = _os.path.join(d, "mcp.json")
        cli_path = _os.path.join(d, "cli.json")
        if _os.path.isfile(mcp_path):
            try:
                mcp = json.loads(_read_file(mcp_path))
                servers = mcp.get("mcpServers", {})
                entry = servers.get(spec["name"]) or next(iter(servers.values()), {})
            except (OSError, ValueError) as exc:
                logger.warning("skip %s: bad mcp.json (%s)", name, exc)
                continue
            transport = _normalize_transport(entry.get("type"))
            cfg = {k: v for k, v in entry.items() if k != "type"}
            cfg["transport"] = transport
            spec["transport"] = transport
            spec["config_json"] = cfg
        elif _os.path.isfile(cli_path):
            try:
                spec["cli_config_json"] = json.loads(_read_file(cli_path))
            except (OSError, ValueError) as exc:
                logger.warning("skip %s: bad cli.json (%s)", name, exc)
                continue
            spec["transport"] = "stdio"
        specs.append(spec)
    return specs


async def seed_builtin_connectors(db: AsyncSession) -> int:
    """Idempotently upsert the connector catalog from ``connectors-catalog/``.

    New connectors are inserted; existing ones keep their admin-edited
    ``config_json`` / ``shared_credential`` / flags but have their display
    metadata refreshed from the package. Returns number of rows added.
    """
    specs = _load_catalog_specs()
    added = 0
    for spec in specs:
        exists = (
            await db.execute(select(McpConnector).where(McpConnector.name == spec["name"]))
        ).scalar_one_or_none()
        if exists is not None:
            # Refresh catalog-derived display fields; preserve admin config.
            exists.display_name = spec["display_name"]
            exists.description = spec["description"]
            exists.examples = spec["examples"]
            exists.credential_strategy = spec["credential_strategy"]
            exists.transport = spec["transport"]
            exists.cli_config_json = spec["cli_config_json"]
            if not exists.config_json:
                exists.config_json = spec["config_json"]
            continue
        obj = McpConnector(
            name=spec["name"],
            display_name=spec["display_name"],
            description=spec["description"],
            examples=spec["examples"],
            transport=spec["transport"],
            config_json=spec["config_json"],
            cli_config_json=spec["cli_config_json"],
            credential_strategy=spec["credential_strategy"],
            is_builtin=True,
            is_default=False,
            is_mandatory=False,
            status="active",
        )
        db.add(obj)
        added += 1
    # Commit even when only refreshing existing rows: the refresh branch mutates
    # credential_strategy / transport / cli_config_json / examples on existing
    # McpConnector rows, and those changes must be persisted (not just inserts).
    await db.commit()
    return added


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()
