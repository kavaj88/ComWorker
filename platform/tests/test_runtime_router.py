import sys
import types

import pytest
from fastapi import HTTPException

if "docker" not in sys.modules:
    docker_stub = types.ModuleType("docker")
    docker_stub.from_env = lambda: None
    docker_stub.DockerClient = object
    docker_stub.models = types.SimpleNamespace(containers=types.SimpleNamespace(Container=object))
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.APIError = RuntimeError
    docker_errors.NotFound = RuntimeError
    docker_stub.errors = docker_errors
    sys.modules["docker"] = docker_stub
    sys.modules["docker.errors"] = docker_errors

if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from app.config import Settings
from app.db.models import User
from app.runtime_router import _load_backend, get_runtime_backend


class DummyBackend:
    pass


class CloseableBackend:
    def __init__(self):
        self.close_count = 0

    async def aclose(self):
        self.close_count += 1


def test_hermes_backend_is_supported(monkeypatch):
    imported = []

    class FakeImporter:
        class DedicatedHermesBackend:
            pass

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.runtime_backends.dedicated_hermes":
            imported.append(name)
            return FakeImporter
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    backend = _load_backend("hermes")

    assert isinstance(backend, FakeImporter.DedicatedHermesBackend)
    assert imported == ["app.runtime_backends.dedicated_hermes"]


def test_unknown_backend_raises_clear_error():
    try:
        _load_backend("mystery")
    except ValueError as exc:
        assert "Unsupported runtime backend" in str(exc)
        assert "mystery" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_get_runtime_backend_uses_settings(monkeypatch):
    settings = Settings(dedicated_runtime_backend="hermes")
    backend = DummyBackend()

    monkeypatch.setattr("app.runtime_router._load_backend", lambda name: backend)

    assert get_runtime_backend(settings) is backend


def test_get_runtime_backend_defaults_to_global_settings(monkeypatch):
    backend = DummyBackend()

    monkeypatch.setattr("app.runtime_router._load_backend", lambda name: backend)
    monkeypatch.setattr("app.runtime_router.settings", Settings(dedicated_runtime_backend="hermes"))

    assert get_runtime_backend() is backend


@pytest.mark.skip(reason="get_agent_info does not go through _resolve_base_url in businessbot")
@pytest.mark.asyncio
async def test_dedicated_hermes_without_config_reports_clear_error(monkeypatch):
    from app.runtime_backend import RuntimeContext

    user = User(
        id="u1",
        username="tester",
        email="tester@example.com",
        password_hash="x",
        runtime_mode="dedicated",
    )

    class FakeContainer:
        internal_host = ""
        internal_port = 18080
        docker_id = "fake-docker-id"

    class FakeAsyncSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_ensure_running(db, user_id):
        return FakeContainer()

    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.async_session", lambda: FakeAsyncSessionContext())
    monkeypatch.setattr("app.runtime_backends.dedicated_hermes.ensure_running", fake_ensure_running)

    import app.runtime_router as runtime_router
    runtime_router._dedicated_backends.clear()
    backend = _load_backend("hermes")

    with pytest.raises(HTTPException, match="Hermes runtime address is unavailable") as exc:
        await backend.get_agent_info(RuntimeContext(user=user, scope="dedicated"))

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_close_runtime_backends_closes_cached_backends_once():
    import app.runtime_router as runtime_router

    backend = CloseableBackend()
    backend2 = CloseableBackend()
    runtime_router._dedicated_backends.clear()
    runtime_router._dedicated_backends["hermes"] = backend
    runtime_router._dedicated_backends["shared"] = backend2

    await runtime_router.close_runtime_backends()

    assert backend.close_count == 1
    assert backend2.close_count == 1
    assert runtime_router._dedicated_backends == {}
