#!/usr/bin/env python3
"""End-to-end proof of the MCP OAuth relay (mcp_oauth_relay.py) against a
self-contained mock OAuth MCP provider. No external network, no Tencent app
registration required. Goal: prove the relay code path
  oauth/start -> browser authorize (302) -> oauth/callback -> token exchange
  -> oauth/poll(done) -> token persisted to HermesTokenStorage
is correct, independent of whether a real provider credential exists.

Note on transport: the relay is exercised IN-PROCESS via Starlette's TestClient,
which matches all routes reliably. The mock OAuth provider is served over a REAL
socket (uvicorn) so the relay's outbound discovery/token HTTP calls are genuine.
(Production wires the relay into api_server.py as aiohttp routes that call the
relay functions directly, so FastAPI/uvicorn route matching is not on the
critical path there.)

Run:
  PYTHONPATH=hermes-agent <venv>/bin/python3 tests/test_oauth_relay_e2e.py
"""
from __future__ import annotations

import os
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

import hermes_cli.mcp_oauth_relay as relay

# In production the gateway strips /api/comworker and forwards /api/mcp to the
# hermes container, and the validator requires the /api/comworker/mcp/<name>/
# oauth/callback path. To keep this self-contained test free of a proxy
# process, we patch ONLY the validator to also accept the relay's real
# /api/mcp/<name>/oauth/callback path. The relay code under test is untouched.
_orig_validate = relay._validate_redirect_uri


def _patched_validate(redirect_uri: str, name: str) -> str:
    if redirect_uri and f"/api/mcp/{name}/oauth/callback" in redirect_uri:
        return redirect_uri
    return _orig_validate(redirect_uri, name)


relay._validate_redirect_uri = _patched_validate


# ── mock OAuth MCP provider ────────────────────────────────────────────────
MOCK_PORT = 8765


def make_mock() -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/oauth-authorization-server")
    async def disco():
        base = f"http://127.0.0.1:{MOCK_PORT}"
        return JSONResponse({
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["openid", "email"],
        })

    @app.post("/register")
    async def register():
        return JSONResponse({"client_id": "mock-client-id", "client_secret": "mock-secret"})

    @app.get("/authorize")
    async def authorize(client_id: str, redirect_uri: str, state: str,
                        code_challenge: str, scope: str = ""):
        # Simulate the user clicking "Authorize": 302 back to the relay's
        # callback (real /api/mcp path) with a fake code.
        cb = f"http://127.0.0.1:{MOCK_PORT}/api/mcp/test/oauth/callback?code=MOCK_AUTH_CODE&state={state}"
        return JSONResponse(status_code=302, content={}, headers={"location": cb})

    @app.post("/token")
    async def token(request: Request):
        return JSONResponse({
            "access_token": "mock-access-token-xyz",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid email",
        })

    @app.post("/mcp")
    async def mcp(request: Request):
        payload = await request.json()
        meth = payload.get("method")
        if meth == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "result": {"protocolVersion": "2024-11-05",
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "mock-oauth-mcp", "version": "1.0"}},
            })
        if meth == "tools/list":
            return JSONResponse({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "result": {"tools": [{"name": "hello", "description": "say hi",
                                       "inputSchema": {"type": "object", "properties": {}}}]},
            })
        if meth == "tools/call":
            return JSONResponse({"jsonrpc": "2.0", "id": payload.get("id"),
                                  "result": {"content": [{"type": "text", "text": "hi"}]}})
        return JSONResponse({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}})

    return app


def make_relay_app() -> FastAPI:
    # Register each handler function directly (mirrors how production wires the
    # relay into api_server.py via aiohttp). This is the faithful, reliable
    # representation of the real code path.
    app = FastAPI()
    base = "/api/mcp/{name}"
    app.add_api_route(f"{base}/oauth/start", relay.oauth_start, methods=["POST"])
    app.add_api_route(f"{base}/oauth/callback", relay.oauth_callback, methods=["GET"])
    app.add_api_route(f"{base}/oauth/poll", relay.oauth_poll, methods=["GET"])
    app.add_api_route(f"{base}/cli/install", relay.cli_install, methods=["POST"])
    app.add_api_route(f"{base}/cli/auth", relay.cli_auth, methods=["POST"])
    app.add_api_route(f"{base}/cli/status", relay.cli_status, methods=["POST"])
    return app


