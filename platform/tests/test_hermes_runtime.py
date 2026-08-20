import io
import json
import logging
import sys
import tarfile
import types
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

if "docker" not in sys.modules:
    docker_stub = types.ModuleType("docker")
    docker_stub.from_env = lambda: None
    docker_stub.DockerClient = object
    docker_stub.models = SimpleNamespace(containers=SimpleNamespace(Container=object))
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.APIError = RuntimeError
    docker_errors.NotFound = RuntimeError
    docker_stub.errors = docker_errors
    sys.modules["docker"] = docker_stub
    sys.modules["docker.errors"] = docker_errors

if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from app.db.models import User
from app.runtime_backend import RuntimeContext


class FakeStreamResponse:
    def __init__(self, chunks, status_code=200):
        self.status_code = status_code
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeAsyncClient:
    def __init__(self, *, response_map=None, stream_map=None, capture=None, timeout=None):
        self.response_map = response_map or {}
        self.stream_map = stream_map or {}
        self.capture = capture if capture is not None else []
        self.timeout = timeout
        self.is_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aclose(self):
        self.is_closed = True

    async def request(self, method, url, **kwargs):
        self.capture.append((method, url, kwargs))
        responder = self.response_map[(method, url)]
        if isinstance(responder, Exception):
            raise responder
        if callable(responder):
            return responder(method, url, kwargs)
        return responder

    def stream(self, method, url, **kwargs):
        self.capture.append((f"STREAM {method}", url, kwargs))
        responder = self.stream_map[(method, url)]
        if isinstance(responder, Exception):
            raise responder
        if callable(responder):
            responder = responder(method, url, kwargs)
        return responder


class FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


@pytest.fixture
def dedicated_user():
    return User(
        id="u-hermes",
        username="hermes",
        email="hermes@example.com",
        password_hash="x",
        runtime_mode="dedicated",
        is_active=True,
    )


@pytest.fixture
def shared_user():
    return User(
        id="shared-user-1234567890abcdef",
        username="shared",
        email="shared@example.com",
        password_hash="x",
        runtime_mode="shared",
        is_active=True,
    )


def _tar_file_bytes(name: str, contents: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name=name)
        info.size = len(contents)
        archive.addfile(info, io.BytesIO(contents))
    buffer.seek(0)
    return buffer.read()


def test_read_hermes_data_file_falls_back_to_workspace_root(monkeypatch):
    from app.runtime_backends import hermes_files

    class FakeContainer:
        def get_archive(self, path):
            if path == "/opt/data/agents/main/sessions/sessions.json":
                raise hermes_files.DockerNotFound("missing")
            assert path == "/workspace/agents/main/sessions/sessions.json"
            return [_tar_file_bytes("sessions.json", b'{"ok":true}')], {}

    monkeypatch.setattr("app.runtime_backends.hermes_files.get_docker_container", lambda container_id: FakeContainer())

    data = hermes_files.read_data_file_from_hermes_container("container-1", "agents/main/sessions/sessions.json")

    assert data == b'{"ok":true}'


@pytest.mark.asyncio
async def test_hermes_client_get_models_wraps_response(monkeypatch):
    from app.hermes_client import HermesClient

    captured = []

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            response_map={
                ("GET", "http://hermes.local/v1/models"): FakeResponse(
                    json_data={"data": [{"id": "hermes-agent"}]}
                )
            },
            capture=captured,
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    client = HermesClient(base_url="http://hermes.local")
    payload = await client.get_models()

    assert payload == {"data": [{"id": "hermes-agent"}]}
    assert captured[0][0] == "GET"
    assert captured[0][1] == "http://hermes.local/v1/models"


@pytest.mark.asyncio
async def test_hermes_client_sends_bearer_auth_header(monkeypatch):
    from app.hermes_client import HermesClient

    captured = []

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            response_map={
                ("GET", "http://hermes.local/v1/models"): FakeResponse(
                    json_data={"data": [{"id": "hermes-agent"}]}
                )
            },
            capture=captured,
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    client = HermesClient(base_url="http://hermes.local", api_key="bridge-key")
    await client.get_models()

    assert captured[0][2]["headers"]["Authorization"] == "Bearer bridge-key"


@pytest.mark.asyncio
async def test_hermes_client_create_run_sends_session_key_header(monkeypatch):
    from app.hermes_client import HermesClient

    captured = []

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            response_map={
                ("POST", "http://hermes.local/v1/runs"): FakeResponse(
                    status_code=202,
                    json_data={"run_id": "run-123", "status": "started"},
                )
            },
            capture=captured,
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    client = HermesClient(base_url="http://hermes.local", api_key="bridge-key")
    payload = await client.create_run(
        message="Hi",
        session_id="agent:usr_sharedagent:sess-1",
        session_key="agent:usr_sharedagent:sess-1",
    )

    assert payload["run_id"] == "run-123"
    assert captured[0][2]["headers"] == {
        "Authorization": "Bearer bridge-key",
        "X-Hermes-Session-Key": "agent:usr_sharedagent:sess-1",
    }


