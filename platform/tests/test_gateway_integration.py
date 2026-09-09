"""使用临时数据库和模拟上游验证实际 HTTP 边界。"""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from app.auth.dependencies import require_admin, require_setup_complete
from app.db.engine import get_db
from app.db.models import (
    AuditLog,
    Base,
    GatewayRequest,
    GatewayRoute,
    GatewayToken,
    ModelProviderConfig,
    User,
)
from app.gateway import admin, proxy
from app.gateway.storage import GatewayRuntime
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest_asyncio.fixture
async def gateway(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    User.__table__,
                    ModelProviderConfig.__table__,
                    GatewayToken.__table__,
                    GatewayRequest.__table__,
                    GatewayRoute.__table__,
                    AuditLog.__table__,
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all(
            [
                User(
                    id="owner",
                    username="owner",
                    email="owner@test",
                    password_hash="test",
                    role="admin",
                ),
                User(id="other", username="other", email="other@test", password_hash="test"),
                ModelProviderConfig(
                    id="vendor",
                    display_name="Vendor",
                    api_base="https://vendor.test/v1",
                    api_key="upstream-test-key",
                    models=[{"id": "coding", "name": "Coding"}],
                    api_mode="chat_completions",
                ),
            ]
        )
        await db.commit()
    seen = []
    streams = []
    response_config = {
        "status": 200,
        "body": b'{"usage":{"prompt_tokens":5,"completion_tokens":3}}',
        "headers": {"content-type": "application/json", "x-vendor-id": "abc"},
    }

    async def upstream(req):
        seen.append(req)
        if response_config.get("error"):
            raise response_config["error"]
        stream = ChunkStream(response_config.get("chunks", [response_config["body"]]))
        streams.append(stream)
        return httpx.Response(
            response_config["status"], headers=response_config["headers"], stream=stream
        )

    runtime = GatewayRuntime(sessions, httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    await runtime.start()
    app = FastAPI()
    app.state.gateway = runtime
    runtime.test_app = app
    app.include_router(admin.router)
    app.include_router(proxy.router)

    async def db_dep():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[require_admin] = lambda: User(id="owner", role="admin")
    app.dependency_overrides[require_setup_complete] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, runtime, sessions, seen, response_config, streams
    await runtime.close()
    await engine.dispose()


async def mint(client, **extra):
    response = await client.post(
        "/api/admin/gateway/tokens",
        json={"name": "开发", "user_id": "owner", "allowed_models": ["vendor/coding"], **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_passthrough_usage_and_secret_once(gateway):
    client, runtime, sessions, seen, config, streams = gateway
    created = await mint(client)
    secret = created["secret"]
    raw = b'{ "model": "coding", "messages": [], "future_option": {"a":1} }'
    response = await client.post(
        "/api/gateway/v1/chat/completions?beta=yes",
        content=raw,
        headers={
            "Authorization": "Bearer " + secret,
            "cookie": "secret=hidden",
            "x-client-test": "ok",
        },
    )
    assert response.status_code == 200
    assert response.content == config["body"]
    assert response.headers["x-vendor-id"] == "abc"
    assert seen[0].content == raw
    assert str(seen[0].url) == "https://vendor.test/v1/chat/completions?beta=yes"
    assert seen[0].headers["authorization"] == "Bearer upstream-test-key"
    assert "cookie" not in seen[0].headers
    assert streams[0].closed
    await runtime.queue.join()
    listing = await client.get("/api/admin/gateway/tokens")
    assert secret not in listing.text
    async with sessions() as db:
        token = await db.get(GatewayToken, created["token"]["id"])
        assert token.token_hash != secret and token.last_used_at
    records = (await client.get("/api/admin/gateway/requests")).json()["items"]
    assert records[0]["input_tokens"] == 5
    detail = (await client.get("/api/admin/gateway/requests/" + records[0]["id"])).json()
    assert detail["content_state"] == "disabled" and detail["request_content"] is None
    usage = (await client.get("/api/admin/gateway/usage")).json()
    assert usage["requests"] == 1 and usage["input_tokens"] == 5


@pytest.mark.asyncio
async def test_whitelist_protocol_and_revocation(gateway):
    client, runtime, _, seen, _, _ = gateway
    created = await mint(client)
    headers = {"authorization": "Bearer " + created["secret"]}
    assert (
        await client.post("/api/gateway/v1/responses", json={"model": "coding"}, headers=headers)
    ).status_code == 400
    assert (
        await client.post(
            "/api/gateway/v1/chat/completions", json={"model": "other-model"}, headers=headers
        )
    ).status_code == 403
    assert not seen
    assert (
        await client.put("/api/admin/gateway/routes/vendor", json={"protocols": ["responses"]})
    ).status_code == 200
    assert (
        await client.post(
            "/api/gateway/v1/responses",
            json={"model": "vendor/coding", "input": "hi"},
            headers=headers,
        )
    ).status_code == 200
    assert json.loads(seen[0].content)["model"] == "coding"
    await client.post("/api/admin/gateway/tokens/" + created["token"]["id"] + "/revoke")
    assert (
        await client.post("/api/gateway/v1/responses", json={"model": "coding"}, headers=headers)
    ).status_code == 401
    assert (
        await client.patch(
            "/api/admin/gateway/tokens/" + created["token"]["id"], json={"enabled": True}
        )
    ).status_code == 409
    await runtime.queue.join()


@pytest.mark.asyncio
async def test_anthropic_stream_and_content_expiry(gateway):
    client, runtime, sessions, seen, config, _ = gateway
    await client.put("/api/admin/gateway/routes/vendor", json={"protocols": ["anthropic_messages"]})
    created = await mint(client, retain_content=True)
    raw = b'event: message_start\ndata: {"message":{"usage":{"input_tokens":10,"output_tokens":0}}}\n\ndata: {"usage":{"output_tokens":4}}\n\n'
    config.update(
        chunks=[raw[i : i + 3] for i in range(0, len(raw), 3)],
        headers={"content-type": "text/event-stream"},
    )
    response = await client.post(
        "/api/gateway/v1/messages",
        json={"model": "coding", "stream": True},
        headers={"x-api-key": created["secret"], "anthropic-beta": "test-feature"},
    )
    assert response.content == raw
    assert seen[0].headers["x-api-key"] == "upstream-test-key"
    assert seen[0].headers["anthropic-beta"] == "test-feature"
    assert "authorization" not in seen[0].headers
    await runtime.queue.join()
    records = (await client.get("/api/admin/gateway/requests")).json()["items"]
    assert records[0]["output_tokens"] == 4
    path = "/api/admin/gateway/requests/" + records[0]["id"]
    assert (await client.get(path)).json()["response_content"] == raw.decode()
    async with sessions() as db:
        row = await db.get(GatewayRequest, records[0]["id"])
        row.content_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await db.commit()
    assert (await client.get(path)).json()["content_state"] == "expired"
    await runtime.cleanup()
    async with sessions() as db:
        assert (await db.get(GatewayRequest, records[0]["id"])).response_content is None


@pytest.mark.asyncio
async def test_error_unknown_usage_and_inactive_owner(gateway):
    client, runtime, sessions, seen, config, _ = gateway
    created = await mint(client)
    headers = {"authorization": "Bearer " + created["secret"]}
    config.update(status=429, body=b'{"error":{"message":"rate limit"}}')
    response = await client.post(
        "/api/gateway/v1/chat/completions", json={"model": "coding"}, headers=headers
    )
    assert response.status_code == 429 and response.content == config["body"]
    await runtime.queue.join()
    row = (await client.get("/api/admin/gateway/requests")).json()["items"][0]
    assert row["input_tokens"] is None and row["error_type"] == "upstream_http"
    async with sessions() as db:
        owner = await db.get(User, "owner")
        owner.is_active = False
        await db.commit()
    assert (await client.get("/api/gateway/v1/models", headers=headers)).status_code == 401
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_provider_scope_and_expired_token(gateway):
    client, _, sessions, seen, _, _ = gateway
    created = await mint(client)
    headers = {"authorization": "Bearer " + created["secret"]}
    async with sessions() as db:
        provider = await db.get(ModelProviderConfig, "vendor")
        provider.user_scope = {"mode": "allow", "user_ids": ["other"]}
        await db.commit()
    assert (
        await client.post(
            "/api/gateway/v1/chat/completions", json={"model": "coding"}, headers=headers
        )
    ).status_code == 403
    async with sessions() as db:
        token = await db.get(GatewayToken, created["token"]["id"])
        token.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await db.commit()
    assert (
        await client.post(
            "/api/gateway/v1/chat/completions", json={"model": "coding"}, headers=headers
        )
    ).status_code == 401
    assert not seen


@pytest.mark.asyncio
async def test_admin_routes_reject_non_admin(gateway):
    from app.auth.dependencies import get_current_user

    client, runtime, *_ = gateway
    del runtime.test_app.dependency_overrides[require_admin]
    runtime.test_app.dependency_overrides[get_current_user] = lambda: User(id="other", role="user")
    assert (await client.get("/api/admin/gateway/tokens")).status_code == 403
    assert (await client.get("/api/admin/gateway/requests")).status_code == 403


@pytest.mark.asyncio
async def test_stream_delivers_first_chunk_before_completion_and_closes_on_disconnect(gateway):
    client, runtime, sessions, *_ = gateway
    created = await mint(client)
    reached = asyncio.Event()
    disconnected = asyncio.Event()
    stream_closed = asyncio.Event()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            stream_closed.set()

    await runtime.client.aclose()
    runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SlowStream())))
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b'{"model":"coding","stream":true}', "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            assert message["body"] == b'data: {"choices":[]}\n\n'
            reached.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/api/gateway/v1/chat/completions", "raw_path": b"/api/gateway/v1/chat/completions", "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("test", 123), "headers": [(b"authorization", ("Bearer " + created["secret"]).encode())]}
    task = asyncio.create_task(runtime.test_app(scope, receive, send))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        assert not task.done()
        disconnected.set()
        await asyncio.wait_for(task, 3)
        await asyncio.wait_for(stream_closed.wait(), 1)
        await runtime.queue.join()
        async with sessions() as db:
            row = await db.scalar(select(GatewayRequest))
            assert row.outcome == "cancelled" and row.first_byte_ms is not None
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_queue_overflow_is_observable(gateway):
    _, runtime, *_ = gateway
    for _ in range(runtime.queue.maxsize + 1):
        runtime.record({})
    assert runtime.dropped == 1
    # 此用例只验证入队边界，不向数据库写入模拟空记录。
    while not runtime.queue.empty():
        runtime.queue.get_nowait()
        runtime.queue.task_done()
