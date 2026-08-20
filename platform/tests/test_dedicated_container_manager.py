import sys
import yaml
import types
from types import SimpleNamespace

from app.config import Settings

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

from app.container import manager


class DummyContainer:
    def __init__(self, ports=None):
        self.attrs = {"NetworkSettings": {"Ports": ports or {}}}


def test_settings_expose_dedicated_container_prefix_fields():
    settings = Settings()

    assert settings.dedicated_runtime_container_name_prefix == "hermes-user"
    assert settings.dedicated_runtime_data_volume_prefix == "hermes-data"
    assert settings.hermes_api_toolsets == "full"


def test_hermes_runtime_switches_internal_port_publish(monkeypatch):
    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_internal_port", 18123)
    monkeypatch.setattr(manager.settings, "user_container_bind_ip", "127.0.0.1")

    assert manager._internal_port() == 18123
    assert manager._runtime_command() == ["gateway", "run"]
    assert manager._runtime_published_ports() == {"18123/tcp": ("127.0.0.1", None)}
    assert manager._runtime_preferred_ports(5901, 30001) is None



def test_hermes_runtime_environment_enables_api_server(monkeypatch):
    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_internal_port", 18123)
    monkeypatch.setattr(manager.settings, "dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_default_api_key", "proxy-key")
    monkeypatch.setattr(manager.settings, "default_model", "claude-sonnet-4-5")
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "none")
    monkeypatch.setattr(manager.settings, "container_tz", "Asia/Shanghai")

    env = manager._runtime_environment("container-token", "sso-token")

    assert env["COMWORKER_PROXY__URL"] == "http://gateway:8080/llm/v1"
    assert env["COMWORKER_PROXY__TOKEN"] == "container-token"
    # COMWORKER_AGENTS__DEFAULTS__MODEL is intentionally absent from the hermes
    # docker-run env so the entrypoint does not overwrite a user-chosen
    # model.default in config.yaml on every restart. The model comes from
    # config.yaml (preserved by _write_hermes_runtime_files).
    assert "COMWORKER_AGENTS__DEFAULTS__MODEL" not in env
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["API_SERVER_ENABLED"] == "true"
    assert env["API_SERVER_HOST"] == "0.0.0.0"
    assert env["API_SERVER_PORT"] == "18123"
    assert env["API_SERVER_KEY"] == "bridge-key"
    assert env["GATEWAY_ALLOW_ALL_USERS"] == "true"
    assert env["OPENAI_API_KEY"] == "proxy-key"
    # HERMES_API_TOOLSETS is intentionally absent from the docker-run env so the
    # hermes entrypoint does not overwrite capability-driven api_server in
    # config.yaml. api_server comes from config.yaml (plan.enabled_toolsets).
    assert "HERMES_API_TOOLSETS" not in env
    assert env["INFOX_MED_TOKEN"] == "sso-token"
    assert "BRIDGE_ENABLE_CHANNELS" not in env


def test_build_hermes_runtime_files_support_platform_default_model(monkeypatch):
    monkeypatch.setattr(manager.settings, "default_model", "claude-sonnet-4-5")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_default_provider", "custom")
    monkeypatch.setattr(
        manager.settings,
        "dedicated_hermes_default_base_url",
        "http://gateway:8080/llm/v1",
    )
    monkeypatch.setattr(manager.settings, "dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_default_api_key", "proxy-key")
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "none")
    monkeypatch.setattr(manager.settings, "hermes_reasoning_effort", "none")
    monkeypatch.setattr(manager.settings, "hermes_service_tier", "")

    config_yaml = manager._build_hermes_config_yaml()
    env_file = manager._build_hermes_env_file()

    assert 'default: claude-sonnet-4-5' in config_yaml
    assert 'provider: custom' in config_yaml
    assert 'base_url: http://gateway:8080/llm/v1' in config_yaml
    assert 'agent:' in config_yaml
    assert 'reasoning_effort: none' in config_yaml
    assert "service_tier: ''" in config_yaml
    assert 'platform_toolsets:' in config_yaml
    assert 'api_server: []' in config_yaml
    assert 'API_SERVER_KEY=bridge-key' in env_file
    assert 'GATEWAY_ALLOW_ALL_USERS=true' in env_file
    assert 'OPENAI_API_KEY=proxy-key' in env_file
    assert 'HERMES_API_TOOLSETS=none' in env_file
    assert 'HERMES_REASONING_EFFORT=none' in env_file
    assert 'HERMES_SERVICE_TIER=' in env_file