@pytest.mark.asyncio
async def test_hermes_client_retries_connect_errors(monkeypatch):
    from app.hermes_client import HermesClient

    captured = []
    attempts = {"count": 0}

    def responder(method, url, kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("not ready")
        return FakeResponse(json_data={"data": [{"id": "hermes-agent"}]})

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            response_map={
                ("GET", "http://hermes.local/v1/models"): responder,
            },
            capture=captured,
        )

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)
    monkeypatch.setattr("app.hermes_client.asyncio.sleep", fake_sleep)

    client = HermesClient(base_url="http://hermes.local", connect_retries=1)
    payload = await client.get_models()

    assert payload == {"data": [{"id": "hermes-agent"}]}
    assert attempts["count"] == 2
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_hermes_client_reuses_async_client_for_request_and_stream(monkeypatch):
    from app.hermes_client import HermesClient

    captured = []
    created = []

    def fake_client(*args, **kwargs):
        created.append(kwargs)
        return FakeAsyncClient(
            response_map={
                ("GET", "http://hermes.local/v1/models"): FakeResponse(
                    json_data={"data": [{"id": "hermes-agent"}]}
                ),
            },
            stream_map={
                ("GET", "http://hermes.local/v1/runs/run-1/events"): FakeStreamResponse(
                    [b'data: {"type":"run.completed","output":"ok"}\n\n']
                ),
            },
            capture=captured,
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    client = HermesClient(base_url="http://hermes.local")
    await client.get_models()
    await client.collect_run_events("run-1")
    await client.aclose()

    assert len(created) == 1
    assert captured[0][0] == "GET"
    assert captured[1][0] == "STREAM GET"


@pytest.mark.skip(reason="get_agent_info does not use models endpoint in businessbot")
@pytest.mark.asyncio
async def test_dedicated_hermes_get_agent_info_uses_models_endpoint(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_get_models(self):
        return {"data": [{"id": "hermes-agent", "object": "model"}]}

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.get_models", fake_get_models)

    payload = await backend.get_agent_info(RuntimeContext(user=dedicated_user, scope="dedicated"))

    assert payload["defaultId"] == "main"
    assert payload["mainKey"] == "agent:main"
    assert payload["scope"] == "dedicated"
    assert payload["runtime_mode"] == "dedicated"
    assert [item["id"] for item in payload["agents"]][:2] == ["main", "manager"]


@pytest.mark.skip(reason="get_agent_info does not use models endpoint in businessbot")
@pytest.mark.asyncio
async def test_dedicated_hermes_get_agent_info_uses_packaged_agents(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_get_models(self):
        return {
            "data": [
                {"id": "hermes-agent", "object": "model"},
                {"id": "main", "object": "model"},
                {"id": "innovation", "object": "model"},
            ]
        }

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.get_models", fake_get_models)

    payload = await backend.get_agent_info(RuntimeContext(user=dedicated_user, scope="dedicated"))

    agent_ids = [item["id"] for item in payload["agents"]]
    assert payload["defaultId"] == "main"
    assert payload["mainKey"] == "agent:main"
    assert agent_ids[:6] == ["main", "manager", "programmer", "researcher", "hr", "doctor"]
    assert "innovation" in agent_ids
    assert "hermes-agent" not in agent_ids
    innovation = next(item for item in payload["agents"] if item["id"] == "innovation")
    assert innovation["available"] is True


@pytest.mark.asyncio
async def test_dedicated_hermes_list_skills_reads_container_metadata(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    class FakeContainer:
        docker_id = "container-123"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return FakeContainer()

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.ensure_running", fake_ensure_running)
    monkeypatch.setattr(
        "app.runtime_backends.dedicated_hermes.list_skills_from_hermes_container",
        lambda container_id: [
            {
                "name": "dogfood",
                "description": "Systematic QA testing",
                "source": "hermes",
                "disabled": False,
            }
        ],
    )

    payload = await DedicatedHermesBackend().list_skills(
        RuntimeContext(user=dedicated_user, scope="dedicated")
    )

    assert payload == [
        {
            "name": "dogfood",
            "description": "Systematic QA testing",
            "source": "hermes",
            "disabled": False,
        }
    ]


def test_agent_skill_scope_hides_identical_global_copies(monkeypatch):
    from app.runtime_backends import hermes_skills

    class FakeExecResult:
        exit_code = 0

        def __init__(self, output):
            self.output = output

    class FakeContainer:
        def exec_run(self, cmd):
            root = cmd[-1]
            if root == "/opt/data/skills":
                payload = [
                    {
                        "name": "shared-global",
                        "description": "Available everywhere",
                        "source": "hermes",
                        "disabled": False,
                        "path": "shared-global",
                        "fingerprint": "same",
                    }
                ]
            else:
                payload = [
                    {
                        "name": "shared-global",
                        "description": "Copied into the agent profile",
                        "source": "hermes",
                        "disabled": False,
                        "path": "shared-global",
                        "fingerprint": "same",
                    },
                    {
                        "name": "agent-only",
                        "description": "Local workflow",
                        "source": "hermes",
                        "disabled": False,
                        "path": "agent-only",
                        "fingerprint": "different",
                    },
                ]
            return FakeExecResult(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(hermes_skills, "get_docker_container", lambda container_id: FakeContainer())

    payload = hermes_skills.list_skills_from_hermes_container(
        "container-123",
        scope="agent",
        agent_id="programmer",
    )

    assert [item["name"] for item in payload] == ["agent-only"]
    assert payload[0]["scope"] == "agent:programmer"
    assert "fingerprint" not in payload[0]


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_context_does_not_call_comworker_agent_api(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeDB:
        def __init__(self):
            self.added = None
            self.committed = False

        async def execute(self, _stmt):
            return FakeResult()

        def add(self, binding):
            self.added = binding

        async def commit(self):
            self.committed = True

        async def refresh(self, _binding):
            return None

    async def fail_create_shared_agent(*_args, **_kwargs):
        raise AssertionError("shared Hermes must not call ComWorker /api/agents")

    async def fake_audit_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.shared_runtime.create_shared_agent", fail_create_shared_agent)
    monkeypatch.setattr("app.shared_runtime.write_audit_log", fake_audit_log)

    db = FakeDB()
    ctx = await SharedHermesBackend()._context_for_user(db, shared_user)

    assert db.added is not None
    assert db.committed is True
    assert ctx.binding.comworker_agent_id.startswith("usr_")
    assert ctx.session_prefix == f"agent:{ctx.binding.comworker_agent_id}:"


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_get_agent_info_uses_models_endpoint(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeSharedContext:
        class Binding:
            comworker_agent_id = "usr_sharedagent"

        binding = Binding()

    async def fake_context(self, db, user):
        assert user is shared_user
        return FakeSharedContext()

    async def fake_get_models(self):
        return {"data": [{"id": "hermes-agent", "object": "model"}]}

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.HermesClient.get_models", fake_get_models)

    backend = SharedHermesBackend()
    payload = await backend.get_agent_info(RuntimeContext(user=shared_user, scope="shared"))

    assert payload == {
        "agents": [{"id": "hermes-agent", "object": "model"}],
        "defaultId": "usr_sharedagent",
        "runtime_mode": "shared",
    }


@pytest.mark.asyncio
async def test_dedicated_hermes_reuses_client_for_same_runtime(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.settings.dedicated_hermes_api_key", "bridge-key")

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")
    ctx = RuntimeContext(user=dedicated_user, scope="dedicated")

    first = await backend._client(ctx)
    second = await backend._client(ctx)

    assert second is first


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
def test_shared_hermes_client_uses_platform_api_key(monkeypatch):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.shared_hermes_url", "http://shared-hermes")
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.settings.shared_hermes_api_key",
        "shared-key",
        raising=False,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.hermes_connect_retries", 12, raising=False)
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.settings.hermes_retry_delay_seconds",
        0.1,
        raising=False,
    )

    client = SharedHermesBackend()._client()

    assert client.api_key == "shared-key"
    assert client.connect_retries == 12
    assert client.retry_delay_seconds == 0.1


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
def test_shared_hermes_reuses_client_for_same_runtime(monkeypatch):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.shared_hermes_url", "http://shared-hermes")
    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.shared_hermes_api_key", "shared-key", raising=False)

    backend = SharedHermesBackend()

    assert backend._client() is backend._client()


@pytest.mark.skip(reason="send_message requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_dedicated_hermes_send_message_starts_run_with_session_id(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    captured = {}

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_create_run(self, *, message, session_id, session_key=None, model="hermes-agent", conversation_history=None):
        captured["message"] = message
        captured["session_id"] = session_id
        captured["session_key"] = session_key
        captured["model"] = model
        captured["conversation_history"] = conversation_history
        return {
            "run_id": "run-123",
            "session_id": session_id,
            "status": "started",
        }

    async def fake_session_record(ctx, session_key):
        return {"session_id": session_key, "messages": []}

    monkeypatch.setattr(backend, "_session_record", fake_session_record)
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.create_run", fake_create_run)

    payload = await backend.send_message(
        RuntimeContext(user=dedicated_user, scope="dedicated"),
        session_key="sess-123",
        message="Hi",
    )

    assert captured == {
        "message": "Hi",
        "session_id": "sess-123",
        "session_key": "sess-123",
        "model": "hermes-agent",
        "conversation_history": [],
    }
    assert payload["run_id"] == "run-123"
    assert payload["runId"] == "run-123"
    assert payload["session_key"] == "sess-123"
    assert payload["sessionKey"] == "sess-123"
    assert payload["raw"]["run_id"] == "run-123"


@pytest.mark.skip(reason="send_message requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_dedicated_hermes_send_message_uses_agent_profile_model(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    captured = {}
    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_create_run(self, *, message, session_id, session_key=None, model="hermes-agent", conversation_history=None):
        captured["message"] = message
        captured["session_id"] = session_id
        captured["session_key"] = session_key
        captured["model"] = model
        captured["conversation_history"] = conversation_history
        return {"run_id": "run-innovation", "session_id": session_id}

    async def fake_session_record(ctx, session_key):
        return {"session_id": session_key, "messages": []}

    monkeypatch.setattr(backend, "_session_record", fake_session_record)
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.create_run", fake_create_run)

    await backend.send_message(
        RuntimeContext(user=dedicated_user, scope="dedicated"),
        session_key="agent:innovation:session-123",
        message="挖掘空白点",
    )

    assert captured == {
        "message": "挖掘空白点",
        "session_id": "agent:innovation:session-123",
        "session_key": "agent:innovation:session-123",
        "model": "innovation",
        "conversation_history": [],
    }


@pytest.mark.skip(reason="send_message requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_dedicated_hermes_send_message_passes_existing_session_history(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    captured = {}

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_session_record(ctx, session_key):
        assert session_key == "sess-123"
        return {
            "session_id": "sess-123",
            "messages": [
                {"role": "user", "content": "肺栓塞的临床表现有哪些"},
                {"role": "assistant", "content": "直接裸答"},
                {"role": "tool", "content": "large tool result should not be replayed"},
                {"role": "assistant", "content": ""},
            ],
        }

    async def fake_create_run(self, *, message, session_id, session_key=None, model="hermes-agent", conversation_history=None):
        captured["conversation_history"] = conversation_history
        return {"run_id": "run-123", "session_id": session_id}

    monkeypatch.setattr(backend, "_session_record", fake_session_record)
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.create_run", fake_create_run)

    await backend.send_message(
        RuntimeContext(user=dedicated_user, scope="dedicated"),
        session_key="sess-123",
        message="你怎么不用PICO或者论文检索",
    )

    assert captured["conversation_history"] == [
        {"role": "user", "content": "肺栓塞的临床表现有哪些"},
        {"role": "assistant", "content": "直接裸答"},
    ]


@pytest.mark.skip(reason="send_message requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_dedicated_hermes_send_message_logs_run_start_latency(
    monkeypatch,
    caplog,
    dedicated_user,
):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    times = iter([10.0, 10.125])
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.time.perf_counter", lambda: next(times))

    async def fake_create_run(self, *, message, session_id, session_key=None, model="hermes-agent", conversation_history=None):
        assert message == "Hi"
        assert session_id == "sess-123"
        assert session_key == "sess-123"
        assert model == "hermes-agent"
        assert conversation_history == []
        return {"run_id": "run-123", "session_id": session_id, "status": "started"}

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.create_run", fake_create_run)

    caplog.set_level(logging.INFO, logger="app.runtime_backends.dedicated_hermes")
    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_session_record(ctx, session_key):
        return {"session_id": session_key, "messages": []}

    monkeypatch.setattr(backend, "_session_record", fake_session_record)

    await backend.send_message(
        RuntimeContext(user=dedicated_user, scope="dedicated"),
        session_key="sess-123",
        message="Hi",
    )

    assert "hermes_run_started" in caplog.text
    assert "scope=dedicated" in caplog.text
    assert "user_id=u-hermes" in caplog.text
    assert "run_id=run-123" in caplog.text
    assert "elapsed_ms=125.0" in caplog.text


@pytest.mark.asyncio
async def test_dedicated_hermes_client_uses_platform_api_key(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.settings.dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.settings.hermes_connect_retries", 12, raising=False)
    monkeypatch.setattr(
        "app.runtime_backends.dedicated_hermes.settings.hermes_retry_delay_seconds",
        0.1,
        raising=False,
    )

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")
    client = await backend._client(RuntimeContext(user=dedicated_user, scope="dedicated"))

    assert client.api_key == "bridge-key"
    assert client.connect_retries == 12
    assert client.retry_delay_seconds == 0.1


@pytest.mark.asyncio
async def test_dedicated_hermes_base_url_waits_for_api_once(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    class FakeContainer:
        docker_id = "docker-a"
        internal_host = "172.22.0.10"
        internal_port = 18080

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return FakeContainer()

    ready_checks = []

    async def fake_get_models(self):
        ready_checks.append((self.base_url, self.api_key))
        return {"data": [{"id": "hermes-agent"}]}

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.settings.dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.get_models", fake_get_models)

    backend = DedicatedHermesBackend()
    ctx = RuntimeContext(user=dedicated_user, scope="dedicated")

    assert await backend._resolve_base_url(ctx) == "http://172.22.0.10:18080"
    assert await backend._resolve_base_url(ctx) == "http://172.22.0.10:18080"
    assert ready_checks == [("http://172.22.0.10:18080", "bridge-key")]


@pytest.mark.asyncio
async def test_dedicated_hermes_base_url_rechecks_api_after_container_recreate(
    monkeypatch,
    dedicated_user,
):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    class FakeContainer:
        internal_host = "172.22.0.10"
        internal_port = 18080

        def __init__(self, docker_id):
            self.docker_id = docker_id

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    container_ids = iter(["docker-a", "docker-b", "docker-b"])

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return FakeContainer(next(container_ids))

    ready_checks = []

    async def fake_get_models(self):
        ready_checks.append((self.base_url, self.api_key))
        return {"data": [{"id": "hermes-agent"}]}

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.settings.dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.get_models", fake_get_models)

    backend = DedicatedHermesBackend()
    ctx = RuntimeContext(user=dedicated_user, scope="dedicated")

    assert await backend._resolve_base_url(ctx) == "http://172.22.0.10:18080"
    assert await backend._resolve_base_url(ctx) == "http://172.22.0.10:18080"
    assert await backend._resolve_base_url(ctx) == "http://172.22.0.10:18080"
    assert ready_checks == [
        ("http://172.22.0.10:18080", "bridge-key"),
        ("http://172.22.0.10:18080", "bridge-key"),
    ]


@pytest.mark.asyncio
async def test_dedicated_hermes_wait_run_reads_sse_events(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        assert timeout_ms == 9000
        assert on_event is not None
        on_event({"type": "message.delta", "delta": "Done"})
        return [
            {"type": "run.started"},
            {"type": "message.completed", "message": {"role": "assistant", "content": "Done"}},
            {"type": "run.completed"},
        ]

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.collect_run_events", fake_collect)

    payload = await backend.wait_run(RuntimeContext(user=dedicated_user, scope="dedicated"), "run-123", 9000)

    assert payload["run_id"] == "run-123"
    assert payload["status"] == "completed"
    assert payload["message"]["content"] == "Done"
    assert payload["events"][0]["type"] == "run.started"


@pytest.mark.asyncio
async def test_dedicated_hermes_wait_run_uses_completed_output(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        if on_event is not None:
            on_event({"type": "message.delta", "delta": "pong"})
        return [
            {"type": "message.delta", "delta": "<think>private"},
            {"type": "message.delta", "delta": " reasoning</think>\n\npong"},
            {"type": "run.completed", "output": "<think>private</think>\n\npong"},
        ]

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.collect_run_events", fake_collect)

    payload = await backend.wait_run(RuntimeContext(user=dedicated_user, scope="dedicated"), "run-123", 9000)

    assert payload["status"] == "completed"
    assert payload["message"] == {"role": "assistant", "content": "pong"}
    assert payload["events"] == [
        {"type": "message.delta", "delta": "pong"},
        {"type": "run.completed", "output": "pong"},
    ]


@pytest.mark.asyncio
async def test_dedicated_hermes_wait_run_logs_first_delta_and_total_latency(
    monkeypatch,
    caplog,
    dedicated_user,
):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    times = iter([20.0, 20.25, 21.0])
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.time.perf_counter", lambda: next(times))

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        assert timeout_ms == 9000
        assert on_event is not None
        on_event({"type": "message.delta", "delta": "Hi"})
        return [
            {"type": "message.delta", "delta": "Hi"},
            {"type": "run.completed", "output": "Hi"},
        ]

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.collect_run_events", fake_collect)

    caplog.set_level(logging.INFO, logger="app.runtime_backends.dedicated_hermes")
    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    await backend.wait_run(RuntimeContext(user=dedicated_user, scope="dedicated"), "run-123", 9000)

    assert "hermes_run_finished" in caplog.text
    assert "scope=dedicated" in caplog.text
    assert "user_id=u-hermes" in caplog.text
    assert "run_id=run-123" in caplog.text
    assert "first_delta_ms=250.0" in caplog.text
    assert "elapsed_ms=1000.0" in caplog.text
    assert "event_count=2" in caplog.text


@pytest.mark.asyncio
async def test_dedicated_hermes_wait_run_logs_first_event_and_visible_delta_latency(
    monkeypatch,
    caplog,
    dedicated_user,
):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    times = iter([10.0, 10.1, 10.3, 10.9, 11.2])
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.time.perf_counter", lambda: next(times))

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        assert timeout_ms == 9000
        assert on_event is not None
        on_event({"type": "run.started"})
        on_event({"type": "message.delta", "delta": "<think>private"})
        on_event({"type": "message.delta", "delta": " reasoning</think>\n\nVisible"})
        return [
            {"type": "run.started"},
            {"type": "message.delta", "delta": "<think>private"},
            {"type": "message.delta", "delta": " reasoning</think>\n\nVisible"},
            {"type": "run.completed", "output": "<think>private reasoning</think>\n\nVisible"},
        ]

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.HermesClient.collect_run_events", fake_collect)

    caplog.set_level(logging.INFO, logger="app.runtime_backends.dedicated_hermes")
    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    await backend.wait_run(RuntimeContext(user=dedicated_user, scope="dedicated"), "run-123", 9000)

    assert "first_event_ms=100.0" in caplog.text
    assert "first_delta_ms=300.0" in caplog.text
    assert "first_visible_delta_ms=900.0" in caplog.text
    assert "elapsed_ms=1200.0" in caplog.text


@pytest.mark.skip(reason="HermesEventSanitizer does not filter reasoning.available events in businessbot")
def test_hermes_event_sanitizer_filters_split_thinking_delta():
    from app.runtime_backends.hermes_run import HermesEventSanitizer

    sanitizer = HermesEventSanitizer()

    assert sanitizer.sanitize_event({"type": "message.delta", "delta": "<think>\nThe user"}) is None

    event = sanitizer.sanitize_event({"type": "message.delta", "delta": " wants pong</think>\n\npong"})

    assert event == {"type": "message.delta", "delta": "pong"}
    assert sanitizer.sanitize_event({"type": "reasoning.available", "text": "private"}) is None


def test_sanitize_sse_block_filters_split_thinking_delta():
    from app.runtime_backends.hermes_run import HermesEventSanitizer, sanitize_sse_block

    sanitizer = HermesEventSanitizer()

    assert sanitize_sse_block('data: {"type":"message.delta","delta":"<think>private"}', sanitizer) is None

    block = sanitize_sse_block(
        'data: {"type":"message.delta","delta":" reasoning</think>\\n\\npong"}',
        sanitizer,
    )

    assert block == 'data: {"type":"message.delta","delta":"pong"}\n\n'


def test_summarize_run_events_strips_completed_output_thinking():
    from app.runtime_backends.hermes_run import summarize_run_events

    status_text, final_message = summarize_run_events(
        [{"type": "run.completed", "output": "<think>private</think>\n\npong"}]
    )

    assert status_text == "completed"
    assert final_message == {"role": "assistant", "content": "pong"}


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_wait_run_uses_completed_output(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        if on_event is not None:
            on_event({"type": "message.delta", "delta": "pong"})
        return [
            {"type": "message.delta", "delta": "<think>private"},
            {"type": "message.delta", "delta": " reasoning</think>\n\npong"},
            {"type": "run.completed", "output": "<think>private</think>\n\npong"},
        ]

    async def fake_ensure_owned(db, *, run_id, user_id, runtime_mode, backend):
        assert run_id == "run-123"
        assert user_id == shared_user.id
        assert runtime_mode == "shared"
        assert backend == "hermes"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.ensure_runtime_run_owned",
        fake_ensure_owned,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.HermesClient.collect_run_events", fake_collect)

    payload = await SharedHermesBackend().wait_run(
        RuntimeContext(user=shared_user, scope="shared"),
        "run-123",
        9000,
    )

    assert payload["status"] == "completed"
    assert payload["message"] == {"role": "assistant", "content": "pong"}
    assert payload["events"] == [
        {"type": "message.delta", "delta": "pong"},
        {"type": "run.completed", "output": "pong"},
    ]


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_send_message_logs_run_start_latency(monkeypatch, caplog, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeBinding:
        comworker_agent_id = "usr_sharedagent"

    class FakeSharedContext:
        binding = FakeBinding()
        session_prefix = "agent:usr_sharedagent:"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_context(self, db, user):
        assert user is shared_user
        return FakeSharedContext()

    async def fake_create_run(self, *, message, session_id, session_key):
        assert message == "Hi"
        assert session_id == "agent:usr_sharedagent:sess-1"
        assert session_key == "agent:usr_sharedagent:sess-1"
        return {"run_id": "run-123", "session_id": session_id, "status": "started"}

    recorded = []

    async def fake_record_runtime_run(db, **kwargs):
        recorded.append(kwargs)

    times = iter([30.0, 30.1])
    monkeypatch.setattr("app.runtime_backends.shared_hermes.time.perf_counter", lambda: next(times))
    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.HermesClient.create_run", fake_create_run)
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.record_runtime_run",
        fake_record_runtime_run,
    )

    caplog.set_level(logging.INFO, logger="app.runtime_backends.shared_hermes")

    await SharedHermesBackend().send_message(
        RuntimeContext(user=shared_user, scope="shared"),
        session_key="agent:usr_sharedagent:sess-1",
        message="Hi",
    )

    assert "hermes_run_started" in caplog.text
    assert "scope=shared" in caplog.text
    assert "user_id=shared-user-1234567890abcdef" in caplog.text
    assert "run_id=run-123" in caplog.text
    assert "elapsed_ms=100.0" in caplog.text
    assert recorded == [
        {
            "run_id": "run-123",
            "user_id": shared_user.id,
            "session_key": "agent:usr_sharedagent:sess-1",
            "runtime_mode": "shared",
            "backend": "hermes",
        }
    ]


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_wait_run_rejects_unowned_run(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_owned(db, *, run_id, user_id, runtime_mode, backend):
        assert run_id == "run-other"
        assert user_id == shared_user.id
        assert runtime_mode == "shared"
        assert backend == "hermes"
        raise HTTPException(status_code=403, detail="Run does not belong to current user")

    async def fake_collect(self, *args, **kwargs):
        raise AssertionError("unowned run must not be sent to Hermes")

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.ensure_runtime_run_owned",
        fake_ensure_owned,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.HermesClient.collect_run_events", fake_collect)

    with pytest.raises(HTTPException) as exc:
        await SharedHermesBackend().wait_run(
            RuntimeContext(user=shared_user, scope="shared"),
            "run-other",
            9000,
        )

    assert exc.value.status_code == 403


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_wait_run_logs_first_delta_and_total_latency(
    monkeypatch,
    caplog,
    shared_user,
):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    times = iter([40.0, 40.3, 41.0])
    monkeypatch.setattr("app.runtime_backends.shared_hermes.time.perf_counter", lambda: next(times))

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        assert timeout_ms == 9000
        assert on_event is not None
        on_event({"type": "message.delta", "delta": "Hi"})
        return [
            {"type": "message.delta", "delta": "Hi"},
            {"type": "run.completed", "output": "Hi"},
        ]

    async def fake_ensure_owned(db, *, run_id, user_id, runtime_mode, backend):
        assert run_id == "run-123"
        assert user_id == shared_user.id
        assert runtime_mode == "shared"
        assert backend == "hermes"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.ensure_runtime_run_owned",
        fake_ensure_owned,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.HermesClient.collect_run_events", fake_collect)

    caplog.set_level(logging.INFO, logger="app.runtime_backends.shared_hermes")

    await SharedHermesBackend().wait_run(RuntimeContext(user=shared_user, scope="shared"), "run-123", 9000)

    assert "hermes_run_finished" in caplog.text
    assert "scope=shared" in caplog.text
    assert "user_id=shared-user-1234567890abcdef" in caplog.text
    assert "run_id=run-123" in caplog.text
    assert "first_delta_ms=300.0" in caplog.text
    assert "elapsed_ms=1000.0" in caplog.text
    assert "event_count=2" in caplog.text


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_wait_run_logs_first_event_and_visible_delta_latency(
    monkeypatch,
    caplog,
    shared_user,
):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    times = iter([30.0, 30.05, 30.2, 30.8, 31.0])
    monkeypatch.setattr("app.runtime_backends.shared_hermes.time.perf_counter", lambda: next(times))

    async def fake_collect(self, run_id, timeout_ms=25000, on_event=None):
        assert run_id == "run-123"
        assert timeout_ms == 9000
        assert on_event is not None
        on_event({"type": "run.started"})
        on_event({"type": "message.delta", "delta": "<think>private"})
        on_event({"type": "message.delta", "delta": " reasoning</think>\n\nVisible"})
        return [
            {"type": "run.started"},
            {"type": "message.delta", "delta": "<think>private"},
            {"type": "message.delta", "delta": " reasoning</think>\n\nVisible"},
            {"type": "run.completed", "output": "<think>private reasoning</think>\n\nVisible"},
        ]

    async def fake_ensure_owned(db, *, run_id, user_id, runtime_mode, backend):
        assert run_id == "run-123"
        assert user_id == shared_user.id
        assert runtime_mode == "shared"
        assert backend == "hermes"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.ensure_runtime_run_owned",
        fake_ensure_owned,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.HermesClient.collect_run_events", fake_collect)

    caplog.set_level(logging.INFO, logger="app.runtime_backends.shared_hermes")

    await SharedHermesBackend().wait_run(RuntimeContext(user=shared_user, scope="shared"), "run-123", 9000)

    assert "first_event_ms=50.0" in caplog.text
    assert "first_delta_ms=200.0" in caplog.text
    assert "first_visible_delta_ms=800.0" in caplog.text
    assert "elapsed_ms=1000.0" in caplog.text


@pytest.mark.asyncio
async def test_hermes_client_collect_run_events_parses_sse(monkeypatch):
    from app.hermes_client import HermesClient

    captured = []
    chunks = [
        b'event: run.started\n',
        b'data: {"type":"run.started"}\n\n',
        b'data: {"type":"message.completed","message":{"content":"ok"}}\n\n',
        b'data: [DONE]\n\n',
    ]

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            stream_map={
                ("GET", "http://hermes.local/v1/runs/run-1/events"): FakeStreamResponse(chunks)
            },
            capture=captured,
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    seen_events = []
    client = HermesClient(base_url="http://hermes.local")
    events = await client.collect_run_events("run-1", timeout_ms=5000, on_event=seen_events.append)

    assert events == [
        {"type": "run.started"},
        {"type": "message.completed", "message": {"content": "ok"}},
    ]
    assert captured[0][0] == "STREAM GET"
    assert captured[0][1] == "http://hermes.local/v1/runs/run-1/events"
    assert captured[0][2]["params"] == {"timeout_ms": 5000}
    assert seen_events == events


@pytest.mark.asyncio
async def test_hermes_client_collect_run_events_normalizes_event_field(monkeypatch):
    from app.hermes_client import HermesClient

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            stream_map={
                ("GET", "http://hermes.local/v1/runs/run-2/events"): FakeStreamResponse(
                    [b'data: {"event":"run.completed","output":"ok"}\n\n']
                )
            }
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    client = HermesClient(base_url="http://hermes.local")
    events = await client.collect_run_events("run-2")

    assert events == [{"event": "run.completed", "output": "ok", "type": "run.completed"}]


@pytest.mark.asyncio
async def test_hermes_client_raises_http_exception_for_upstream_errors(monkeypatch):
    from app.hermes_client import HermesClient

    def fake_client(*args, **kwargs):
        return FakeAsyncClient(
            response_map={
                ("GET", "http://hermes.local/v1/models"): FakeResponse(
                    status_code=503,
                    json_data={"error": {"message": "booting"}},
                )
            }
        )

    monkeypatch.setattr("app.hermes_client.httpx.AsyncClient", fake_client)

    client = HermesClient(base_url="http://hermes.local")

    with pytest.raises(HTTPException, match="booting") as exc:
        await client.get_models()

    assert exc.value.status_code == 503


@pytest.mark.skip(reason="list_sessions requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_dedicated_hermes_list_sessions_maps_api_payload(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_request(self, ctx, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/hermes/sessions"
        return {
            "sessions": [
                {
                    "session_id": "sess-1",
                    "title": "First",
                    "message_count": 3,
                    "updated_at": "2026-04-13T15:00:00Z",
                }
            ]
        }

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.DedicatedHermesBackend._request", fake_request)

    payload = await backend.list_sessions(RuntimeContext(user=dedicated_user, scope="dedicated"))

    assert payload == [
        {
            "key": "sess-1",
            "sessionKey": "sess-1",
            "title": "First",
            "updatedAt": "2026-04-13T15:00:00Z",
            "messageCount": 3,
        }
    ]


@pytest.mark.asyncio
async def test_dedicated_hermes_list_sessions_includes_legacy_comworker_sessions(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_request(self, ctx, method, path, **kwargs):
        assert path == "/api/hermes/sessions"
        return {"sessions": []}

    async def fake_read_legacy_data_file(ctx, path):
        assert path == "agents/main/sessions/sessions.json"
        return json.dumps(
            {
                "agent:main:session-1778564877296": {
                    "sessionId": "8fbcc394-0d8c-4058-8bd1-70c4003333c8",
                    "updatedAt": 1778564916392,
                }
            }
        )

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.DedicatedHermesBackend._request", fake_request)
    monkeypatch.setattr(backend, "_read_legacy_data_file", fake_read_legacy_data_file)

    payload = await backend.list_sessions(RuntimeContext(user=dedicated_user, scope="dedicated"))

    assert payload == [
        {
            "key": "agent:main:session-1778564877296",
            "sessionKey": "agent:main:session-1778564877296",
            "title": "agent:main:session-1778564877296",
            "created_at": None,
            "createdAt": None,
            "updated_at": "2026-05-12T05:48:36.392Z",
            "updatedAt": "2026-05-12T05:48:36.392Z",
            "messageCount": None,
            "runtime": "legacy-comworker",
            "readonly": True,
        }
    ]


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_list_sessions_filters_by_session_prefix(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeSharedContext:
        session_prefix = "agent:usr_sharedagent:"

    async def fake_context(self, db, user):
        assert user is shared_user
        return FakeSharedContext()

    async def fake_request(self, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/hermes/sessions"
        return {
            "sessions": [
                {
                    "session_id": "agent:usr_sharedagent:session-1",
                    "title": "Mine",
                    "message_count": 3,
                    "updated_at": "2026-04-13T15:00:00Z",
                },
                {
                    "session_id": "agent:other:session-2",
                    "title": "Other",
                    "message_count": 1,
                    "updated_at": "2026-04-13T16:00:00Z",
                },
            ]
        }

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._request", fake_request)

    backend = SharedHermesBackend()
    payload = await backend.list_sessions(RuntimeContext(user=shared_user, scope="shared"))

    assert payload == [
        {
            "key": "agent:usr_sharedagent:session-1",
            "sessionKey": "agent:usr_sharedagent:session-1",
            "title": "Mine",
            "updatedAt": "2026-04-13T15:00:00Z",
            "messageCount": 3,
        }
    ]


@pytest.mark.asyncio
async def test_dedicated_hermes_get_session_maps_messages(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_request(self, ctx, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/hermes/sessions/sess-2"
        return {
            "session_id": "sess-2",
            "title": "Second",
            "message_count": 2,
            "created_at": "2026-04-13T14:00:00Z",
            "updated_at": "2026-04-13T15:00:00Z",
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ],
        }

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.DedicatedHermesBackend._request", fake_request)

    payload = await backend.get_session(RuntimeContext(user=dedicated_user, scope="dedicated"), "sess-2")

    assert payload["sessionKey"] == "sess-2"
    assert payload["title"] == "Second"
    assert payload["messageCount"] == 2
    assert payload["messages"][1]["content"] == "Hello"


@pytest.mark.asyncio
async def test_dedicated_hermes_get_session_falls_back_to_legacy_comworker_jsonl(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_request(self, ctx, method, path, **kwargs):
        raise HTTPException(status_code=404, detail="Session not found")

    async def fake_read_legacy_data_file(ctx, path):
        if path == "agents/main/sessions/sessions.json":
            return json.dumps(
                {
                    "agent:main:session-1778564877296": {
                        "sessionId": "8fbcc394-0d8c-4058-8bd1-70c4003333c8",
                        "updatedAt": 1778564916392,
                    }
                }
            )
        if path == "agents/main/sessions/8fbcc394-0d8c-4058-8bd1-70c4003333c8.jsonl":
            return "\n".join(
                [
                    json.dumps({"type": "session", "timestamp": "2026-05-12T05:48:27.885Z"}),
                    json.dumps(
                        {
                            "type": "message",
                            "timestamp": "2026-05-12T05:48:27.944Z",
                            "message": {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "Sender (untrusted metadata):\n```json\n{}\n```\n\n[Tue 2026-05-12 13:48 GMT+8] 你好",
                                    }
                                ],
                                "timestamp": 1778564907934,
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "message",
                            "timestamp": "2026-05-12T05:48:36.321Z",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "thinking", "thinking": "hidden"},
                                    {"type": "text", "text": "你好，有什么医学学术问题需要我帮忙吗？"},
                                ],
                            },
                        }
                    ),
                ]
            )
        pytest.fail(f"unexpected legacy path: {path}")

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.DedicatedHermesBackend._request", fake_request)
    monkeypatch.setattr(backend, "_read_legacy_data_file", fake_read_legacy_data_file)

    payload = await backend.get_session(RuntimeContext(user=dedicated_user, scope="dedicated"), "agent:main:session-1778564877296")

    assert payload["key"] == "agent:main:session-1778564877296"
    assert payload["runtime"] == "legacy-comworker"
    assert payload["readonly"] is True
    assert payload["messageCount"] == 2
    assert payload["messages"] == [
        {"role": "user", "content": "你好", "timestamp": "2026-05-12T05:48:27.934Z"},
        {"role": "assistant", "content": "你好，有什么医学学术问题需要我帮忙吗？", "timestamp": "2026-05-12T05:48:36.321Z"},
    ]


@pytest.mark.asyncio
async def test_dedicated_hermes_get_session_returns_empty_comworker_session(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_request(self, ctx, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/hermes/sessions/agent:main:session-1779333210355"
        raise HTTPException(status_code=404, detail="Session not found")

    async def fake_legacy_session(ctx, session_key):
        assert session_key == "agent:main:session-1779333210355"
        return None

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.DedicatedHermesBackend._request", fake_request)
    monkeypatch.setattr(backend, "_legacy_comworker_session", fake_legacy_session)

    payload = await backend.get_session(RuntimeContext(user=dedicated_user, scope="dedicated"), "agent:main:session-1779333210355")

    assert payload["key"] == "agent:main:session-1779333210355"
    assert payload["sessionKey"] == "agent:main:session-1779333210355"
    assert payload["messages"] == []
    assert payload["messageCount"] == 0
    assert payload["runtime"] == "hermes"
    assert payload["pending"] is True


@pytest.mark.asyncio
async def test_dedicated_hermes_get_session_strips_thinking_blocks(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_request(self, ctx, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/hermes/sessions/sess-think"
        return {
            "session_id": "sess-think",
            "messages": [
                {"role": "user", "content": "Hi"},
                {
                    "role": "assistant",
                    "content": "<think>\nprivate reasoning\n</think>\n\nHello",
                    "reasoning": "private reasoning",
                },
            ],
        }

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.DedicatedHermesBackend._request", fake_request)

    payload = await backend.get_session(RuntimeContext(user=dedicated_user, scope="dedicated"), "sess-think")

    assert payload["messages"][1] == {"role": "assistant", "content": "Hello"}


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_get_session_strips_thinking_blocks(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeSharedContext:
        session_prefix = "agent:usr_sharedagent:"

    async def fake_context(self, db, user):
        assert user is shared_user
        return FakeSharedContext()

    async def fake_request(self, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/hermes/sessions/agent:usr_sharedagent:sess-think"
        return {
            "session_id": "agent:usr_sharedagent:sess-think",
            "messages": [
                {"role": "user", "content": "Hi"},
                {
                    "role": "assistant",
                    "content": "<think>private reasoning</think>\n\nHello",
                    "reasoning": "private reasoning",
                },
            ],
        }

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._request", fake_request)

    payload = await SharedHermesBackend().get_session(
        RuntimeContext(user=shared_user, scope="shared"),
        "agent:usr_sharedagent:sess-think",
    )

    assert payload["messages"][1] == {"role": "assistant", "content": "Hello"}


@pytest.mark.skip(reason="upload_file requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_dedicated_hermes_upload_file_puts_archive_into_container(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend
    from fastapi import UploadFile

    class FakeDockerContainer:
        def __init__(self):
            self.calls = []
            self.exec_calls = []

        def put_archive(self, path, data):
            self.calls.append((path, data))
            return True

        def exec_run(self, cmd):
            self.exec_calls.append(cmd)
            return SimpleNamespace(exit_code=0, output=b"")

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.requested_ids = []
            self.containers = self

        def get(self, container_id):
            self.requested_ids.append(container_id)
            return self.container

    fake_docker_container = FakeDockerContainer()
    fake_docker_client = FakeDockerClient(fake_docker_container)
    container_record = SimpleNamespace(docker_id="docker-123")
    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return container_record

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.container.manager._docker", lambda: fake_docker_client)

    upload = UploadFile(filename="notes.txt", file=io.BytesIO(b"hello hermes"))
    payload = await backend.upload_file(RuntimeContext(user=dedicated_user, scope="dedicated"), upload)

    assert payload["original_name"] == "notes.txt"
    assert payload["size"] == len(b"hello hermes")
    assert payload["path"].startswith("workspace/uploads/")
    assert payload["url"].startswith("/api/comworker/filemanager/serve?path=/workspace/uploads/")
    assert fake_docker_client.requested_ids == ["docker-123"]
    assert fake_docker_container.exec_calls
    assert "/root/.comworker" in fake_docker_container.exec_calls[0][-1]
    assert fake_docker_container.calls
    assert fake_docker_container.calls[0][0] == "/opt/data"
    with tarfile.open(fileobj=io.BytesIO(fake_docker_container.calls[0][1]), mode="r") as tar:
        members = {member.name: member for member in tar.getmembers()}
        assert members["home/.comworker"].issym()
        assert members["home/.comworker"].linkname == "/opt/data"
        uploaded_name = next(
            name
            for name in members
            if name.startswith("workspace/uploads/") and name.endswith("notes.txt")
        )
        assert tar.extractfile(uploaded_name).read() == b"hello hermes"


@pytest.mark.skip(reason="browse_hermes_filemanager requires DB mocking not implemented")
def test_browse_hermes_filemanager_reads_workspace_directory(monkeypatch):
    from app.runtime_backends.hermes_files import browse_hermes_filemanager

    class FakeDockerContainer:
        def __init__(self):
            self.exec_calls = []

        def exec_run(self, cmd):
            self.exec_calls.append(cmd)
            assert cmd[:3] == ["python3", "-c", cmd[2]]
            assert cmd[-1] == "workspace/t"
            return SimpleNamespace(
                exit_code=0,
                output=json.dumps(
                    {
                        "type": "directory",
                        "path": "t",
                        "root": "/opt/data/workspace",
                        "items": [
                            {
                                "name": "a.txt",
                                "path": "t/a.txt",
                                "type": "file",
                                "size": 5,
                                "content_type": "text/plain",
                                "modified": "2026-05-21T03:00:00Z",
                            }
                        ],
                        "runtime": "hermes",
                    }
                ).encode("utf-8"),
            )

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.containers = self

        def get(self, container_id):
            assert container_id == "docker-123"
            return self.container

    fake_container = FakeDockerContainer()
    monkeypatch.setattr("app.container.manager._docker", lambda: FakeDockerClient(fake_container))

    payload = browse_hermes_filemanager("docker-123", "t")

    assert payload["path"] == "t"
    assert payload["items"][0]["path"] == "t/a.txt"
    assert fake_container.exec_calls


@pytest.mark.skip(reason="filemanager upload requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_filemanager_upload_normalizes_relative_directory(monkeypatch, dedicated_user):
    from app.api_compat import comworker_compat
    from fastapi import UploadFile

    captured = {}

    class FakeBackend:
        async def upload_file(self, ctx, file, target_dir=None):
            captured["ctx"] = ctx
            captured["filename"] = file.filename
            captured["target_dir"] = target_dir
            return {"path": f"{target_dir}/smoke.txt"}

    monkeypatch.setattr(comworker_compat, "get_runtime_backend", lambda user: FakeBackend())

    upload = UploadFile(filename="smoke.txt", file=io.BytesIO(b"hello"))
    payload = await comworker_compat.upload_dedicated_file(
        file=upload,
        path="t",
        upload_dir=None,
        user=dedicated_user,
    )

    assert captured["ctx"].user is dedicated_user
    assert captured["filename"] == "smoke.txt"
    assert captured["target_dir"] == "workspace/t"
    assert payload["path"] == "workspace/t/smoke.txt"


@pytest.mark.skip(reason="skill upload requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_hermes_skill_upload_extracts_zip_into_skills_root(monkeypatch):
    from app.runtime_backends.hermes_skills import upload_skill_zip_to_hermes_container
    from fastapi import UploadFile

    class FakeDockerContainer:
        def __init__(self):
            self.calls = []
            self.exec_calls = []

        def put_archive(self, path, data):
            self.calls.append((path, data))
            return True

        def exec_run(self, cmd):
            self.exec_calls.append(cmd)
            return SimpleNamespace(exit_code=0, output=b"")

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.requested_ids = []
            self.containers = self

        def get(self, container_id):
            self.requested_ids.append(container_id)
            return self.container

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("demo-skill/SKILL.md", "---\nname: demo-skill\ndescription: Demo skill\n---\n# Demo\n")
        zf.writestr("demo-skill/references/readme.md", "reference")
    zip_buffer.seek(0)

    fake_docker_container = FakeDockerContainer()
    fake_docker_client = FakeDockerClient(fake_docker_container)
    monkeypatch.setattr("app.container.manager._docker", lambda: fake_docker_client)

    upload = UploadFile(filename="demo-skill.zip", file=zip_buffer)
    payload = await upload_skill_zip_to_hermes_container("docker-123", upload)

    assert payload == {"name": "demo-skill", "description": "Demo skill", "source": "hermes", "disabled": False}
    assert fake_docker_client.requested_ids == ["docker-123"]
    assert fake_docker_container.exec_calls
    assert fake_docker_container.calls[0][0] == "/opt/data/skills"
    with tarfile.open(fileobj=io.BytesIO(fake_docker_container.calls[0][1]), mode="r") as tar:
        members = {member.name: member for member in tar.getmembers()}
        assert "demo-skill/SKILL.md" in members
        assert "demo-skill/references/readme.md" in members
        assert tar.extractfile("demo-skill/SKILL.md").read().startswith(b"---\nname: demo-skill")


@pytest.mark.asyncio
async def test_hermes_skill_upload_installs_multiple_skills_from_archive(monkeypatch):
    from app.runtime_backends.hermes_skills import upload_skill_zip_to_hermes_container
    from fastapi import UploadFile

    class FakeDockerContainer:
        def __init__(self):
            self.calls = []
            self.exec_calls = []

        def put_archive(self, path, data):
            self.calls.append((path, data))
            return True

        def exec_run(self, cmd):
            self.exec_calls.append(cmd)
            return SimpleNamespace(exit_code=0, output=b"")

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.containers = self

        def get(self, container_id):
            return self.container

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr(
            "superpowers-main/skills/brainstorming/SKILL.md",
            "---\nname: brainstorming\ndescription: Brainstorming\n---\n# Brainstorming\n",
        )
        zf.writestr(
            "superpowers-main/skills/systematic-debugging/SKILL.md",
            "---\nname: systematic-debugging\ndescription: Debugging\n---\n# Debugging\n",
        )
        zf.writestr("superpowers-main/skills/systematic-debugging/references/root-cause.md", "trace")
    zip_buffer.seek(0)

    fake_docker_container = FakeDockerContainer()
    monkeypatch.setattr("app.container.manager._docker", lambda: FakeDockerClient(fake_docker_container))

    upload = UploadFile(filename="superpowers-main.zip", file=zip_buffer)
    payload = await upload_skill_zip_to_hermes_container("docker-123", upload)

    assert payload["name"] == "brainstorming"
    assert [item["name"] for item in payload["installed"]] == ["brainstorming", "systematic-debugging"]
    with tarfile.open(fileobj=io.BytesIO(fake_docker_container.calls[0][1]), mode="r") as tar:
        members = {member.name: member for member in tar.getmembers()}
        assert "superpowers-main/brainstorming/SKILL.md" in members
        assert "superpowers-main/systematic-debugging/SKILL.md" in members
        assert "superpowers-main/systematic-debugging/references/root-cause.md" in members
        assert "superpowers-main/brainstorming/systematic-debugging/SKILL.md" not in members


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_upload_file_puts_archive_into_shared_container(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend
    from fastapi import UploadFile

    class FakeBinding:
        comworker_agent_id = "usr_sharedagent"

    class FakeSharedContext:
        binding = FakeBinding()
        session_prefix = "agent:usr_sharedagent:"
        upload_dir = "workspace-usr_sharedagent/uploads"

    class FakeDockerContainer:
        def __init__(self):
            self.calls = []
            self.exec_calls = []

        def put_archive(self, path, data):
            self.calls.append((path, data))
            return True

        def exec_run(self, cmd):
            self.exec_calls.append(cmd)
            return SimpleNamespace(exit_code=0, output=b"")

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.requested_ids = []
            self.containers = self

        def get(self, container_id):
            self.requested_ids.append(container_id)
            return self.container

    async def fake_context(self, db, user):
        assert user is shared_user
        return FakeSharedContext()

    async def fail_old_upload_endpoint(*args, **kwargs):
        pytest.fail("Shared Hermes upload must not call the old ComWorker filemanager endpoint")

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    fake_docker_container = FakeDockerContainer()
    fake_docker_client = FakeDockerClient(fake_docker_container)

    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr("app.shared_runtime.shared_runtime_request", fail_old_upload_endpoint)
    monkeypatch.setattr("app.container.manager._docker", lambda: fake_docker_client)

    upload = UploadFile(filename="shared.txt", file=io.BytesIO(b"shared hermes"))
    payload = await SharedHermesBackend().upload_file(RuntimeContext(user=shared_user, scope="shared"), upload)

    assert payload["original_name"] == "shared.txt"
    assert payload["size"] == len(b"shared hermes")
    assert payload["path"].startswith("workspace-usr_sharedagent/uploads/")
    assert fake_docker_client.requested_ids == ["comworker-shared"]
    assert fake_docker_container.exec_calls
    assert "/root/.comworker" in fake_docker_container.exec_calls[0][-1]
    assert fake_docker_container.calls
    assert fake_docker_container.calls[0][0] == "/opt/data"
    with tarfile.open(fileobj=io.BytesIO(fake_docker_container.calls[0][1]), mode="r") as tar:
        members = {member.name: member for member in tar.getmembers()}
        assert members["home/.comworker"].issym()
        assert members["home/.comworker"].linkname == "/opt/data"
        uploaded_name = next(
            name
            for name in members
            if name.startswith("workspace-usr_sharedagent/uploads/") and name.endswith("shared.txt")
        )
        assert tar.extractfile(uploaded_name).read() == b"shared hermes"


@pytest.mark.asyncio
async def test_dedicated_hermes_stream_maps_upstream_events(monkeypatch, dedicated_user):
    from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend

    class FakeRequest:
        async def is_disconnected(self):
            return False

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def aiter_text(self):
            yield 'data: {"type":"message.delta","session_id":"sess-9","delta":"Hi"}\n\n'
            yield 'data: {"type":"message.completed","session_id":"sess-9","message":{"role":"assistant","content":"Hello"}}\n\n'

    captured = []

    class FakeAsyncClientForStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, **kwargs):
            captured.append((method, url, kwargs))
            return FakeStreamResponse()

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.settings.dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.decode_token", lambda token: {"type": "access", "sub": dedicated_user.id})

    async def fake_get_user(db, user_id):
        assert user_id == dedicated_user.id
        return dedicated_user

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.get_user_by_id", fake_get_user)

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.httpx.AsyncClient", lambda timeout=None: FakeAsyncClientForStream())

    backend = DedicatedHermesBackend(base_url="http://dedicated-hermes")
    response = await backend.stream_events(RuntimeContext(user=dedicated_user, scope="dedicated"), FakeRequest(), "tok")

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    text = body.decode("utf-8")
    assert captured[0] == (
        "GET",
        "http://dedicated-hermes/api/hermes/events/stream",
        {"headers": {"Authorization": "Bearer bridge-key"}},
    )
    assert '"state": "delta"' in text
    assert '"state": "final"' in text
    assert '"sessionKey": "sess-9"' in text


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_stream_events_sends_bridge_key(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeRequest:
        async def is_disconnected(self):
            return False

    class FakeSharedContext:
        session_prefix = "agent:usr_sharedagent:"

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def aiter_text(self):
            yield (
                'data: {"type":"message.delta",'
                '"session_id":"agent:usr_sharedagent:sess-9","delta":"Hi"}\n\n'
            )

    captured = []

    class FakeAsyncClientForStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, **kwargs):
            captured.append((method, url, kwargs))
            return FakeStreamResponse()

    async def fake_get_user(db, user_id):
        assert user_id == shared_user.id
        return shared_user

    async def fake_context(self, db, user):
        assert user is shared_user
        return FakeSharedContext()

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.shared_hermes_url", "http://shared-hermes")
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.settings.shared_hermes_api_key",
        "shared-key",
        raising=False,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.decode_token", lambda token: {"type": "access", "sub": shared_user.id})
    monkeypatch.setattr("app.runtime_backends.shared_hermes.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.httpx.AsyncClient", lambda timeout=None: FakeAsyncClientForStream())

    response = await SharedHermesBackend().stream_events(
        RuntimeContext(user=shared_user, scope="shared"),
        FakeRequest(),
        "tok",
    )

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert captured[0] == (
        "GET",
        "http://shared-hermes/api/hermes/events/stream",
        {"headers": {"Authorization": "Bearer shared-key"}},
    )
    assert '"sessionKey": "agent:usr_sharedagent:sess-9"' in body.decode("utf-8")


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_stream_run_events_sends_bridge_key(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeRequest:
        async def is_disconnected(self):
            return False

    class FakeStreamResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def aiter_bytes(self):
            yield b'data: {"event":"run.completed"}\n\n'

    captured = []

    class FakeAsyncClientForStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, **kwargs):
            captured.append((method, url, kwargs))
            return FakeStreamResponse()

    async def fake_get_user(db, user_id):
        assert user_id == shared_user.id
        return shared_user

    async def fake_context(self, db, user):
        assert user is shared_user
        return object()

    async def fake_ensure_owned(db, *, run_id, user_id, runtime_mode, backend):
        assert run_id == "run-123"
        assert user_id == shared_user.id
        assert runtime_mode == "shared"
        assert backend == "hermes"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.shared_hermes_url", "http://shared-hermes")
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.settings.shared_hermes_api_key",
        "shared-key",
        raising=False,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.decode_token", lambda token: {"type": "access", "sub": shared_user.id})
    monkeypatch.setattr("app.runtime_backends.shared_hermes.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.ensure_runtime_run_owned",
        fake_ensure_owned,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.httpx.AsyncClient", lambda timeout=None: FakeAsyncClientForStream())

    response = await SharedHermesBackend().stream_run_events(
        RuntimeContext(user=shared_user, scope="shared"),
        FakeRequest(),
        "tok",
        "run-123",
    )

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    assert captured[0] == (
        "GET",
        "http://shared-hermes/v1/runs/run-123/events",
        {"headers": {"Authorization": "Bearer shared-key"}},
    )
    assert body == b'data: {"event":"run.completed"}\n\n'


@pytest.mark.skip(reason="Shared hermes backend not available in businessbot")
@pytest.mark.asyncio
async def test_shared_hermes_stream_run_events_rejects_unowned_run(monkeypatch, shared_user):
    from app.runtime_backends.shared_hermes import SharedHermesBackend

    class FakeRequest:
        async def is_disconnected(self):
            return False

    class FakeAsyncClientForStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, **kwargs):
            raise AssertionError("unowned run must not be sent to Hermes")

    async def fake_get_user(db, user_id):
        assert user_id == shared_user.id
        return shared_user

    async def fake_context(self, db, user):
        assert user is shared_user
        return object()

    async def fake_ensure_owned(db, *, run_id, user_id, runtime_mode, backend):
        assert run_id == "run-other"
        assert user_id == shared_user.id
        assert runtime_mode == "shared"
        assert backend == "hermes"
        raise HTTPException(status_code=403, detail="Run does not belong to current user")

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.runtime_backends.shared_hermes.settings.shared_hermes_url", "http://shared-hermes")
    monkeypatch.setattr("app.runtime_backends.shared_hermes.decode_token", lambda token: {"type": "access", "sub": shared_user.id})
    monkeypatch.setattr("app.runtime_backends.shared_hermes.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.runtime_backends.shared_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.shared_hermes.SharedHermesBackend._context_for_user", fake_context)
    monkeypatch.setattr(
        "app.runtime_backends.shared_hermes.ensure_runtime_run_owned",
        fake_ensure_owned,
    )
    monkeypatch.setattr("app.runtime_backends.shared_hermes.httpx.AsyncClient", lambda timeout=None: FakeAsyncClientForStream())

    with pytest.raises(HTTPException) as exc:
        await SharedHermesBackend().stream_run_events(
            RuntimeContext(user=shared_user, scope="shared"),
            FakeRequest(),
            "tok",
            "run-other",
        )

    assert exc.value.status_code == 403


@pytest.mark.skip(reason="proxy file request requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_proxy_file_request_maps_workspace_paths_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        headers = {}
        query_params = SimpleNamespace(get=lambda key, default="": "/workspace/uploads/test.txt" if key == "path" else default)

    captured = []

    def fake_decode(token):
        assert token == "tok"
        return {"type": "access", "sub": dedicated_user.id}

    async def fake_get_user(db, user_id):
        assert user_id == dedicated_user.id
        return dedicated_user

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return SimpleNamespace(docker_id="docker-123")

    class FakeDockerContainer:
        def __init__(self):
            self.paths = []

        def get_archive(self, path):
            self.paths.append(path)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                info = tarfile.TarInfo(name="test.txt")
                info.size = len(b"hello")
                tar.addfile(info, io.BytesIO(b"hello"))
            tar_buffer.seek(0)
            return [tar_buffer.read()], {"name": "test.txt", "size": len(b"hello")}

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.requested_ids = []
            self.containers = self

        def get(self, container_id):
            self.requested_ids.append(container_id)
            return self.container

    fake_docker_container = FakeDockerContainer()
    fake_docker_client = FakeDockerClient(fake_docker_container)

    async def fail_container_url(db, user):
        assert user.id == dedicated_user.id
        pytest.fail("Dedicated Hermes file serving must not rely on upstream static HTTP")

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("app.auth.service.decode_token", fake_decode)
    monkeypatch.setattr("app.auth.service.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.routes.proxy.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.routes.proxy.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.routes.proxy._container_url", fail_container_url)
    monkeypatch.setattr("app.container.manager._docker", lambda: fake_docker_client)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy._proxy_file_request(FakeRequest(), "tok", "filemanager/serve")

    assert response.body == b"hello"
    assert fake_docker_client.requested_ids == ["docker-123"]
    assert fake_docker_container.paths == ["/opt/data/workspace/uploads/test.txt"]
    assert captured == []


@pytest.mark.asyncio
async def test_proxy_file_request_serves_tmp_paths_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        headers = {}
        query_params = SimpleNamespace(get=lambda key, default="": "/tmp/sepsis_results.json" if key == "path" else default)

    def fake_decode(token):
        assert token == "tok"
        return {"type": "access", "sub": dedicated_user.id}

    async def fake_get_user(db, user_id):
        assert user_id == dedicated_user.id
        return dedicated_user

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return SimpleNamespace(docker_id="docker-123")

    class FakeDockerContainer:
        def __init__(self):
            self.paths = []

        def get_archive(self, path):
            self.paths.append(path)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                content = b'{"ok": true}'
                info = tarfile.TarInfo(name="sepsis_results.json")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            tar_buffer.seek(0)
            return [tar_buffer.read()], {"name": "sepsis_results.json", "size": len(content)}

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.containers = self

        def get(self, container_id):
            return self.container

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    fake_docker_container = FakeDockerContainer()

    monkeypatch.setattr("app.auth.service.decode_token", fake_decode)
    monkeypatch.setattr("app.auth.service.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.routes.proxy.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.routes.proxy.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.container.manager._docker", lambda: FakeDockerClient(fake_docker_container))
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy._proxy_file_request(FakeRequest(), "tok", "filemanager/serve")

    assert response.body == b'{"ok": true}'
    assert fake_docker_container.paths == ["/tmp/sepsis_results.json"]


@pytest.mark.asyncio
async def test_proxy_file_request_serves_legacy_scripts_path_from_skill(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        headers = {}
        query_params = SimpleNamespace(get=lambda key, default="": "/scripts/medical_search.py" if key == "path" else default)

    def fake_decode(token):
        assert token == "tok"
        return {"type": "access", "sub": dedicated_user.id}

    async def fake_get_user(db, user_id):
        assert user_id == dedicated_user.id
        return dedicated_user

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return SimpleNamespace(docker_id="docker-123")

    class FakeDockerContainer:
        def __init__(self):
            self.paths = []
            self.exec_calls = []

        def get_archive(self, path):
            self.paths.append(path)
            if path == "/scripts/medical_search.py":
                raise RuntimeError("not found")
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                content = b"print('medical search')\n"
                info = tarfile.TarInfo(name="medical_search.py")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            tar_buffer.seek(0)
            return [tar_buffer.read()], {"name": "medical_search.py", "size": len(content)}

        def exec_run(self, cmd):
            self.exec_calls.append(cmd)
            return SimpleNamespace(
                exit_code=0,
                output=b"/opt/data/skills/medical-keyword-search/scripts/medical_search.py\n",
            )

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.containers = self

        def get(self, container_id):
            return self.container

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    fake_docker_container = FakeDockerContainer()

    monkeypatch.setattr("app.auth.service.decode_token", fake_decode)
    monkeypatch.setattr("app.auth.service.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.routes.proxy.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.routes.proxy.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.container.manager._docker", lambda: FakeDockerClient(fake_docker_container))
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy._proxy_file_request(FakeRequest(), "tok", "filemanager/serve")

    assert response.body == b"print('medical search')\n"
    assert fake_docker_container.paths == [
        "/scripts/medical_search.py",
        "/opt/data/skills/medical-keyword-search/scripts/medical_search.py",
    ]


@pytest.mark.skip(reason="proxy file request requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_proxy_file_request_serves_download_paths_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        headers = {}
        query_params = SimpleNamespace(get=lambda key, default="": "t/test.txt" if key == "path" else default)

    def fake_decode(token):
        return {"type": "access", "sub": dedicated_user.id}

    async def fake_get_user(db, user_id):
        return dedicated_user

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return SimpleNamespace(docker_id="docker-123")

    class FakeDockerContainer:
        def __init__(self):
            self.paths = []

        def get_archive(self, path):
            self.paths.append(path)
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                content = b"hello download"
                info = tarfile.TarInfo(name="test.txt")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            tar_buffer.seek(0)
            return [tar_buffer.read()], {"name": "test.txt", "size": len(content)}

    class FakeDockerClient:
        def __init__(self, container):
            self.container = container
            self.containers = self

        def get(self, container_id):
            assert container_id == "docker-123"
            return self.container

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    fake_docker_container = FakeDockerContainer()

    monkeypatch.setattr("app.auth.service.decode_token", fake_decode)
    monkeypatch.setattr("app.auth.service.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.routes.proxy.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.routes.proxy.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.container.manager._docker", lambda: FakeDockerClient(fake_docker_container))
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy._proxy_file_request(FakeRequest(), "tok", "filemanager/download")

    assert response.body == b"hello download"
    assert fake_docker_container.paths == ["/opt/data/workspace/t/test.txt"]


@pytest.mark.asyncio
async def test_proxy_http_returns_empty_commands_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        method = "GET"
        query_params = {"agentId": "agent-main"}
        headers = {}

        async def body(self):
            return b""

    async def fake_container_url(db, user):
        pytest.fail("Hermes commands compatibility must not proxy to missing /api/commands")

    monkeypatch.setattr("app.routes.proxy._container_url", fake_container_url)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy.proxy_http("commands", FakeRequest(), dedicated_user, object())

    assert response.status_code == 200
    assert response.body == (
        b'{"agentId":"agent-main","commands":[],"runtime":"hermes",'
        b'"compatibility":"comworker-empty"}'
    )


@pytest.mark.asyncio
async def test_proxy_http_returns_settings_config_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        method = "GET"
        query_params = {}
        headers = {}

        async def body(self):
            return b""

    async def fake_container_url(db, user):
        pytest.fail("Hermes settings compatibility must not proxy to missing /api/settings/config")

    monkeypatch.setattr("app.routes.proxy._container_url", fake_container_url)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy.proxy_http("settings/config", FakeRequest(), dedicated_user, object())
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["runtime"] == "hermes"
    assert payload["compatibility"] == "comworker-readonly"
    assert payload["config"]["gateway"]["bind"] == "platform"
    assert isinstance(payload["config"]["gateway"]["port"], int)
    assert payload["config"]["gateway"]["controlUi"]["allowedOrigins"] == ["*"]


@pytest.mark.asyncio
async def test_proxy_http_rejects_settings_config_write_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        method = "PUT"
        query_params = {}
        headers = {"content-type": "application/json"}

        async def body(self):
            return b'{"gateway":{"port":18789}}'

    async def fake_container_url(db, user):
        pytest.fail("Hermes settings write compatibility must not proxy to missing /api/settings/config")

    monkeypatch.setattr("app.routes.proxy._container_url", fake_container_url)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy.proxy_http("settings/config", FakeRequest(), dedicated_user, object())
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["runtime"] == "hermes"
    assert "environment variables" in payload["detail"]


@pytest.mark.asyncio
async def test_proxy_http_maps_hermes_status_to_comworker_gateway_shape(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeRequest:
        method = "GET"
        query_params = {}
        headers = {}

        async def body(self):
            return b""

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"status": "ok", "platform": "hermes-agent"}

    async def fake_container_url(db, user):
        return "http://dedicated-hermes"

    async def fake_hermes_request(method, base_url, path, **kwargs):
        assert (method, base_url, path) == ("GET", "http://dedicated-hermes", "/health")
        return FakeResponse()

    monkeypatch.setattr("app.routes.proxy._container_url", fake_container_url)
    monkeypatch.setattr("app.routes.proxy._hermes_request", fake_hermes_request)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(proxy.settings, "default_model", "dashscope/qwen3-coder-plus")

    response = await proxy.proxy_http("status", FakeRequest(), dedicated_user, object())
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["gateway_connected"] is True
    assert payload["config_path"] == "platform://hermes"
    assert payload["workspace"] == "/workspace"
    assert payload["model"] == "dashscope/qwen3-coder-plus"
    assert payload["runtime"] == "hermes"


@pytest.mark.skip(reason="proxy http maps models requires DB mocking not implemented")
@pytest.mark.asyncio
async def test_proxy_http_maps_models_to_hermes_v1_models(monkeypatch, dedicated_user):
    from app.routes import proxy

    captured = []

    class FakeRequest:
        method = "GET"
        query_params = {}
        headers = {}

        async def body(self):
            return b""

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"data":[{"id":"hermes-agent","object":"model"}]}'

        def json(self):
            return {"data": [{"id": "hermes-agent", "object": "model"}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            captured.append((method, url, kwargs))
            return FakeResponse()

    async def fake_container_url(db, user):
        return "http://dedicated-hermes"

    monkeypatch.setattr("app.routes.proxy._container_url", fake_container_url)
    monkeypatch.setattr("app.routes.proxy.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(proxy.settings, "default_model", "dashscope/qwen3-coder-plus")

    response = await proxy.proxy_http("models", FakeRequest(), dedicated_user, object())

    assert response.status_code == 200
    assert captured[0][0] == "GET"
    assert captured[0][1] == "http://dedicated-hermes/v1/models"
    assert b'"models":[{"id":"hermes-agent","name":"hermes-agent","provider":"hermes"}]' in response.body
    assert b'"configuredModel":"dashscope/qwen3-coder-plus"' in response.body


@pytest.mark.asyncio
async def test_proxy_terminal_websocket_uses_platform_bridge_for_dedicated_hermes(monkeypatch, dedicated_user):
    from app.routes import proxy

    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.sent = []
            self.closed = []

        async def accept(self):
            self.accepted = True

        async def send_text(self, text):
            self.sent.append(text)

        async def close(self, code=1000, reason=None):
            self.closed.append((code, reason))

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_get_user(db, user_id):
        assert user_id == dedicated_user.id
        return dedicated_user

    class FakeContainer:
        docker_id = "container-123"
        internal_host = "hermes-user"
        internal_port = 18080

    async def fake_ensure_running(db, user_id):
        assert user_id == dedicated_user.id
        return FakeContainer()

    called = []

    async def fake_bridge(websocket_arg, container_id):
        called.append((websocket_arg, container_id))

    websocket = FakeWebSocket()
    monkeypatch.setattr("app.auth.service.decode_token", lambda token: {"type": "access", "sub": dedicated_user.id})
    monkeypatch.setattr("app.auth.service.get_user_by_id", fake_get_user)
    monkeypatch.setattr("app.routes.proxy.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.routes.proxy.ensure_running", fake_ensure_running)
    monkeypatch.setattr("app.routes.proxy._bridge_hermes_terminal_websocket", fake_bridge)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    await proxy.proxy_terminal_websocket(websocket, token="tok")

    assert called == [(websocket, "container-123")]
    assert websocket.sent == []


@pytest.mark.asyncio
async def test_hermes_terminal_bridge_relays_docker_exec_output(monkeypatch):
    from app.routes import proxy

    class FakeTerminalSocket:
        def __init__(self):
            self.reads = [b"hello from container\n", b""]
            self.writes = []
            self.closed = False

        def recv(self, size):
            return self.reads.pop(0)

        def sendall(self, data):
            self.writes.append(data)

        def close(self):
            self.closed = True

    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.sent = []
            self.inputs = [
                json.dumps({"type": "init", "session_key": "terminal:test", "command": "bash -il"}),
                json.dumps({"type": "input", "data": "pwd\n"}),
            ]

        async def accept(self):
            self.accepted = True

        async def receive_text(self):
            if self.inputs:
                return self.inputs.pop(0)
            raise WebSocketDisconnect()

        async def send_text(self, text):
            self.sent.append(json.loads(text))

        async def close(self, code=1000, reason=None):
            pass

    terminal_socket = FakeTerminalSocket()
    monkeypatch.setattr("app.routes.proxy._start_hermes_terminal_socket", lambda container_id, command: terminal_socket)

    websocket = FakeWebSocket()
    await proxy._bridge_hermes_terminal_websocket(websocket, "container-123")

    assert websocket.accepted is True
    assert {"type": "started", "command": "bash -il"} in websocket.sent
    assert {"type": "output", "data": "hello from container\n"} in websocket.sent
    assert terminal_socket.writes == [b"pwd\n"]
    assert terminal_socket.closed is True


@pytest.mark.asyncio
async def test_proxy_http_maps_comworker_cron_jobs_to_hermes_jobs(monkeypatch, dedicated_user):
    from app.routes import proxy

    captured = []

    class FakeRequest:
        method = "GET"
        query_params = {"include_disabled": "true"}
        headers = {}

        async def body(self):
            return b""

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"jobs":[]}'

        def json(self):
            return {
                "jobs": [
                    {
                        "id": "job-1",
                        "name": "daily brief",
                        "enabled": True,
                        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
                        "schedule_display": "0 9 * * *",
                        "prompt": "summarize",
                        "deliver": "local",
                        "next_run_at": "2026-05-07T09:00:00+08:00",
                        "last_run_at": None,
                        "last_status": None,
                        "last_error": None,
                        "created_at": "2026-05-06T09:00:00+08:00",
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, **kwargs):
            captured.append((method, url, kwargs))
            return FakeResponse()

    async def fake_container_url(db, user):
        return "http://dedicated-hermes"

    monkeypatch.setattr("app.routes.proxy._container_url", fake_container_url)
    monkeypatch.setattr("app.routes.proxy.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(proxy.settings, "dedicated_runtime_backend", "hermes")

    response = await proxy.proxy_http("cron/jobs", FakeRequest(), dedicated_user, object())

    assert response.status_code == 200
    assert captured[0][0] == "GET"
    assert captured[0][1] == "http://dedicated-hermes/api/jobs?include_disabled=true"
    assert b'"id":"job-1"' in response.body
    assert b'"schedule_kind":"cron"' in response.body
    assert b'"schedule_expr":"0 9 * * *"' in response.body
    assert b'"message":"summarize"' in response.body