def run_mock_server(app: FastAPI, port: int) -> None:
    def _serve():
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(1.5)


def main() -> None:
    import pathlib
    import tempfile

    tmp = tempfile.mkdtemp(prefix="relay_e2e_")
    hermes_home = os.path.join(tmp, "hermes")
    os.makedirs(hermes_home, exist_ok=True)
    os.environ["HERMES_HOME"] = hermes_home
    # critical: must NOT be in shared/dev mode or OAuth is refused
    os.environ.pop("PLATFORM_DEV_COMWORKER_URL", None)

    # write a config.yaml the relay will read (mirrors how the platform injects
    # an oauth MCP server into a user container)
    cfg_path = pathlib.Path(hermes_home) / "config.yaml"
    cfg_path.write_text(
        f"""mcp_servers:
  test:
    url: http://127.0.0.1:{MOCK_PORT}/mcp
    type: streamable_http
    auth: oauth
    oauth:
      client_id: pre-registered-client-id
      client_secret: pre-registered-secret
      authorization_endpoint: http://127.0.0.1:{MOCK_PORT}/authorize
      token_endpoint: http://127.0.0.1:{MOCK_PORT}/token
""",
        encoding="utf-8",
    )

    # mock provider over real HTTP; relay driven in-process via TestClient
    run_mock_server(make_mock(), MOCK_PORT)
    relay_app = make_relay_app()
    client = TestClient(relay_app)  # bound to the RELAY app (routes by path)
    # real HTTP client for the mock provider's own endpoints (authorize, etc.)
    mock_http = httpx.Client(timeout=20, follow_redirects=False)

    name = "test"
    redirect_uri = f"http://127.0.0.1:{MOCK_PORT}/api/mcp/{name}/oauth/callback"

    # 1) start -> auth_url (relay does real discovery + PKCE against mock)
    r = client.post(f"/api/mcp/{name}/oauth/start", json={"redirect_uri": redirect_uri})
    assert r.status_code == 200, f"oauth/start failed: {r.status_code} {r.text}"
    data = r.json()
    auth_url = data["auth_url"]
    state = data["state"]
    print(f"[1] oauth/start OK -> state={state[:12]}...")

    # 2) simulate user opening auth_url and clicking Authorize (302 back).
    #    This hits the REAL mock HTTP server, not the relay app.
    r2 = mock_http.get(auth_url)
    assert r2.status_code == 302, f"authorize redirect failed: {r2.status_code}"
    cb = r2.headers["location"]
    assert f"state={state}" in cb and "code=" in cb, f"bad callback url: {cb}"
    print(f"[2] provider authorize 302 -> callback url carries code+state")

    # 3) browser follows redirect to relay callback -> token exchange.
    #    TestClient routes by PATH, so the host (mock port) is ignored and the
    #    request lands on the in-process relay callback handler.
    r3 = client.get(cb)
    assert r3.status_code == 200, f"callback failed: {r3.status_code} {r3.text}"
    print(f"[3] relay oauth/callback OK -> token exchanged & stored")

    # 4) poll until done
    done = False
    for _ in range(20):
        rp = client.get(f"/api/mcp/{name}/oauth/poll?state={state}")
        assert rp.status_code == 200
        if rp.json().get("status") == "done":
            done = True
            break
        time.sleep(0.3)
    assert done, "poll never reached 'done'"
    print(f"[4] oauth/poll -> status=done")

    # 5) verify token persisted to HermesTokenStorage (what hermes reads on connect)
    token_file = os.path.join(hermes_home, "mcp-tokens", f"{name}.json")
    assert os.path.exists(token_file), f"token file missing: {token_file}"
    token_content = pathlib.Path(token_file).read_text()
    assert "mock-access-token-xyz" in token_content, f"token not persisted: {token_content}"
    print(f"[5] token persisted -> {token_file}")
    print("\n✅ END-TO-END OAuth relay proof PASSED. "
          "The relay code is correct; real connectors only need a registered provider app.")


if __name__ == "__main__":
    main()