def test_write_hermes_runtime_files_repairs_data_volume_ownership(monkeypatch):
    class RecordingContainer:
        def __init__(self):
            self.attrs = {
                "Mounts": [
                    {
                        "Type": "volume",
                        "Name": "hermes-anonymous-data",
                        "Destination": "/opt/data",
                    }
                ]
            }
            self.archives = []

        def put_archive(self, path, data):
            self.archives.append((path, data))
            return True

    class RecordingContainerRunner:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(id="repair-container")

    runner = RecordingContainerRunner()
    monkeypatch.setattr(manager, "_runtime_image", lambda: "comworker-hermes-agent:latest")
    monkeypatch.setattr(manager, "_docker", lambda: SimpleNamespace(containers=runner))
    monkeypatch.setattr(
        manager.docker.types,
        "Mount",
        lambda target, source, type: {"target": target, "source": source, "type": type},
    )

    container = RecordingContainer()

    manager._write_hermes_runtime_files(container)

    assert container.archives[0][0] == "/opt/data"
    assert runner.calls == [
        {
            "image": "comworker-hermes-agent:latest",
            "entrypoint": "chown",
            "command": ["-R", "hermes:hermes", "/opt/data"],
            "mounts": [{"target": "/opt/data", "source": "hermes-anonymous-data", "type": "volume"}],
            "remove": True,
        }
    ]


def test_hermes_api_toolsets_support_skills_and_full_modes(monkeypatch):
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "skills")
    config_yaml = manager._build_hermes_config_yaml()
    assert "- skills" in config_yaml

    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "full")
    config_yaml = manager._build_hermes_config_yaml()
    assert "- hermes-api-server" in config_yaml


def test_published_port_bindings_follow_runtime_backend(monkeypatch):
    hermes_container = DummyContainer(
        {
            "18123/tcp": [{"HostIp": "127.0.0.1", "HostPort": "40123"}],
        }
    )

    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_internal_port", 18123)
    assert manager._published_port_bindings(hermes_container) == (
        ("", ""),
        ("127.0.0.1", "40123"),
    )


def test_hermes_runtime_rejects_legacy_comworker_container(monkeypatch):
    legacy = SimpleNamespace(
        attrs={
            "Config": {
                "Image": "comworker:latest",
                "Entrypoint": ["/entrypoint.sh"],
                "Cmd": ["node", "bridge/dist/bridge/start.js"],
                "Env": ["BRIDGE_ENABLE_CHANNELS=1"],
            }
        }
    )
    hermes = SimpleNamespace(
        attrs={
            "Config": {
                "Image": "comworker-hermes-agent:latest",
                "Entrypoint": ["/opt/hermes/docker/entrypoint.sh"],
                "Cmd": ["gateway", "run", "-v"],
                "Env": ["API_SERVER_ENABLED=true", "HERMES_HOME=/opt/data"],
            }
        }
    )

    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")

    assert manager._container_matches_runtime(legacy) is False
    assert manager._container_matches_runtime(hermes) is True


