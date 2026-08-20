"""Unit tests for model config persistence (routes/models.py) and container
config preservation (container/manager.py).

Tests the fix that routes user-added custom_providers directly instead
of sending everything through platform-gateway.
"""

from __future__ import annotations

import io
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub docker so platform code can import without a Docker daemon
# ---------------------------------------------------------------------------
if "docker" not in sys.modules:
    docker_stub = types.ModuleType("docker")
    docker_stub.from_env = lambda: None
    docker_stub.DockerClient = object
    docker_stub.models = types.SimpleNamespace(
        containers=types.SimpleNamespace(Container=object)
    )
    docker_stub.types = types.SimpleNamespace(Mount=lambda *args, **kwargs: None)
    docker_errors = types.ModuleType("docker.errors")
    docker_errors.APIError = RuntimeError
    docker_errors.NotFound = RuntimeError
    docker_stub.errors = docker_errors
    sys.modules["docker"] = docker_stub
    sys.modules["docker.errors"] = docker_errors

if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = types.ModuleType("asyncpg")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeContainerExec:
    """Simulates a Docker exec_run result."""

    def __init__(self, exit_code: int = 0, output: bytes = b""):
        self.exit_code = exit_code
        self.output = output


class _FakeContainer:
    """Records exec calls and put_archive calls."""

    def __init__(self, config_yaml: bytes | None = None):
        self._config_yaml = config_yaml
        self.exec_calls: list[tuple] = []
        self.archive_calls: list[tuple[str, bytes]] = []

    def exec_run(self, cmd, user=None):
        self.exec_calls.append((tuple(cmd), user))
        if cmd[0] == "cat" and self._config_yaml is not None:
            return _FakeContainerExec(0, self._config_yaml)
        if cmd[0] == "chown":
            return _FakeContainerExec(0)
        return _FakeContainerExec(1)  # unknown command

    def put_archive(self, path: str, data: bytes) -> bool:
        self.archive_calls.append((path, data))
        return True


def _yaml_bytes(data: dict) -> bytes:
    import yaml

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Tests: update_models_config provider routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_model_routes_to_user_provider(monkeypatch):
    """When defaultModel matches a user custom_provider with an API key,
    model.provider should switch to that provider."""
    from app.routes.models import update_models_config, UpdateModelsConfig
    from app.db.models import User

    existing_config = {
        "model": {"provider": "platform-gateway", "default": "hermes-agent"},
        "custom_providers": [
            {"name": "platform-gateway", "base_url": "http://gw:8080/llm/v1", "api_key": "proxy-key"},
        ],
    }
    container = _FakeContainer(config_yaml=_yaml_bytes(existing_config))

    captured_config: dict = {}

    async def fake_ensure_running(db, user_id):
        return MagicMock()

    from app.routes import models as models_module

    monkeypatch.setattr(models_module, "ensure_running", fake_ensure_running)
    monkeypatch.setattr(models_module, "_container_name", lambda uid: "fake-container")
    monkeypatch.setattr(
        models_module, "get_docker_container", lambda name: container
    )
    monkeypatch.setattr(
        models_module,
        "_write_container_config",
        lambda cname, cfg: captured_config.update(cfg),
    )

    user = User(id="u1", username="t", email="t@t.com", password_hash="x", runtime_mode="dedicated")

    # Set defaultModel to a user provider that has an API key
    body = UpdateModelsConfig(
        providers={
            "deepseek": {
                "baseUrl": "https://api.deepseek.com",
                "apiKey": "sk-test-key",
                "api": "openai-completions",
                "models": [{"id": "deepseek-chat", "name": "DeepSeek Chat"}],
            }
        },
        defaultModel="deepseek/deepseek-chat",
    )

    class FakeDB:
        async def commit(self):
            pass

    await update_models_config(body, user=user, db=FakeDB())

    # Verify custom_providers were written
    providers = captured_config.get("custom_providers", [])
    provider_names = [p["name"] for p in providers if isinstance(p, dict)]
    assert "deepseek" in provider_names
    assert "platform-gateway" in provider_names

    # THE KEY ASSERTION: model.provider must be deepseek, not platform-gateway
    model = captured_config.get("model", {})
    assert model.get("provider") == "deepseek", (
        f"Expected provider=deepseek, got {model.get('provider')}"
    )
    # Provider prefix must be stripped from model.default when using a
    # user-provided provider, so the hermes agent sends the actual model
    # name (e.g. "deepseek-chat") not the fully qualified form.
    assert model.get("default") == "deepseek-chat"
    # base_url override must be removed so the custom_provider's own base_url is used
    assert "base_url" not in model


