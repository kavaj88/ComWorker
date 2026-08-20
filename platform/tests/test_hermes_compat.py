import sys
import types

from fastapi import UploadFile
import pytest
from fastapi import HTTPException
from app.db.models import User
from app.runtime.event_translator import hermes_event_to_comworker_sse
from app.runtime.run_mapper import normalize_platform_run_id
from app.runtime.session_mapper import normalize_platform_session_key
from app.runtime_backend import RuntimeContext

if "docker" not in sys.modules:
    docker_stub = types.ModuleType("docker")
    docker_stub.DockerClient = object
    docker_stub.from_env = lambda: None
    docker_stub.types = types.SimpleNamespace(Mount=lambda *args, **kwargs: None)
    docker_stub.models = types.SimpleNamespace(containers=types.SimpleNamespace(Container=object))
    sys.modules["docker"] = docker_stub

    errors_module = types.ModuleType("docker.errors")
    errors_module.APIError = RuntimeError
    errors_module.NotFound = RuntimeError
    sys.modules["docker.errors"] = errors_module


def make_user(runtime_mode: str = "dedicated") -> User:
    return User(
        id="u1",
        username="tester",
        email="tester@example.com",
        password_hash="x",
        runtime_mode=runtime_mode,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_comworker_skills_route_uses_runtime_backend(monkeypatch):
    from app.api_compat import comworker_compat

    class FakeBackend:
        async def list_skills(self, ctx: RuntimeContext):
            assert ctx.user.username == "tester"
            assert ctx.scope == "dedicated"
            return [{"name": "dogfood", "description": "QA testing", "source": "hermes"}]

    monkeypatch.setattr(comworker_compat, "get_runtime_backend", lambda: FakeBackend())

    payload = await comworker_compat.list_dedicated_skills(make_user())

    assert payload == [{"name": "dogfood", "description": "QA testing", "source": "hermes"}]


@pytest.mark.asyncio
async def test_comworker_prewarm_route_uses_runtime_backend(monkeypatch):
    from app.api_compat import comworker_compat

    class FakeBackend:
        async def prewarm(self, ctx: RuntimeContext):
            assert ctx.user.username == "tester"
            assert ctx.scope == "dedicated"
            return {"ok": True, "status": "ready"}

    monkeypatch.setattr(comworker_compat, "get_runtime_backend", lambda: FakeBackend())

    payload = await comworker_compat.prewarm_dedicated_runtime(make_user())

    assert payload == {"ok": True, "status": "ready"}


@pytest.mark.skip(reason="Requires full Docker container mocking not available")
@pytest.mark.asyncio
async def test_comworker_agent_files_route_reads_packaged_agent_files(monkeypatch, tmp_path):
    from app.api_compat import comworker_compat
    from app.runtime_backends import hermes_agents

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_running(db, user_id):
        return types.SimpleNamespace(docker_id="docker-test")

    monkeypatch.setattr(comworker_compat, "async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(comworker_compat, "ensure_running", fake_ensure_running)

    deploy_copy = tmp_path / "deploy_copy"
    agent_dir = deploy_copy / "Agents" / "main"
    agent_dir.mkdir(parents=True)
    soul = agent_dir / "SOUL.md"
    soul.write_text("你是 Medclaw 助手\n", encoding="utf-8")
    (agent_dir / "IDENTITY.md").write_text("name: main\n", encoding="utf-8")

    monkeypatch.setattr(hermes_agents, "_deploy_copy_dir", lambda: deploy_copy)

    files_payload = await comworker_compat.list_dedicated_agent_files("main", make_user())
    content_payload = await comworker_compat.get_dedicated_agent_file("main", "SOUL.md", make_user())

    assert files_payload["agentId"] == "main"
    assert files_payload["workspace"] == "Agents/main"
    assert {item["name"] for item in files_payload["files"]} == {"IDENTITY.md", "SOUL.md"}
    assert content_payload == {
        "agentId": "main",
        "workspace": "Agents/main",
        "file": {"name": "SOUL.md", "content": "你是 Medclaw 助手\n"},
    }


@pytest.mark.asyncio
async def test_comworker_skill_upload_route_uses_hermes_container(monkeypatch):
    from app.api_compat import comworker_compat

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_running(db, user_id):
        assert user_id == "u1"
        return types.SimpleNamespace(docker_id="docker-123")

    async def fake_upload(container_id, file):
        assert container_id == "docker-123"
        assert file.filename == "demo-skill.zip"
        return {"name": "demo-skill", "description": "Demo", "source": "hermes", "disabled": False}

    monkeypatch.setattr(comworker_compat, "async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(comworker_compat, "ensure_running", fake_ensure_running)
    monkeypatch.setattr(comworker_compat, "upload_skill_zip_to_hermes_container", fake_upload)

    payload = await comworker_compat.upload_dedicated_skill_zip(
        UploadFile(filename="demo-skill.zip", file=types.SimpleNamespace()),
        make_user(),
    )

    assert payload == {"name": "demo-skill", "description": "Demo", "source": "hermes", "disabled": False}


@pytest.mark.asyncio
async def test_comworker_skill_search_route_matches_installed_hermes_skill_paths(monkeypatch):
    from app.api_compat import comworker_compat

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_running(db, user_id):
        assert user_id == "u1"
        return types.SimpleNamespace(docker_id="docker-123")

    def fake_list_skills(container_id):
        assert container_id == "docker-123"
        return [
            {
                "name": "brainstorming",
                "description": "Explores user intent",
                "source": "hermes",
                "disabled": False,
                "path": "superpowers-main/brainstorming",
            }
        ]

    monkeypatch.setattr(comworker_compat, "async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr(comworker_compat, "ensure_running", fake_ensure_running)
    monkeypatch.setattr(comworker_compat, "list_skills_from_hermes_container", fake_list_skills)

    payload = await comworker_compat.search_dedicated_skills(
        comworker_compat.SkillSearchRequest(query="super", limit=10),
        make_user(),
    )

    assert payload["results"] == [
        {
            "slug": "brainstorming",
            "url": "local://superpowers-main/brainstorming",
            "installs": "installed",
            "description": "Explores user intent",
            "source": "hermes",
            "path": "superpowers-main/brainstorming",
        }
    ]


def test_normalize_platform_session_key_preserves_existing_key():
    key = "agent:main:session-123"
    assert normalize_platform_session_key(key) == key


def test_normalize_platform_session_key_generates_default_when_missing():
    key = normalize_platform_session_key(None)
    assert key.startswith("agent:main:session-")
    assert len(key) > len("agent:main:session-")


def test_normalize_platform_run_id_preserves_existing_id():
    run_id = "run_abc123"
    assert normalize_platform_run_id(run_id) == run_id


def test_normalize_platform_run_id_generates_default_when_missing():
    run_id = normalize_platform_run_id(None)
    assert run_id.startswith("run_")
    assert len(run_id) > len("run_")


def test_hermes_event_to_comworker_sse_translates_delta_event():
    payload = {
        "type": "response.output_text.delta",
        "delta": "Hello",
        "run_id": "hermes-run-1",
    }

    sse = hermes_event_to_comworker_sse(payload, session_key="agent:main:session-1", platform_run_id="run_1")

    assert sse.startswith("data: ")
    assert '"event": "chat"' in sse
    assert '"state": "delta"' in sse
    assert '"sessionKey": "agent:main:session-1"' in sse
    assert '"runId": "run_1"' in sse
    assert '"text": "Hello"' in sse


def test_hermes_event_to_comworker_sse_translates_completed_event():
    payload = {
        "type": "response.completed",
        "run_id": "hermes-run-1",
    }

    sse = hermes_event_to_comworker_sse(payload, session_key="agent:main:session-1", platform_run_id="run_1")

    assert '"state": "final"' in sse


def test_hermes_event_to_comworker_sse_ignores_unknown_event_without_text():
    payload = {"type": "response.unknown"}
    assert hermes_event_to_comworker_sse(payload, session_key="agent:main:session-1", platform_run_id="run_1") is None