def test_default_inject_plan_flows_into_container_env_and_config(monkeypatch):
    from app.capabilities import build_capability_plan

    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_internal_port", 18123)
    monkeypatch.setattr(manager.settings, "dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_default_api_key", "proxy-key")
    monkeypatch.setattr(manager.settings, "default_model", "claude-sonnet-4-5")
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "terminal,file,skills")
    monkeypatch.setattr(manager.settings, "container_tz", "Asia/Shanghai")
    monkeypatch.setattr(manager.settings, "tavily_api_key", "tvly-default")

    plan = build_capability_plan(
        manager.settings,
        platform_defaults={"web_search": SimpleNamespace(default_inject=True)},
    )

    env = manager._runtime_environment("tok", None, plan)
    assert env["TAVILY_API_KEY"] == "tvly-default"

    cfg = manager._build_hermes_config_yaml(plan)
    assert "web:" in cfg
    assert "backend: tavily" in cfg
    parsed = yaml.safe_load(cfg)
    assert "web" in parsed["platform_toolsets"]["api_server"]

    env_file = manager._build_hermes_env_file(None, plan)
    assert "TAVILY_API_KEY=tvly-default" in env_file


def test_default_inject_without_platform_key_does_not_mount_broken_toolset(monkeypatch):
    from app.capabilities import build_capability_plan

    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_internal_port", 18123)
    monkeypatch.setattr(manager.settings, "dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_default_api_key", "proxy-key")
    monkeypatch.setattr(manager.settings, "default_model", "claude-sonnet-4-5")
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "terminal,file,skills")
    monkeypatch.setattr(manager.settings, "container_tz", "Asia/Shanghai")
    monkeypatch.setattr(manager.settings, "tavily_api_key", "")

    plan = build_capability_plan(
        manager.settings,
        platform_defaults={"web_search": SimpleNamespace(default_inject=True)},
    )

    env = manager._runtime_environment("tok", None, plan)
    assert "TAVILY_API_KEY" not in env

    cfg = manager._build_hermes_config_yaml(plan)
    assert "web:" not in cfg
    parsed = yaml.safe_load(cfg)
    assert "web" not in parsed["platform_toolsets"]["api_server"]


def test_user_grant_plan_flows_into_container_env_and_config(monkeypatch):
    from app.capabilities import build_capability_plan

    monkeypatch.setattr(manager.settings, "dedicated_runtime_backend", "hermes")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_internal_port", 18123)
    monkeypatch.setattr(manager.settings, "dedicated_hermes_api_key", "bridge-key")
    monkeypatch.setattr(manager.settings, "dedicated_hermes_default_api_key", "proxy-key")
    monkeypatch.setattr(manager.settings, "default_model", "claude-sonnet-4-5")
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "terminal,file,skills")
    monkeypatch.setattr(manager.settings, "container_tz", "Asia/Shanghai")
    monkeypatch.setattr(manager.settings, "tavily_api_key", "tvly-x")

    # Per-user grant with NO platform default -> capability is on for this user.
    plan = build_capability_plan(
        manager.settings,
        user_caps={"web_search": SimpleNamespace(enabled=True)},
    )

    env = manager._runtime_environment("tok", None, plan)
    assert env["TAVILY_API_KEY"] == "tvly-x"
    parsed = yaml.safe_load(manager._build_hermes_config_yaml(plan))
    assert "web" in parsed["platform_toolsets"]["api_server"]


def test_user_revoke_overrides_default_in_plan(monkeypatch):
    from app.capabilities import build_capability_plan

    monkeypatch.setattr(manager.settings, "tavily_api_key", "tvly-x")
    monkeypatch.setattr(manager.settings, "hermes_api_toolsets", "terminal,file,skills")

    # Default inject on, but per-user revoke -> off for this user.
    plan = build_capability_plan(
        manager.settings,
        user_caps={"web_search": SimpleNamespace(enabled=False)},
        platform_defaults={"web_search": SimpleNamespace(default_inject=True)},
    )

    env = manager._runtime_environment("tok", None, plan)
    assert "TAVILY_API_KEY" not in env
    parsed = yaml.safe_load(manager._build_hermes_config_yaml(plan))
    assert "web" not in parsed["platform_toolsets"]["api_server"]
