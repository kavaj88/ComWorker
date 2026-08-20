"""MCP OAuth 2.1 web relay for multi-tenant, browser-driven containers.

WorkBuddy's desktop app opens a browser and captures the OAuth redirect via a
custom ``workbuddy://`` scheme. In our web / multi-tenant model there is no
desktop scheme, so this module replicates the flow with a browser-driven
authorization-code + PKCE exchange whose ``redirect_uri`` points at the web
client, which forwards the grant to this relay.

Tokens are persisted through :class:`tools.mcp_oauth.HermesTokenStorage`, which
is exactly what hermes's MCP client reads on connect — so once a user completes
this flow, hermes connects to the server using the cached token with **no
engine auth rewiring**.

Endpoints (mounted at ``/api/mcp`` by web_server):
  POST /api/mcp/{name}/oauth/start   body {redirect_uri} -> {auth_url, state}
  GET  /api/mcp/{name}/oauth/callback?code&state  (provider -> client -> here)
  GET  /api/mcp/{name}/oauth/poll?state -> {status, error?}

CLI connectors:
  POST /api/mcp/{name}/cli/install   run the package's init/install command
  POST /api/mcp/{name}/cli/auth      run the auth command, capture output (QR/URL)
  POST /api/mcp/{name}/cli/status    report install/auth status
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/api/mcp")

# session_id (== state) -> session dict. Single-process (hermes web_server runs
# one worker); good enough for the relay. Sessions self-expire via _EXPIRY.
_SESSIONS: dict[str, dict] = {}
_EXPIRY = 600.0

# ── config + storage helpers ────────────────────────────────────────────────


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _server_entry(name: str) -> dict | None:
    """Return the ``mcp_servers.<name>`` config dict, or None if absent."""
    cfg_path = _hermes_home() / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    return (cfg.get("mcp_servers") or {}).get(name)


def _token_storage(name: str):
    from tools.mcp_oauth import HermesTokenStorage

    return HermesTokenStorage(name)


# ── security guards (adversarial-review fixes) ─────────────────────────────
# P0-1 (TLS), P1-2 (redirect_uri pinning), P1-3 (SSRF), P1-4 (shared-mode
# isolation collapse). See docs/connector-adversarial-review.md.

def _dev_shared_mode() -> bool:
    """True when this process serves ALL users (shared/dev runtime).

    The relay's in-memory session table and on-disk token store assume one
    user per process. In shared mode OAuth/CLI connectors would cross-talk
    between users, so they are refused outright (P1-4).
    """
    return bool(os.environ.get("PLATFORM_DEV_COMWORKER_URL"))


def _tls_verify() -> bool:
    """Default: verify TLS certificates against the system CA bundle.

    Opt-out ONLY for trusted internal deployments that use a private CA, via
    ``MCP_OAUTH_RELAY_INSECURE_TLS=1``. Never on by default — disabling TLS
    verification lets a network MITM steal OAuth tokens / client_secret (P0-1).
    """
    return os.environ.get("MCP_OAUTH_RELAY_INSECURE_TLS", "0") not in ("1", "true", "yes")


def _allowed_origins() -> set[str]:
    raw = os.environ.get("MCP_RELAY_ALLOWED_ORIGINS", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _is_safe_url(target: str) -> bool:
    """Reject URLs that could enable SSRF against internal/metadata endpoints.

    Allows https everywhere; allows http only on loopback. Blocks private,
    loopback, link-local and reserved IP literals and ``.internal``/``.local``
    hosts (unless explicitly dev-relaxed). Plain hostnames are allowed (they
    are DNS-resolved at request time, so no IP-literal SSRF).
    """
    if not target:
        return False
    p = urllib.parse.urlparse(target)
    if p.scheme not in ("https", "http"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if host.endswith(".internal") or host.endswith(".local"):
        return False
    # http is permitted only on loopback. OAuth/metadata endpoints are
    # https-only in practice; non-loopback http is both unencrypted (MITM /
    # P0-1 surface) and an SSRF vector, so we refuse it here. Internal http
    # providers are accommodated via MCP_OAUTH_RELAY_INSECURE_TLS only when
    # the target is loopback.
    if p.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True  # loopback is safe for both http and https
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # hostname — allowed
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False
    return True


def _require_safe_url(target: str) -> None:
    if not _is_safe_url(target):
        raise HTTPException(status_code=400, detail=f"refusing to contact disallowed URL: {target}")


def _validate_redirect_uri(redirect_uri: str, name: str) -> str:
    """P1-2: pin the callback path and, when configured, the origin.

    Unconditionally requires an exact callback path of
    ``/api/comworker/mcp/<name>/oauth/callback`` and https for non-loopback
    origins (loopback http is permitted for local dev clients). If
    ``MCP_RELAY_ALLOWED_ORIGINS`` is set, the origin must be a member.
    """
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri is required")
    p = urllib.parse.urlparse(redirect_uri)
    if p.scheme not in ("https", "http"):
        raise HTTPException(status_code=400, detail="redirect_uri must use http(s)")
    host = (p.hostname or "").lower()
    is_loopback = host in ("localhost", "127.0.0.1", "::1")
    if p.scheme == "http" and not is_loopback:
        raise HTTPException(status_code=400, detail="redirect_uri must use https unless on loopback")
    expected_path = f"/api/comworker/mcp/{name}/oauth/callback"
    if p.path != expected_path:
        raise HTTPException(status_code=400, detail="redirect_uri path must be the connector callback path")
    allowed = _allowed_origins()
    if allowed:
        origin = p._replace(path="", query="", fragment="").geturl().rstrip("/")
        if origin not in allowed:
            raise HTTPException(status_code=400, detail="redirect_uri origin not allowed")
    return redirect_uri


# ── PKCE + discovery ────────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _parse_token_response(r: Any) -> dict:
    """Parse a token endpoint response as JSON or form-urlencoded.

    QQ Connect (graph.qq.com) returns ``application/x-www-form-urlencoded``
    (``access_token=..&expires_in=..``) rather than JSON, so fall back to that.
    """
    ctype = (getattr(r, "headers", {}) or {}).get("content-type", "") or ""
    if "application/json" in ctype:
        return r.json()
    try:
        return dict(urllib.parse.parse_qsl(r.text))
    except Exception:
        try:
            return r.json()
        except Exception:
            return {}


def _resolve_env_val(v: Any) -> Any:
    """Resolve a ``${VAR}`` placeholder to its environment value.

    An unset/empty variable resolves to ``None`` so that, e.g., an OAuth
    ``client_id`` left as ``${PROVIDER_CLIENT_ID}`` cleanly falls through to
    dynamic client registration instead of being sent literally to the
    provider.
    """
    if not isinstance(v, str):
        return v
    m = re.match(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$", v.strip())
    if m:
        return os.environ.get(m.group(1)) or None
    return v


def _with_scope(meta: dict) -> dict:
    """Ensure a usable ``scope`` is present (first of ``scopes_supported``)."""
    if not meta.get("scope") and meta.get("scopes_supported"):
        meta = dict(meta)
        meta["scope"] = " ".join(meta["scopes_supported"][:3])
    return meta


async def _discover(server_url: str, oauth_block: dict | None) -> dict:
    """Resolve authorization/token/registration endpoints.

    Resolution order:
      1. explicit ``oauth`` block in the server config (admin-supplied) — wins.
      2. RFC 8414 authorization-server metadata at the server origin.
      3. RFC 9728 protected-resource metadata at the origin, which points to
         the authorization server(s) whose metadata we then fetch.
      4. convention fallback: ``/authorize`` + ``/token`` alongside the MCP path.
    """
    if oauth_block and oauth_block.get("authorization_endpoint") and oauth_block.get("token_endpoint"):
        return _with_scope({**oauth_block})

    origin = urllib.parse.urlparse(server_url)._replace(path="", query="", fragment="").geturl()
    base = urllib.parse.urlparse(server_url)._replace(path="").geturl()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, verify=_tls_verify()) as client:
        # (2) RFC 8414 authorization-server metadata at the origin.
        try:
            r = await client.get(f"{origin}/.well-known/oauth-authorization-server")
            if r.status_code == 200:
                return _with_scope({**r.json(), **(oauth_block or {})})
        except Exception:
            pass
        # (3) RFC 9728 protected-resource metadata -> authorization_servers[0].
        try:
            pr = await client.get(f"{origin}/.well-known/oauth-protected-resource")
            if pr.status_code == 200:
                prm = pr.json()
                for auth_server in prm.get("authorization_servers") or []:
                    try:
                        r2 = await client.get(f"{auth_server}/.well-known/oauth-authorization-server")
                        if r2.status_code == 200:
                            merged = {**r2.json(), **(oauth_block or {})}
                            if prm.get("scopes_supported") and not merged.get("scope"):
                                merged["scope"] = " ".join(prm["scopes_supported"][:3])
                            return _with_scope(merged)
                    except Exception:
                        continue
        except Exception:
            pass
    # (4) convention fallback.
    return _with_scope({
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        **(oauth_block or {}),
    })


async def _ensure_client(name: str, meta: dict, redirect_uri: str) -> tuple[str, str | None]:
    """Return (client_id, client_secret) using DCR when no client_id is set."""
    client_id = _resolve_env_val(meta.get("client_id"))
    client_secret = _resolve_env_val(meta.get("client_secret"))
    if client_id:
        return client_id, client_secret
    reg = meta.get("registration_endpoint")
    if not reg:
        # _require_safe_url here guards the discovery-origin token endpoint too.
        _require_safe_url(meta.get("token_endpoint", ""))
        raise HTTPException(
            status_code=400,
            detail=f"Connector '{name}' has no pre-registered client_id and the "
            "provider exposes no dynamic registration endpoint. Register a "
            "client_id (and redirect_uri) with the provider, then set it in the "
            "connector's oauth block.",
        )
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, verify=_tls_verify()) as client:
        _require_safe_url(reg)
        r = await client.post(
            reg,
            json={
                "client_name": "ComWorker",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": meta.get("scope", ""),
            },
            headers={"Content-Type": "application/json"},
        )
        if r.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Dynamic client registration failed ({r.status_code}): {r.text[:200]}",
            )
        data = r.json()
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
    # Persist for reuse.
    storage = _token_storage(name)
    from tools.mcp_oauth import OAuthClientInformationFull

    try:
        info = OAuthClientInformationFull.model_validate(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uris": [redirect_uri],
            }
        )
        await storage.set_client_info(info)
    except Exception:
        pass
    return client_id, client_secret


# ── OAuth endpoints ─────────────────────────────────────────────────────────


@router.post("/{name}/oauth/start")
async def oauth_start(name: str, body: dict | None = None):
    entry = _server_entry(name)
    if not entry or entry.get("auth") != "oauth":
        raise HTTPException(status_code=404, detail=f"no OAuth MCP server named '{name}'")
    if _dev_shared_mode():
        raise HTTPException(
            status_code=403,
            detail="OAuth connectors are disabled in shared/dev runtime mode",
        )
    redirect_uri = _validate_redirect_uri((body or {}).get("redirect_uri"), name)

    # SSRF guard (P1-3): never contact internal/metadata endpoints on behalf
    # of an admin-configured server URL.
    _require_safe_url(entry.get("url", ""))
    meta = await _discover(entry.get("url", ""), entry.get("oauth"))
    if not meta.get("authorization_endpoint") or not meta.get("token_endpoint"):
        raise HTTPException(
            status_code=502,
            detail="Could not resolve the provider's OAuth endpoints (authorization/token).",
        )
    client_id, client_secret = await _ensure_client(name, meta, redirect_uri)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    scope = meta.get("scope") or entry.get("oauth", {}).get("scope")
    if scope:
        params["scope"] = scope
    # The authorization_endpoint may already carry a query string (e.g.
    # Tencent Docs uses ".../open-claw.html?authType=2"). Appending another
    # "?" would glue every OAuth param into the first query value and the
    # provider would never see client_id/redirect_uri/state -> the auth page
    # appears to do nothing. Use "&" when a "?" is already present.
    base = meta["authorization_endpoint"]
    sep = "&" if "?" in base else "?"
    auth_url = base + sep + urllib.parse.urlencode(params)

    _SESSIONS[state] = {
        "name": name,
        "redirect_uri": redirect_uri,
        "token_endpoint": meta["token_endpoint"],
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
        "scope": scope,
        "status": "pending",
        "error": None,
        "created_at": time.time(),
    }
    _sweep()
    return {"auth_url": auth_url, "state": state}


@router.get("/{name}/oauth/callback")
async def oauth_callback(name: str, code: str | None = None, state: str | None = None, error: str | None = None):
    sess = _SESSIONS.get(state or "") if state else None
    if sess is None or sess.get("name") != name:
        raise HTTPException(status_code=400, detail="unknown or expired OAuth session")
    if error:
        sess["status"] = "error"
        sess["error"] = error
        return HTMLResponse(
            "<html><head><meta charset='utf-8'></head><body><h2>授权失败</h2><p>%s</p>"
            "<p>你可以关闭此页面并返回客户端。</p></body></html>" % error
        )
    if not code:
        sess["status"] = "error"
        sess["error"] = "missing authorization code"
        return HTMLResponse(
            "<html><head><meta charset='utf-8'></head><body><h2>授权失败</h2><p>缺少授权码。</p></body></html>"
        )
    try:
        await _exchange_and_store(name, sess, code)
        sess["status"] = "done"
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        sess["status"] = "error"
        sess["error"] = str(exc)
        return HTMLResponse(
            "<html><head><meta charset='utf-8'></head><body><h2>授权失败</h2><p>%s</p></body></html>" % str(exc)
        )
    return HTMLResponse(
        "<html><head><meta charset='utf-8'></head><body><h2>授权成功</h2>"
        "<p>连接器已授权，你可以关闭此页面并返回客户端。</p></body></html>"
    )


async def _exchange_and_store(name: str, sess: dict, code: str) -> None:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": sess["redirect_uri"],
        "client_id": sess["client_id"],
        "code_verifier": sess["code_verifier"],
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if sess.get("client_secret"):
        # Confidential client: send secret in the body (common for DCR-issued).
        data["client_secret"] = sess["client_secret"]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=_tls_verify()) as client:
        r = await client.post(sess["token_endpoint"], data=data, headers=headers)
        if r.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"token exchange failed ({r.status_code}): {r.text[:200]}",
            )
        token_json = _parse_token_response(r)
    from tools.mcp_oauth import OAuthToken

    try:
        token = OAuthToken.model_validate(token_json)
    except Exception:
        # Some providers return extra fields; pad with required-ish defaults.
        token_json.setdefault("access_token", token_json.get("access_token"))
        token = OAuthToken.model_validate(token_json)
    await _token_storage(name).set_tokens(token)


@router.get("/{name}/oauth/poll")
async def oauth_poll(name: str, state: str):
    sess = _SESSIONS.get(state)
    if sess is None or sess.get("name") != name:
        raise HTTPException(status_code=404, detail="unknown or expired OAuth session")
    return {"status": sess["status"], "error": sess.get("error")}


def _sweep() -> None:
    now = time.time()
    expired = [k for k, v in _SESSIONS.items() if now - v.get("created_at", 0) > _EXPIRY]
    for k in expired:
        _SESSIONS.pop(k, None)


# ── CLI connector endpoints (install + interactive auth) ────────────────────


def _cli_entry(name: str) -> dict | None:
    """Read the cli_config_json for ``name`` from config.yaml (platform-set)."""
    cfg_path = _hermes_home() / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    cli = (cfg.get("mcp_cli_connectors") or {}).get(name)
    return cli


def _platform() -> str:
    return os.environ.get("OSTYPE", "linux")


def _cli_env() -> dict:
    """Build the env for CLI connector commands.

    Mirrors the logic in ``_run``: redirect npm/pip global installs to a
    user-writable prefix so the non-root runtime user can install/run the CLI.
    """
    env = os.environ.copy()
    _home = env.get("HOME") or "/opt/data"
    _npm_prefix = os.path.join(_home, ".local")
    env["npm_config_prefix"] = _npm_prefix
    _bin_dir = os.path.join(_npm_prefix, "bin")
    _cur_path = env.get("PATH", "")
    if _bin_dir not in _cur_path.split(":"):
        env["PATH"] = f"{_bin_dir}:{_cur_path}"
    env["PYTHONUSERBASE"] = os.path.join(_home, ".local")
    return env


def _extract_cli_commands(field: object) -> list[str]:
    """Extract a flat list of shell commands from a CLI spec field.

    Supports both the flat platform-keyed form used by the connector catalog
    (``{"linux": "cmd", "darwin": "cmd"}`` — used by ``init``/``status``/``auth``)
    and the multi-step nested form (``[{"command": {"linux": "cmd"}}]``).
    The previous implementation only handled the nested form for ``auth``, so a
    flat ``auth`` dict was silently skipped and the connector reported a false
    "authorized" success.
    """
    if not field:
        return []
    plat = _platform()
    if isinstance(field, list):
        cmds: list[str] = []
        for step in field:
            if isinstance(step, dict):
                cmd = (step.get("command") or {}).get(plat) or (step.get("command") or {}).get(
                    "linux"
                )
                if cmd:
                    cmds.append(cmd)
        return cmds
    if isinstance(field, dict):
        cmd = field.get(plat) or field.get("linux")
        return [cmd] if cmd else []
    if isinstance(field, str):
        return [field]
    return []


async def _drain_auth_proc(proc: "asyncio.subprocess.Process") -> None:
    """Background drain of a device-code auth process.

    After we surface the authorize URL to the client, the CLI keeps running
    (polling for the browser step to complete). We drain its output and wait so
    the process does not leak and its exit is logged.
    """
    try:
        if proc.stdout is not None:
            async for _line in proc.stdout:
                pass
    except Exception:
        pass
    try:
        await proc.wait()
    except Exception:
        pass


@router.post("/{name}/cli/install")
async def cli_install(name: str, request: Request):
    if _dev_shared_mode():
        raise HTTPException(
            status_code=403,
            detail="CLI connectors are disabled in shared/dev runtime mode",
        )
    cli = _cli_entry(name)
    if not cli:
        raise HTTPException(status_code=404, detail=f"no CLI connector '{name}'")
    cmds = _extract_cli_commands(cli.get("init"))
    if not cmds:
        raise HTTPException(status_code=400, detail="no install command for this platform")
    out, rc = await _run(cmds[0], timeout=300)
    if rc not in (0, None):
        raise HTTPException(
            status_code=400,
            detail=f"CLI 安装失败（退出码 {rc}）：\n{out[-1500:]}",
        )
    return {"ok": True, "output": out}


@router.post("/{name}/cli/auth")
async def cli_auth(name: str, request: Request):
    if _dev_shared_mode():
        raise HTTPException(
            status_code=403,
            detail="CLI connectors are disabled in shared/dev runtime mode",
        )
    cli = _cli_entry(name)
    if not cli:
        raise HTTPException(status_code=404, detail=f"no CLI connector '{name}'")
    cmds = _extract_cli_commands(cli.get("auth"))
    if not cmds:
        raise HTTPException(
            status_code=400, detail="no auth command for this platform"
        )
    # Device-code CLIs (tmeet, lark, ...) print an authorize URL, then BLOCK
    # waiting for the user to complete the browser step. We stream stdout,
    # surface the URL to the client immediately, and let the process keep
    # running in the background so the device-code exchange can finish once the
    # user authorizes. (The old code buffered the whole output via
    # communicate() and — due to a schema mismatch — actually skipped the
    # command entirely, so the connector reported a false "authorized" success.)
    cmd = cmds[0]
    env = _cli_env()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"CLI 授权启动失败：{exc}")
    auth_url: str | None = None
    domain = (cli.get("authUrlDomain") or "").strip()
    captured: list[str] = []
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=8)
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            captured.append(text)
            if auth_url is None:
                m = re.search(r"https?://[^\s'\"]+", text)
                if m and (not domain or domain in m.group(0)):
                    auth_url = m.group(0)
            if proc.returncode is not None:
                break
    except asyncio.TimeoutError:
        # No new output for a while. If the process is still running it is
        # waiting for the user to authorize in the browser — keep it alive.
        pass
    if auth_url is not None and proc.returncode is None:
        # Surface the URL now; the device-code flow finishes in the background.
        asyncio.create_task(_drain_auth_proc(proc))
        return {
            "ok": True,
            "authorize_url": auth_url,
            "outputs": ["".join(captured)],
            "status": "awaiting_browser",
        }
    # No URL surfaced (non-device-code CLI): wait for completion and report rc.
    try:
        await asyncio.wait_for(proc.wait(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
    rc = proc.returncode
    out = "".join(captured)
    if rc not in (0, None):
        raise HTTPException(
            status_code=400,
            detail=f"CLI 授权失败（退出码 {rc}）：\n{out[-1500:]}",
        )
    return {"ok": True, "authorize_url": None, "outputs": [out]}


@router.post("/{name}/cli/status")
async def cli_status(name: str, request: Request):
    if _dev_shared_mode():
        raise HTTPException(
            status_code=403,
            detail="CLI connectors are disabled in shared/dev runtime mode",
        )
    cli = _cli_entry(name)
    if not cli:
        raise HTTPException(status_code=404, detail=f"no CLI connector '{name}'")
    cmds = _extract_cli_commands(cli.get("status"))
    if not cmds:
        return {"ok": True, "installed": None, "output": ""}
    out, _ = await _run(cmds[0], timeout=30)
    return {"ok": True, "output": out}


async def _run(cmd: str, timeout: int = 300) -> tuple[str, int | None]:
    """Run a shell command in the container.

    Returns ``(combined_output, returncode)``. ``returncode`` is ``None`` when the
    command could not be launched at all.
    """
    import asyncio

    # CLI install/auth/status commands run as the non-root runtime user.  The
    # container's system npm defaults its global prefix to /usr/local, which that
    # user cannot write -> `npm install -g` fails with EACCES.  Redirect global
    # installs to a user-writable prefix ($HOME/.local) whose bin dir is already
    # on PATH, so the just-installed `dws`/`tcb`/... binaries resolve for the
    # subsequent auth/status steps without any further PATH juggling.
    env = os.environ.copy()
    _home = env.get("HOME") or "/opt/data"
    _npm_prefix = os.path.join(_home, ".local")
    env["npm_config_prefix"] = _npm_prefix
    _bin_dir = os.path.join(_npm_prefix, "bin")
    _cur_path = env.get("PATH", "")
    if _bin_dir not in _cur_path.split(":"):
        env["PATH"] = f"{_bin_dir}:{_cur_path}"
    # pip install (e.g. tccli) as a non-root user also needs a writable target.
    env["PYTHONUSERBASE"] = os.path.join(_home, ".local")

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = (stdout or b"").decode("utf-8", errors="replace")
        return out, proc.returncode
    except Exception as exc:  # noqa: BLE001
        return f"<error running '{cmd}': {exc}>", 1
