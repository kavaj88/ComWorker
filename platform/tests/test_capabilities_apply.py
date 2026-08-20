"""Tests for the hot-patch capability apply path (#05).

Covers:
- ``_read_existing_capability_keys`` reads user-provided keys (capability-keyed).
- ``_write_hermes_runtime_files`` preserves user ``web.backend`` while a
  capability stays enabled, and drops it when revoked.
- ``apply_container_capabilities`` preserves a user key (even with no platform
  key), restarts the container, and refuses non-running / missing containers.
- apply + bulk-apply admin routes translate results to HTTP status and audit.
"""

from __future__ import annotations

import io
import sys
import tarfile
import types
from types import SimpleNamespace

import pytest

# Stub docker + asyncpg so platform modules import without a daemon.
if "docker" not in sys.modules:
    _docker = types.ModuleType("docker")
    _docker.DockerClient = object
    _docker.from_env = lambda: None
    _docker.models = types.SimpleNamespace(containers=types.SimpleNamespace(Container=object))
    _docker.types = types.SimpleNamespace(Mount=lambda *a, **k: None)
    sys.modules["docker"] = _docker
    _docker_errors = types.ModuleType("docker.errors")
    _docker_errors.APIError = RuntimeError
    _docker_errors.NotFound = RuntimeError
    sys.modules["docker.errors"] = _docker_errors
if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from fastapi import HTTPException  # noqa: E402

from app.container import manager  # noqa: E402


# ----------------------------- helpers ---------------------------------------


def _extract_archive(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for member in tar.getmembers():
            f = tar.extractfile(member)
            out[member.name] = f.read().decode("utf-8") if f else ""
    return out


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return self._rows


class ApplyFakeDB:
    """Queue-backed fake session: each execute() pops the next prepared result."""

    def __init__(self, results=()):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        if self._results:
            return self._results.pop(0)
        return _ScalarsResult([])

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True


class FakeContainer:
    def __init__(self, env_text="", config_text="", status="running", docker_id="abc123"):
        self.id = docker_id
        self.status = status
        self.attrs = {"Mounts": [{"Type": "volume", "Name": "vol-x", "Destination": "/opt/data"}]}
        self._env = env_text
        self._config = config_text
        self.restarted = False
        self.archives = []

    def reload(self):
        pass

    def restart(self, **kwargs):
        self.restarted = True

    def exec_run(self, cmd, user=None):
        if cmd == ["cat", "/opt/data/.env"]:
            return SimpleNamespace(exit_code=0, output=self._env.encode("utf-8"))
        if cmd == ["cat", "/opt/data/config.yaml"]:
            return SimpleNamespace(exit_code=0, output=self._config.encode("utf-8"))
        return SimpleNamespace(exit_code=0, output=b"")

    def put_archive(self, path, data):
        self.archives.append((path, data))
        return True


class FakeDockerClient:
    def __init__(self, container):
        self._container = container

    @property
    def containers(self):
        outer = self

        class _Containers:
            def get(self, id_or_name):
                return outer._container

            def run(self, **kwargs):
                return SimpleNamespace(id="chown-repair")

        return _Containers()


def _patch_hermes_settings(monkeypatch, tavily=""):
    monkeypatch.setattr(manager.settings, "tavily_api_key", tavily)
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "terminal,file,skills")


# --------------------- _read_existing_capability_keys ------------------------


def test_read_capability_keys_extracts_user_tavily_key():
    container = FakeContainer(env_text="API_SERVER_KEY=x\nTAVILY_API_KEY=tvly-user\nFEISHU_TOKEN=t\n")
    keys = manager._read_existing_capability_keys(container)
    # capability-keyed (not env-keyed) so it feeds build_capability_plan directly
    assert keys == {"web_search": "tvly-user"}


def test_read_capability_keys_empty_when_no_env_file():
    container = FakeContainer(env_text="")
    container.exec_run = lambda cmd, user=None: SimpleNamespace(exit_code=1, output=b"")
    assert manager._read_existing_capability_keys(container) == {}


# ------------------- _write_hermes_runtime_files preserve -------------------