@pytest.mark.asyncio
async def test_default_model_falls_back_to_platform_provider(monkeypatch):
    """When defaultModel has NO matching custom_provider with API key,
    model.provider should stay as platform-gateway."""
    from app.routes.models import update_models_config, UpdateModelsConfig
    from app.db.models import User

    existing_config = {
        "model": {"provider": "platform-gateway", "default": "hermes-agent"},
        "custom_providers": [
            {"name": "platform-gateway", "base_url": "http://gw:8080/llm/v1", "api_key": "proxy-key"},
        ],
    }
    container = _FakeContainer(config_yaml=_yaml_bytes(existing_config))
    captured_config: dict = {}

    async def fake_ensure_running(db, user_id):
        return MagicMock()

    from app.routes import models as models_module

    monkeypatch.setattr(models_module, "ensure_running", fake_ensure_running)
    monkeypatch.setattr(models_module, "_container_name", lambda uid: "fake-container")
    monkeypatch.setattr(models_module, "get_docker_container", lambda name: container)
    monkeypatch.setattr(
        models_module, "_write_container_config", lambda cname, cfg: captured_config.update(cfg)
    )

    user = User(id="u1", username="t", email="t@t.com", password_hash="x", runtime_mode="dedicated")

    # Model format with provider prefix but NO matching user provider
    body = UpdateModelsConfig(defaultModel="claude-sonnet-4-5")

    class FakeDB:
        async def commit(self):
            pass

    await update_models_config(body, user=user, db=FakeDB())

    model = captured_config.get("model", {})
    assert model.get("provider") == "platform-gateway"
    assert model.get("default") == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_default_model_no_slash_falls_back(monkeypatch):
    """defaultModel without '/' should fall back to platform-gateway."""
    from app.routes.models import update_models_config, UpdateModelsConfig
    from app.db.models import User

    existing_config = {
        "model": {"provider": "platform-gateway"},
        "custom_providers": [],
    }
    container = _FakeContainer(config_yaml=_yaml_bytes(existing_config))
    captured_config: dict = {}

    async def fake_ensure_running(db, user_id):
        return MagicMock()

    from app.routes import models as models_module

    monkeypatch.setattr(models_module, "ensure_running", fake_ensure_running)
    monkeypatch.setattr(models_module, "_container_name", lambda uid: "fake-container")
    monkeypatch.setattr(models_module, "get_docker_container", lambda name: container)
    monkeypatch.setattr(
        models_module, "_write_container_config", lambda cname, cfg: captured_config.update(cfg)
    )

    user = User(id="u1", username="t", email="t@t.com", password_hash="x", runtime_mode="dedicated")
    body = UpdateModelsConfig(defaultModel="qwen-plus")

    class FakeDB:
        async def commit(self):
            pass

    await update_models_config(body, user=user, db=FakeDB())

    model = captured_config.get("model", {})
    assert model.get("provider") == "platform-gateway"
    assert model.get("default") == "qwen-plus"


@pytest.mark.asyncio
async def test_provider_without_api_key_falls_back(monkeypatch):
    """Even when defaultModel matches a custom_provider by name,
    if the provider has NO api_key, fall back to platform-gateway."""
    from app.routes.models import update_models_config, UpdateModelsConfig
    from app.db.models import User

    existing_config = {
        "model": {"provider": "platform-gateway"},
        "custom_providers": [
            {"name": "platform-gateway", "base_url": "http://gw:8080/llm/v1", "api_key": "proxy-key"},
            {"name": "deepseek", "base_url": "https://api.deepseek.com", "api_key": ""},
        ],
    }
    container = _FakeContainer(config_yaml=_yaml_bytes(existing_config))
    captured_config: dict = {}

    async def fake_ensure_running(db, user_id):
        return MagicMock()

    from app.routes import models as models_module

    monkeypatch.setattr(models_module, "ensure_running", fake_ensure_running)
    monkeypatch.setattr(models_module, "_container_name", lambda uid: "fake-container")
    monkeypatch.setattr(models_module, "get_docker_container", lambda name: container)
    monkeypatch.setattr(
        models_module, "_write_container_config", lambda cname, cfg: captured_config.update(cfg)
    )

    user = User(id="u1", username="t", email="t@t.com", password_hash="x", runtime_mode="dedicated")
    body = UpdateModelsConfig(defaultModel="deepseek/deepseek-chat")

    class FakeDB:
        async def commit(self):
            pass

    await update_models_config(body, user=user, db=FakeDB())

    model = captured_config.get("model", {})
    # No API key in the deepseek provider → falls back
    assert model.get("provider") == "platform-gateway"