def test_write_files_preserves_user_web_backend_while_enabled(monkeypatch):
    from app.capabilities import build_capability_plan

    _patch_hermes_settings(monkeypatch, tavily="tvly-platform")
    monkeypatch.setattr(manager, "_docker", lambda: FakeDockerClient(FakeContainer()))
    monkeypatch.setattr(manager, "_runtime_image", lambda: "img:latest")

    plan = build_capability_plan(
        manager.settings,
        user_caps={"web_search": SimpleNamespace(enabled=True)},
    )
    # existing container has a user-customized web backend
    container = FakeContainer(config_text="web:\n  backend: serper\n")
    manager._write_hermes_runtime_files(container, plan)

    files = _extract_archive(container.archives[-1][1])
    cfg = files["config.yaml"]
    assert "backend: serper" in cfg          # user value preserved
    assert "backend: tavily" not in cfg       # platform default NOT forced back


def test_write_files_drops_web_section_when_revoked(monkeypatch):
    from app.capabilities import build_capability_plan

    _patch_hermes_settings(monkeypatch, tavily="tvly-platform")
    monkeypatch.setattr(manager, "_docker", lambda: FakeDockerClient(FakeContainer()))
    monkeypatch.setattr(manager, "_runtime_image", lambda: "img:latest")

    # revoked: per-user enabled=False beats the platform default
    plan = build_capability_plan(
        manager.settings,
        user_caps={"web_search": SimpleNamespace(enabled=False)},
        platform_defaults={"web_search": SimpleNamespace(default_inject=True)},
    )
    container = FakeContainer(
        env_text="TAVILY_API_KEY=tvly-user\n",
        config_text="web:\n  backend: tavily\n",
    )
    manager._write_hermes_runtime_files(container, plan)

    files = _extract_archive(container.archives[-1][1])
    assert "TAVILY_API_KEY" not in files[".env"]   # revoked -> key dropped
    assert "web:" not in files["config.yaml"]       # revoked -> section dropped


# ----------------------- apply_container_capabilities ------------------------


@pytest.mark.asyncio
async def test_apply_preserves_user_key_and_backend_without_platform_key(monkeypatch):
    _patch_hermes_settings(monkeypatch, tavily="")  # NO platform pool key
    monkeypatch.setattr(manager, "_runtime_image", lambda: "img:latest")

    container = FakeContainer(
        env_text="API_SERVER_KEY=x\nTAVILY_API_KEY=tvly-user\n",
        config_text="web:\n  backend: serper\n",
    )
    monkeypatch.setattr(manager, "_docker", lambda: FakeDockerClient(container))
    monkeypatch.setattr(
        manager, "get_container",
        lambda db, user_id: _async_none(SimpleNamespace(docker_id="abc123", status="running")),
    )

    db = ApplyFakeDB([
        _ScalarsResult([]),  # no platform defaults
        _ScalarsResult([SimpleNamespace(capability="web_search", enabled=True)]),  # user grant
    ])

    result = await manager.apply_container_capabilities(db, "u1")

    assert result["applied"] is True
    assert "web" in result["toolsets"]
    assert container.restarted is True
    files = _extract_archive(container.archives[-1][1])
    assert "TAVILY_API_KEY=tvly-user" in files[".env"]   # user key, not dropped
    assert "backend: serper" in files["config.yaml"]      # user backend preserved


@pytest.mark.asyncio
async def test_apply_drops_web_when_revoked(monkeypatch):
    _patch_hermes_settings(monkeypatch, tavily="tvly-platform")
    monkeypatch.setattr(manager, "_runtime_image", lambda: "img:latest")

    container = FakeContainer(
        env_text="TAVILY_API_KEY=tvly-user\n",
        config_text="web:\n  backend: tavily\n",
    )
    monkeypatch.setattr(manager, "_docker", lambda: FakeDockerClient(container))
    monkeypatch.setattr(
        manager, "get_container",
        lambda db, user_id: _async_none(SimpleNamespace(docker_id="abc123", status="running")),
    )

    db = ApplyFakeDB([
        _ScalarsResult([]),
        _ScalarsResult([SimpleNamespace(capability="web_search", enabled=False)]),  # revoke
    ])

    result = await manager.apply_container_capabilities(db, "u1")

    assert result["applied"] is True
    assert "web" not in result["toolsets"]
    assert container.restarted is True
    files = _extract_archive(container.archives[-1][1])
    assert "TAVILY_API_KEY" not in files[".env"]
    assert "web:" not in files["config.yaml"]


@pytest.mark.asyncio
async def test_apply_no_container_returns_not_applied(monkeypatch):
    monkeypatch.setattr(manager, "get_container", lambda db, user_id: _async_none(None))
    db = ApplyFakeDB()
    result = await manager.apply_container_capabilities(db, "u1")
    assert result == {"applied": False, "reason": "no_container"}


@pytest.mark.asyncio
async def test_apply_container_missing_in_docker(monkeypatch):
    monkeypatch.setattr(
        manager, "get_container",
        lambda db, user_id: _async_none(SimpleNamespace(docker_id="abc", status="running")),
    )

    def _raise(_id):
        raise RuntimeError("not found")  # DockerNotFound is RuntimeError under the stub

    monkeypatch.setattr(manager, "get_docker_container", _raise)
    db = ApplyFakeDB()
    result = await manager.apply_container_capabilities(db, "u1")
    assert result == {"applied": False, "reason": "container_missing"}


@pytest.mark.asyncio
async def test_apply_refuses_not_running_container(monkeypatch):
    container = FakeContainer(status="exited")
    monkeypatch.setattr(manager, "_docker", lambda: FakeDockerClient(container))
    monkeypatch.setattr(
        manager, "get_container",
        lambda db, user_id: _async_none(SimpleNamespace(docker_id="abc", status="running")),
    )
    db = ApplyFakeDB()
    result = await manager.apply_container_capabilities(db, "u1")
    assert result == {"applied": False, "reason": "not_running"}
    assert container.restarted is False  # must not restart a non-running container


async def _async_none(value):
    return value


# ------------------------------- routes --------------------------------------


ADMIN = SimpleNamespace(id="admin-id", username="admin")


@pytest.mark.asyncio
async def test_apply_route_success_audits(monkeypatch):
    from app.routes import admin

    async def _fake_apply(db, user_id):
        return {"applied": True, "toolsets": ["terminal", "file", "skills", "web"]}

    monkeypatch.setattr(admin, "apply_container_capabilities", _fake_apply)
    db = ApplyFakeDB()
    res = await admin.apply_user_capabilities("u1", db=db, admin_user=ADMIN)
    assert res == {"ok": True, "applied": True, "toolsets": ["terminal", "file", "skills", "web"]}
    assert len(db.added) == 1  # audit row staged
    assert db.committed is True


@pytest.mark.asyncio
async def test_apply_route_404_when_no_container(monkeypatch):
    from app.routes import admin

    async def _fake_apply(db, user_id):
        return {"applied": False, "reason": "no_container"}

    monkeypatch.setattr(admin, "apply_container_capabilities", _fake_apply)
    with pytest.raises(HTTPException) as exc:
        await admin.apply_user_capabilities("u1", db=ApplyFakeDB(), admin_user=ADMIN)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_apply_route_409_when_not_running(monkeypatch):
    from app.routes import admin

    async def _fake_apply(db, user_id):
        return {"applied": False, "reason": "not_running"}

    monkeypatch.setattr(admin, "apply_container_capabilities", _fake_apply)
    with pytest.raises(HTTPException) as exc:
        await admin.apply_user_capabilities("u1", db=ApplyFakeDB(), admin_user=ADMIN)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_bulk_apply_route_user_ids_reports_applied_skipped(monkeypatch):
    from app.routes import admin

    async def _fake_apply(db, user_id):
        if user_id == "u2":
            return {"applied": False, "reason": "not_running"}
        return {"applied": True, "toolsets": ["web"]}

    monkeypatch.setattr(admin, "apply_container_capabilities", _fake_apply)
    monkeypatch.setattr(admin, "_BULK_APPLY_RESTART_DELAY", 0)  # no real delay in tests

    req = admin.BulkApplyCapabilitiesRequest(target=admin.BulkCapabilityTarget(user_ids=["u1", "u2"]))
    db = ApplyFakeDB()
    res = await admin.bulk_apply_capabilities(req, db=db, admin_user=ADMIN)

    assert res["applied_count"] == 1
    assert res["applied"] == ["u1"]
    assert res["skipped_count"] == 1
    assert res["skipped"][0]["user_id"] == "u2"
    assert res["failed_count"] == 0
    assert len(db.added) == 1  # audit
    assert db.committed is True


@pytest.mark.asyncio
async def test_bulk_apply_route_no_target_rejected(monkeypatch):
    from app.routes import admin

    req = admin.BulkApplyCapabilitiesRequest(target=admin.BulkCapabilityTarget())
    with pytest.raises(HTTPException) as exc:
        await admin.bulk_apply_capabilities(req, db=ApplyFakeDB(), admin_user=ADMIN)
    assert exc.value.status_code == 400