# ---------------------------------------------------------------------------
# Tests: _write_hermes_runtime_files provider preservation
# ---------------------------------------------------------------------------

def test_write_runtime_files_preserves_user_provider(monkeypatch):
    """When existing config has a user-defined model.provider (not
    platform-gateway), container recreation should preserve it."""
    from app.container import manager as mgr
    from app.config import settings

    platform_config = {
        "model": {
            "default": settings.default_model,
            "provider": settings.dedicated_hermes_default_provider,
            "base_url": settings.dedicated_hermes_default_base_url,
        },
    }
    monkeypatch.setattr(mgr, "_build_hermes_config_yaml", lambda plan=None: __import__("yaml").safe_dump(platform_config))
    monkeypatch.setattr(mgr, "_build_hermes_env_file", lambda preserve_vars=None, plan=None: "")

    existing_config = {
        "model": {"provider": "deepseek", "default": "deepseek-chat"},
        "custom_providers": [
            {"name": "platform-gateway", "base_url": "http://gw:8080/llm/v1", "api_key": "proxy-key"},
            {"name": "deepseek", "base_url": "https://api.deepseek.com", "api_key": "sk-xxx"},
        ],
    }

    container = _FakeContainer(config_yaml=_yaml_bytes(existing_config))
    monkeypatch.setattr(mgr, "_read_existing_hermes_config", lambda c: existing_config)
    monkeypatch.setattr(mgr, "_repair_hermes_data_ownership", lambda c: None)

    mgr._write_hermes_runtime_files(container)

    # The written config should have preserved the user's provider
    path, tar_data = container.archive_calls[0]
    import tarfile

    tf = tarfile.open(fileobj=io.BytesIO(tar_data))
    config_member = next(m for m in tf.getmembers() if m.name == "config.yaml")
    written = __import__("yaml").safe_load(tf.extractfile(config_member).read())

    model = written.get("model", {})
    assert model.get("provider") == "deepseek", (
        f"Expected provider=deepseek, got {model.get('provider')}"
    )
    # model.default should already be stripped of prefix (from update_models_config)
    assert model.get("default") == "deepseek-chat"
    assert "base_url" not in model
    # User's custom_providers should be preserved
    provider_names = [p["name"] for p in written.get("custom_providers", [])]
    assert "deepseek" in provider_names


def test_write_runtime_files_keeps_platform_gateway_when_no_user_provider(monkeypatch):
    """When existing config has platform-gateway, container recreation
    should keep it (no user override to preserve)."""
    from app.container import manager as mgr
    from app.config import settings

    platform_config = {
        "model": {
            "default": settings.default_model,
            "provider": settings.dedicated_hermes_default_provider,
            "base_url": settings.dedicated_hermes_default_base_url,
        },
    }
    monkeypatch.setattr(mgr, "_build_hermes_config_yaml", lambda plan=None: __import__("yaml").safe_dump(platform_config))
    monkeypatch.setattr(mgr, "_build_hermes_env_file", lambda preserve_vars=None, plan=None: "")

    existing_config = {
        "model": {"provider": "platform-gateway", "default": "hermes-agent"},
        "custom_providers": [
            {"name": "platform-gateway", "base_url": "http://gw:8080/llm/v1", "api_key": "proxy-key"},
        ],
    }

    container = _FakeContainer(config_yaml=_yaml_bytes(existing_config))
    monkeypatch.setattr(mgr, "_read_existing_hermes_config", lambda c: existing_config)
    monkeypatch.setattr(mgr, "_repair_hermes_data_ownership", lambda c: None)

    mgr._write_hermes_runtime_files(container)

    import tarfile

    path, tar_data = container.archive_calls[0]
    tf = tarfile.open(fileobj=io.BytesIO(tar_data))
    config_member = next(m for m in tf.getmembers() if m.name == "config.yaml")
    written = __import__("yaml").safe_load(tf.extractfile(config_member).read())

    model = written.get("model", {})
    # platform-gateway should be the provider (entrypoint.sh will also ensure this)
    assert model.get("provider") in ("platform-gateway", "custom")
