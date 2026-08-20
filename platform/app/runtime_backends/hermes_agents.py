from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from docker.errors import APIError as DockerAPIError
from docker.errors import NotFound as DockerNotFound
from fastapi import HTTPException

from app.container.manager import get_docker_container

DEFAULT_HERMES_MODEL = "hermes-agent"
SYSTEM_AGENT_IDS = {"main", "manager", "programmer", "researcher", "hr", "doctor"}
HERMES_PROFILES_ROOT = "/opt/data/profiles"
CONTAINER_PROFILES_DIR = "/opt/data/profiles"
HERMES_PROFILE_GATEWAY_BASE_PORT = 19080

_SESSION_AGENT_RE = re.compile(r"^agent:([^:]+):")
_AGENT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}$")
_IDENTITY_NAME_RE = re.compile(r"(?:\*\*)?\s*(?:名字|name)\s*(?:：|:)\s*(?:\*\*)?\s*(.+)", re.IGNORECASE)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _deploy_copy_dir() -> Path:
    existing_candidates: list[Path] = []
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "deploy_copy"
        if (candidate / "comworker_defaults.json").is_file() or (candidate / "Agents").is_dir():
            return candidate
        if candidate.exists():
            existing_candidates.append(candidate)
    if existing_candidates:
        return existing_candidates[0]
    return _repo_root() / "deploy_copy"


def _defaults_path() -> Path:
    return _deploy_copy_dir() / "comworker_defaults.json"


def _agents_dir() -> Path:
    return _deploy_copy_dir() / "Agents"


def _agent_dir(agent_id: str) -> Path:
    root = _agents_dir().resolve()
    path = (root / agent_id).resolve()
    if root not in path.parents or not path.is_dir():
        raise HTTPException(status_code=404, detail="Agent not found")
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Agent not found")
    return path


def _safe_agent_id(agent_id: str) -> str:
    normalized = agent_id.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not normalized or not _AGENT_ID_RE.match(normalized):
        raise HTTPException(status_code=400, detail="Invalid agent id")
    return normalized


def safe_agent_id_value(agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    return _safe_agent_id(agent_id)


def _safe_agent_file_name(name: str) -> str:
    normalized = name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not normalized or normalized in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid agent file name")
    return normalized


def _exec_output(result) -> tuple[int, bytes]:
    if isinstance(result, tuple):
        exit_code, output = result
    else:
        exit_code = getattr(result, "exit_code", 0)
        output = getattr(result, "output", b"")
    if isinstance(output, str):
        output = output.encode("utf-8")
    return int(exit_code or 0), output or b""


def _container(container_id_or_name: str | None):
    if not container_id_or_name:
        raise HTTPException(status_code=503, detail="Hermes runtime container is unavailable")
    try:
        return get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(status_code=503, detail="Hermes runtime container is unavailable") from exc


def _profile_gateway_port(agent_id: str) -> int:
    agent = _safe_agent_id(agent_id)
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(agent))
    return HERMES_PROFILE_GATEWAY_BASE_PORT + 1 + (checksum % 3000)


def ensure_profile_gateway_in_hermes_container(
    container_id_or_name: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    agent = _safe_agent_id(agent_id or "main")
    port = _profile_gateway_port(agent)
    container = _container(container_id_or_name)
    script = r'''
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

agent = sys.argv[1]
port = int(sys.argv[2])
root = Path("/opt/data/profiles")
profile = (root / agent).resolve()
if root.resolve() not in profile.parents or not profile.is_dir():
    raise SystemExit("profile not found")

for subdir in ["memories", "sessions", "skills", "skins", "logs", "plans", "workspace", "cron", "home"]:
    (profile / subdir).mkdir(parents=True, exist_ok=True)

def is_ready():
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False

pid_file = profile / "platform-gateway.pid"
if is_ready():
    print(json.dumps({"ok": True, "agentId": agent, "port": port, "started": False}))
    raise SystemExit(0)

if pid_file.exists():
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except Exception:
        try:
            pid_file.unlink()
        except OSError:
            pass

env = os.environ.copy()
env.update({
    "HERMES_HOME": str(profile),
    "HOME": str(profile / "home"),
    "TERMINAL_CWD": str(profile / "workspace"),
    "HERMES_WRITE_SAFE_ROOT": str(profile),
    "API_SERVER_ENABLED": "true",
    "API_SERVER_HOST": "127.0.0.1",
    "API_SERVER_PORT": str(port),
    "GATEWAY_ALLOW_ALL_USERS": "true",
})
log_path = profile / "platform-gateway.log"
log_path.touch(exist_ok=True)
if os.geteuid() == 0:
    subprocess.run(["chown", "-R", "hermes:hermes", str(profile)], check=False)
    subprocess.run(["chown", "hermes:hermes", str(log_path)], check=False)
log = open(log_path, "ab", buffering=0)
init = subprocess.run(
    ["/opt/hermes/docker/entrypoint.sh", "python", "-c", "pass"],
    cwd=str(profile / "workspace"),
    env=env,
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
)
if init.returncode != 0:
    raise SystemExit(f"profile gateway init exited with {init.returncode}")
config_script = r"""
import os
from pathlib import Path
import yaml

config_path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
if not isinstance(config, dict):
    config = {}
terminal_config = config.setdefault("terminal", {})
if not isinstance(terminal_config, dict):
    terminal_config = {}
    config["terminal"] = terminal_config
terminal_config["cwd"] = os.environ["TERMINAL_CWD"]
config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
"""
config_update = subprocess.run(
    ["/opt/hermes/docker/entrypoint.sh", "python", "-c", config_script],
    cwd=str(profile / "workspace"),
    env=env,
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
)
if config_update.returncode != 0:
    raise SystemExit(f"profile gateway config update exited with {config_update.returncode}")
launch = (
    'source /opt/hermes/.venv/bin/activate; '
    'cd "$TERMINAL_CWD"; '
    'export PYTHONPATH="/opt/hermes${PYTHONPATH:+:$PYTHONPATH}"; '
    'exec /opt/hermes/.venv/bin/python /opt/hermes/comworker_hermes.py gateway run'
)
cmd = ["bash", "-lc", launch]
if os.geteuid() == 0:
    cmd = ["/usr/sbin/gosu", "hermes", *cmd]
proc = subprocess.Popen(
    cmd,
    cwd=str(profile / "workspace"),
    env=env,
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
pid_file.write_text(str(proc.pid), encoding="utf-8")
if os.geteuid() == 0:
    subprocess.run(["chown", "hermes:hermes", str(pid_file)], check=False)

for _ in range(120):
    if is_ready():
        print(json.dumps({"ok": True, "agentId": agent, "port": port, "started": True, "pid": proc.pid}))
        raise SystemExit(0)
    if proc.poll() is not None:
        raise SystemExit(f"profile gateway exited with {proc.returncode}")
    time.sleep(0.25)
raise SystemExit("profile gateway did not become ready")
'''
    result = container.exec_run(["python3", "-c", script, agent, str(port)])
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(
            status_code=503,
            detail=output.decode("utf-8", errors="replace") or "Failed to start Hermes profile gateway",
        )
    try:
        payload = json.loads(output.decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid Hermes profile gateway response") from exc
    return payload if isinstance(payload, dict) else {"ok": True, "agentId": agent, "port": port}


def _profile_workspace(agent_id: str) -> str:
    agent = _safe_agent_id(agent_id)
    return f"profiles/{agent}/workspace"


def _profile_script_common() -> str:
    return r'''
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path("/opt/data/profiles")
SYSTEM = {"main", "manager", "programmer", "researcher", "hr", "doctor"}
AGENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}$")

def safe_agent_id(value):
    value = (value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not AGENT_RE.match(value):
        raise ValueError("Invalid agent id")
    return value

def safe_file_name(value):
    value = (value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not value or value in {".", ".."}:
        raise ValueError("Invalid agent file name")
    return value

def profile_dir(agent_id):
    agent_id = safe_agent_id(agent_id)
    path = (ROOT / agent_id).resolve()
    if ROOT.resolve() not in path.parents:
        raise ValueError("Invalid agent id")
    return path

def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""

def parse_identity(agent_id, text, meta=None):
    meta = meta or {}
    configured_name = str(meta.get("name") or "").strip()
    configured_description = str(meta.get("description") or "").strip()
    configured_avatar = str(meta.get("avatar") or "").strip()
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    name = configured_name
    if not name:
        for line in lines:
            if line.startswith("#"):
                name = line.lstrip("#").strip()
                if name.startswith("IDENTITY.md -"):
                    name = name.split("-", 1)[1].strip()
                break
    if not name:
        for line in lines:
            lower = line.lower()
            if "name" in lower or "名字" in line:
                value = re.sub(r"^[\-\*\s]*", "", line)
                value = re.sub(r"(\*\*)?(name|名字)(\*\*)?\s*[:：]\s*(\*\*)?", "", value, flags=re.I).strip()
                value = value.strip("*").strip()
                if value:
                    name = value
                    break
    if not name:
        name = agent_id

    description = configured_description
    if not description:
        marker = "## 一句话简介"
        if marker in text:
            tail = text.split(marker, 1)[1].strip()
            description = next((line.strip() for line in tail.splitlines() if line.strip()), "")
        else:
            body_lines = [line for line in lines if not line.startswith("#") and "name" not in line.lower() and "名字" not in line]
            description = next((line.strip("- ").strip() for line in body_lines if line.strip("- ").strip()), "")
    return name, description, configured_avatar

def meta_for(path):
    try:
        payload = json.loads((path / "profile.json").read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}

def workspace_for(agent_id):
    return f"profiles/{safe_agent_id(agent_id)}/workspace"
'''


def list_agent_profiles_from_hermes_container(
    container_id_or_name: str | None,
    *,
    scope: str,
    runtime_mode: str,
    default_id: str | None = None,
) -> dict[str, Any]:
    container = _container(container_id_or_name)
    script = _profile_script_common() + r'''
ROOT.mkdir(parents=True, exist_ok=True)
agents = []
for path in sorted(ROOT.iterdir(), key=lambda item: item.name):
    if not path.is_dir() or path.name.startswith("."):
        continue
    agent_id = path.name
    identity = read_text(path / "workspace" / "IDENTITY.md")
    meta = meta_for(path)
    name, description, avatar = parse_identity(agent_id, identity, meta)
    system = bool(meta.get("system")) or agent_id in SYSTEM
    agents.append({
        "id": agent_id,
        "name": name,
        "identity": {
            "name": name,
            "description": description,
            "avatar": avatar,
            "avatarUrl": avatar,
        },
        "workspace": workspace_for(agent_id),
        "available": True,
        "default": bool(meta.get("default")) or agent_id == "main",
        "system": system,
        "builtin": system,
        "readonly": system,
        "source": "system" if system else "user",
    })
if not agents:
    agents.append({
        "id": "hermes-agent",
        "name": "Hermes Agent",
        "identity": {"name": "Hermes Agent", "description": ""},
        "workspace": "workspace",
        "available": True,
        "default": True,
        "system": True,
        "builtin": True,
        "readonly": True,
        "source": "system",
    })
default_id = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
agent_ids = {item["id"] for item in agents}
configured_default = next((item["id"] for item in agents if item.get("default")), agents[0]["id"])
resolved_default = default_id if default_id in agent_ids else configured_default
print(json.dumps({
    "agents": agents,
    "defaultId": resolved_default,
    "mainKey": f"agent:{resolved_default}",
    "scope": sys.argv[2],
    "runtime_mode": sys.argv[3],
}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, default_id or "", scope, runtime_mode])
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to list Hermes Agent profiles")
    try:
        payload = json.loads(output.decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid Hermes Agent profile response") from exc
    return payload if isinstance(payload, dict) else {"agents": []}


def list_agent_profile_sessions_from_hermes_container(container_id_or_name: str | None) -> list[dict[str, Any]]:
    container = _container(container_id_or_name)
    script = r'''
import json
from pathlib import Path

root = Path("/opt/data/profiles")
sessions = []
if root.is_dir():
    for profile in sorted(root.iterdir(), key=lambda item: item.name):
        if not profile.is_dir() or profile.name.startswith("."):
            continue
        sessions_dir = profile / "sessions"
        if not sessions_dir.is_dir():
            continue
        for path in sorted(sessions_dir.glob("session_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            session_id = str(payload.get("session_id") or payload.get("key") or payload.get("sessionKey") or "")
            if not session_id:
                session_id = path.name.removeprefix("session_").removesuffix(".json")
            if not session_id.startswith("agent:"):
                session_id = f"agent:{profile.name}:{session_id}"
            messages = payload.get("messages")
            message_count = payload.get("message_count")
            if not isinstance(message_count, int):
                message_count = len(messages) if isinstance(messages, list) else 0
            created_at = payload.get("created_at")
            updated_at = payload.get("updated_at") or payload.get("last_message_at") or created_at
            if not updated_at:
                updated_at = path.stat().st_mtime
            sessions.append({
                "session_id": session_id,
                "title": payload.get("title") or session_id.rsplit(":", 1)[-1] or session_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "last_message_at": payload.get("last_message_at") or updated_at,
                "message_count": message_count,
            })
print(json.dumps({"sessions": sessions}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script])
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to list Hermes Agent sessions")
    try:
        payload = json.loads(output.decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid Hermes Agent sessions response") from exc
    sessions = payload.get("sessions") if isinstance(payload, dict) else []
    return sessions if isinstance(sessions, list) else []


def create_agent_profile_in_hermes_container(
    container_id_or_name: str | None,
    agent_id: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    avatar: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    container = _container(container_id_or_name)
    payload = {
        "agentId": agent_id,
        "name": (display_name or "").strip() or agent_id,
        "description": (description or "").strip(),
        "avatar": (avatar or "").strip(),
        "systemPrompt": (system_prompt or "").strip(),
    }
    script = _profile_script_common() + r'''
payload = json.loads(sys.argv[1])
agent_id = safe_agent_id(payload.get("agentId"))
path = profile_dir(agent_id)
if path.exists():
    print("Agent already exists", file=sys.stderr)
    sys.exit(2)
(path / "workspace").mkdir(parents=True)
for name in ["memories", "sessions", "skills", "skins", "logs", "plans", "cron", "home"]:
    (path / name).mkdir(parents=True, exist_ok=True)
display_name = (payload.get("name") or agent_id).strip() or agent_id
description = (payload.get("description") or "").strip()
identity = f"# {display_name}\n\n{description}\n" if description else f"# {display_name}\n\nYou are {display_name}, a helpful AI assistant.\n"
(path / "workspace" / "IDENTITY.md").write_text(identity, encoding="utf-8")
(path / "workspace" / "AGENTS.md").write_text("1. Read `IDENTITY.md` for your role and behavior.\n2. Answer as this Agent.\n", encoding="utf-8")
(path / "workspace" / "USER.md").write_text("", encoding="utf-8")
(path / "memories" / "USER.md").write_text("", encoding="utf-8")
soul = (payload.get("systemPrompt") or "").strip()
if soul:
    (path / "SOUL.md").write_text(soul, encoding="utf-8")
meta = {
    "id": agent_id,
    "name": display_name,
    "description": description,
    "avatar": (payload.get("avatar") or "").strip(),
    "system": False,
}
(path / "profile.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"ok": True, "agentId": agent_id, "name": display_name, "workspace": workspace_for(agent_id)}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, json.dumps(payload, ensure_ascii=False)])
    exit_code, output = _exec_output(result)
    if exit_code == 2:
        raise HTTPException(status_code=409, detail="Agent already exists")
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to create Hermes Agent profile")
    return json.loads(output.decode("utf-8"))


def update_agent_profile_in_hermes_container(
    container_id_or_name: str | None,
    agent_id: str,
    *,
    name: str | None = None,
    avatar: str | None = None,
) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    container = _container(container_id_or_name)
    payload = {"agentId": agent_id, "name": name, "avatar": avatar}
    script = _profile_script_common() + r'''
payload = json.loads(sys.argv[1])
agent_id = safe_agent_id(payload.get("agentId"))
path = profile_dir(agent_id)
if not path.is_dir():
    print("Agent not found", file=sys.stderr)
    sys.exit(4)
if agent_id in SYSTEM:
    print("System Agent is read-only", file=sys.stderr)
    sys.exit(3)
meta = meta_for(path)
if payload.get("name") is not None:
    meta["name"] = str(payload.get("name") or "").strip() or agent_id
if payload.get("avatar") is not None:
    meta["avatar"] = str(payload.get("avatar") or "").strip()
meta["system"] = False
(path / "profile.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"ok": True, "agentId": agent_id}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, json.dumps(payload, ensure_ascii=False)])
    exit_code, output = _exec_output(result)
    if exit_code == 3:
        raise HTTPException(status_code=403, detail="System Agent is read-only")
    if exit_code == 4:
        raise HTTPException(status_code=404, detail="Agent not found")
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to update Hermes Agent profile")
    return json.loads(output.decode("utf-8"))


def delete_agent_profile_from_hermes_container(
    container_id_or_name: str | None,
    agent_id: str,
    *,
    delete_files: bool = False,
) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    container = _container(container_id_or_name)
    script = _profile_script_common() + r'''
agent_id = safe_agent_id(sys.argv[1])
path = profile_dir(agent_id)
if agent_id in SYSTEM:
    print("System Agent is read-only", file=sys.stderr)
    sys.exit(3)
if not path.is_dir():
    print("Agent not found", file=sys.stderr)
    sys.exit(4)
shutil.rmtree(path)
print(json.dumps({"ok": True, "agentId": agent_id}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, agent_id])
    exit_code, output = _exec_output(result)
    if exit_code == 3:
        raise HTTPException(status_code=403, detail="System Agent is read-only")
    if exit_code == 4:
        raise HTTPException(status_code=404, detail="Agent not found")
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to delete Hermes Agent profile")
    return json.loads(output.decode("utf-8"))


def list_agent_profile_files_from_hermes_container(container_id_or_name: str | None, agent_id: str) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    container = _container(container_id_or_name)
    script = _profile_script_common() + r'''
agent_id = safe_agent_id(sys.argv[1])
path = profile_dir(agent_id)
if not path.is_dir():
    print("Agent not found", file=sys.stderr)
    sys.exit(4)
files = []
for base, relbase in [(path / "workspace", ""), (path, "")]:
    if not base.is_dir():
        continue
    for item in sorted(base.iterdir(), key=lambda p: p.name):
        if not item.is_file() or item.name.startswith("."):
            continue
        if base == path and item.name not in {"SOUL.md", "profile.json"}:
            continue
        stat = item.stat()
        files.append({"name": item.name, "path": item.name, "missing": False, "size": stat.st_size, "updatedAtMs": int(stat.st_mtime * 1000)})
print(json.dumps({"agentId": agent_id, "workspace": workspace_for(agent_id), "files": files}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, agent_id])
    exit_code, output = _exec_output(result)
    if exit_code == 4:
        raise HTTPException(status_code=404, detail="Agent not found")
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to list Hermes Agent files")
    return json.loads(output.decode("utf-8"))


def get_agent_profile_file_from_hermes_container(container_id_or_name: str | None, agent_id: str, name: str) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    file_name = _safe_agent_file_name(name)
    container = _container(container_id_or_name)
    script = _profile_script_common() + r'''
agent_id = safe_agent_id(sys.argv[1])
file_name = safe_file_name(sys.argv[2])
path = profile_dir(agent_id)
if not path.is_dir():
    print("Agent not found", file=sys.stderr)
    sys.exit(4)
target = path / "workspace" / file_name
if file_name == "SOUL.md":
    target = path / "SOUL.md"
if not target.is_file():
    print("Agent file not found", file=sys.stderr)
    sys.exit(5)
content = target.read_text(encoding="utf-8")
print(json.dumps({"agentId": agent_id, "workspace": workspace_for(agent_id), "file": {"name": file_name, "content": content}}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, agent_id, file_name])
    exit_code, output = _exec_output(result)
    if exit_code == 4:
        raise HTTPException(status_code=404, detail="Agent not found")
    if exit_code == 5:
        raise HTTPException(status_code=404, detail="Agent file not found")
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to read Hermes Agent file")
    return json.loads(output.decode("utf-8"))


def set_agent_profile_file_in_hermes_container(container_id_or_name: str | None, agent_id: str, name: str, content: str) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    file_name = _safe_agent_file_name(name)
    container = _container(container_id_or_name)
    payload = {"agentId": agent_id, "name": file_name, "content": content}
    script = _profile_script_common() + r'''
payload = json.loads(sys.argv[1])
agent_id = safe_agent_id(payload.get("agentId"))
file_name = safe_file_name(payload.get("name"))
path = profile_dir(agent_id)
if not path.is_dir():
    print("Agent not found", file=sys.stderr)
    sys.exit(4)
if agent_id in SYSTEM:
    print("System Agent is read-only", file=sys.stderr)
    sys.exit(3)
target = path / "workspace" / file_name
if file_name == "SOUL.md":
    target = path / "SOUL.md"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(payload.get("content") or "", encoding="utf-8")
if file_name == "IDENTITY.md":
    meta = meta_for(path)
    name, description, avatar = parse_identity(agent_id, payload.get("content") or "", meta)
    meta.update({"name": name, "description": description, "system": False})
    if avatar:
        meta["avatar"] = avatar
    (path / "profile.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"agentId": agent_id, "workspace": workspace_for(agent_id), "file": {"name": file_name, "content": payload.get("content") or ""}}, ensure_ascii=False))
'''
    result = container.exec_run(["python3", "-c", script, json.dumps(payload, ensure_ascii=False)])
    exit_code, output = _exec_output(result)
    if exit_code == 3:
        raise HTTPException(status_code=403, detail="System Agent is read-only")
    if exit_code == 4:
        raise HTTPException(status_code=404, detail="Agent not found")
    if exit_code != 0:
        raise HTTPException(status_code=500, detail=output.decode("utf-8", errors="replace") or "Failed to write Hermes Agent file")
    return json.loads(output.decode("utf-8"))


def agent_identity_prompt_from_hermes_container(container_id_or_name: str | None, agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    agent_id = _safe_agent_id(agent_id)
    try:
        profile_file = get_agent_profile_file_from_hermes_container(container_id_or_name, agent_id, "IDENTITY.md")
    except HTTPException:
        return None
    identity = str(profile_file.get("file", {}).get("content") or "").strip()
    if not identity:
        return None
    name = agent_id
    for line in identity.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            name = stripped.lstrip("#").strip() or agent_id
            break
    return "\n\n".join([
        f"You are the Agent named {name}.",
        "Always follow this Agent identity when answering. If the user asks who you are, answer as this Agent rather than as the default Hermes assistant.",
        f"Agent identity file:\n{identity}",
    ])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _agent_ids_from_defaults(defaults: dict[str, Any]) -> list[str]:
    agents = defaults.get("agents")
    if not isinstance(agents, dict):
        return []
    agent_list = agents.get("list")
    if not isinstance(agent_list, list):
        return []
    ids: list[str] = []
    for item in agent_list:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "").strip()
        if agent_id and agent_id not in ids:
            ids.append(agent_id)
    return ids


def _agent_config_by_id(defaults: dict[str, Any]) -> dict[str, dict[str, Any]]:
    agents = defaults.get("agents")
    if not isinstance(agents, dict):
        return {}
    agent_list = agents.get("list")
    if not isinstance(agent_list, list):
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in agent_list:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "").strip()
        if agent_id:
            by_id[agent_id] = item
    return by_id


def _agent_dirs() -> list[str]:
    root = _agents_dir()
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name and not entry.name.startswith(".")
    )


def list_agent_files(agent_id: str) -> dict[str, Any]:
    agent_dir = _agent_dir(agent_id)
    files: list[dict[str, Any]] = []
    for path in sorted(agent_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name.startswith("."):
            continue
        stat = path.stat()
        files.append({
            "name": path.name,
            "path": path.name,
            "missing": False,
            "size": stat.st_size,
            "updatedAtMs": int(stat.st_mtime * 1000),
        })
    return {
        "agentId": agent_id,
        "workspace": f"Agents/{agent_id}",
        "files": files,
    }


def get_agent_file(agent_id: str, name: str) -> dict[str, Any]:
    agent_dir = _agent_dir(agent_id)
    path = (agent_dir / name).resolve()
    if agent_dir not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Agent file not found")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail="Agent file is not UTF-8 text") from exc
    return {
        "agentId": agent_id,
        "workspace": f"Agents/{agent_id}",
        "file": {"name": path.name, "content": content},
    }


def set_agent_file(agent_id: str, name: str, content: str) -> dict[str, Any]:
    agent_dir = _agent_dir(agent_id)
    file_name = _safe_agent_file_name(name)
    path = (agent_dir / file_name).resolve()
    if agent_dir not in path.parents:
        raise HTTPException(status_code=400, detail="Invalid agent file name")
    path.write_text(content, encoding="utf-8")
    return {
        "agentId": agent_id,
        "workspace": f"Agents/{agent_id}",
        "file": {"name": path.name, "content": content},
    }


def _default_identity(agent_id: str, display_name: str | None = None, description: str | None = None) -> str:
    name = (display_name or agent_id).strip() or agent_id
    body = (description or "").strip()
    if body:
        return f"# {name}\n\n{body}\n"
    return f"# {name}\n\nYou are {name}, a helpful AI assistant.\n"


def create_agent(
    agent_id: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    workspace: str | None = None,
    avatar: str | None = None,
) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    resolved_name = (display_name or "").strip() or agent_id
    agents_dir = _agents_dir()
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / agent_id
    if path.exists():
        raise HTTPException(status_code=409, detail="Agent already exists")
    path.mkdir(parents=True)
    (path / "IDENTITY.md").write_text(_default_identity(agent_id, resolved_name, description), encoding="utf-8")

    defaults = _read_json(_defaults_path())
    agents = defaults.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        defaults["agents"] = agents
    agent_list = agents.setdefault("list", [])
    if not isinstance(agent_list, list):
        agent_list = []
        agents["list"] = agent_list
    entry = {
        "id": agent_id,
        "name": resolved_name,
        "workspace": workspace or f"Agents/{agent_id}",
    }
    if description:
        entry["description"] = description.strip()
    if avatar:
        entry["avatar"] = avatar
    agent_list.append(entry)
    _write_json(_defaults_path(), defaults)
    return {"ok": True, "agentId": agent_id, "name": resolved_name, "workspace": entry["workspace"]}


def update_agent(agent_id: str, *, name: str | None = None, avatar: str | None = None) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    _agent_dir(agent_id)
    defaults = _read_json(_defaults_path())
    agents = defaults.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        defaults["agents"] = agents
    agent_list = agents.setdefault("list", [])
    if not isinstance(agent_list, list):
        agent_list = []
        agents["list"] = agent_list
    entry = next((item for item in agent_list if isinstance(item, dict) and item.get("id") == agent_id), None)
    if entry is None:
        entry = {"id": agent_id, "workspace": f"Agents/{agent_id}"}
        agent_list.append(entry)
    if name is not None:
        entry["name"] = name
    if avatar is not None:
        entry["avatar"] = avatar
    _write_json(_defaults_path(), defaults)
    return {"ok": True, "agentId": agent_id}


def delete_agent(agent_id: str, *, delete_files: bool = False) -> dict[str, Any]:
    agent_id = _safe_agent_id(agent_id)
    path = _agent_dir(agent_id)
    defaults = _read_json(_defaults_path())
    agents = defaults.get("agents")
    if isinstance(agents, dict) and isinstance(agents.get("list"), list):
        agents["list"] = [
            item for item in agents["list"]
            if not (isinstance(item, dict) and item.get("id") == agent_id)
        ]
        _write_json(_defaults_path(), defaults)
    if delete_files:
        shutil.rmtree(path)
    return {"ok": True, "agentId": agent_id}


def _identity_name(agent_id: str) -> str:
    config = _agent_config_by_id(_read_json(_defaults_path())).get(agent_id, {})
    configured_name = str(config.get("name") or "").strip()
    if configured_name:
        return configured_name

    identity = _agents_dir() / agent_id / "IDENTITY.md"
    try:
        lines = identity.read_text(encoding="utf-8").splitlines()
    except OSError:
        return agent_id

    for line in lines:
        match = _IDENTITY_NAME_RE.search(line)
        if match:
            value = match.group(1).strip().strip("*").strip()
            if value:
                return value
    return agent_id


def _agent_description(agent_id: str) -> str:
    identity = _agents_dir() / agent_id / "IDENTITY.md"
    try:
        text = identity.read_text(encoding="utf-8")
    except OSError:
        return ""
    marker = "## 一句话简介"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1].strip()
    return next((line.strip() for line in tail.splitlines() if line.strip()), "")


def _resolve_agent_dir(agent_id: str) -> Path:
    normalized = str(agent_id or "").strip().replace("\\", "/").strip("/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    agent_dir = _agents_dir() / normalized
    if not agent_dir.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent_dir


def _resolve_agent_file(agent_id: str, name: str) -> Path:
    agent_dir = _resolve_agent_dir(agent_id)
    normalized = str(name or "").strip().replace("\\", "/").strip("/")
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent file not found")
    path = agent_dir / normalized
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent file not found")
    return path


# ---------------------------------------------------------------------------
# Container-based agent operations (read/write /opt/data/profiles/)
# ---------------------------------------------------------------------------

_LIST_AGENTS_SCRIPT = r"""
import json, re, os
from pathlib import Path

NAME_RE = re.compile(r'(?:\*\*)?\s*(?:名字|name)\s*(?:：|:)\s*(?:\*\*)?\s*(.+)', re.IGNORECASE)

def parse_identity(path):
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return '', ''
    name = ''
    desc = ''
    in_desc = False
    for line in lines:
        m = NAME_RE.search(line)
        if m and not name:
            name = m.group(1).strip().strip('*').strip()
        if '## 一句话简介' in line:
            in_desc = True
            continue
        if in_desc and line.strip() and not desc:
            desc = line.strip()
    return name, desc

profiles_dir = Path('/opt/data/profiles')
agents = []
if profiles_dir.exists():
    for d in sorted(profiles_dir.iterdir()):
        if not d.is_dir() or d.name.startswith('.'):
            continue
        identity = d / 'workspace' / 'IDENTITY.md'
        if not identity.exists():
            identity = d / 'IDENTITY.md'
        name, desc = parse_identity(identity)
        agents.append({
            'id': d.name,
            'name': name or d.name,
            'description': desc,
        })
print(json.dumps(agents, ensure_ascii=False))
"""


def _get_container(container_id_or_name: str):
    from app.container.manager import get_docker_container
    return get_docker_container(container_id_or_name)


def _container_name_for_user(user_id: str) -> str:
    from app.config import settings
    prefix = settings.dedicated_runtime_container_name_prefix
    return f"{prefix}-{user_id[:8]}"


def list_agents_from_container(container_id: str) -> list[dict]:
    try:
        container = _get_container(container_id)
        result = container.exec_run(["python3", "-c", _LIST_AGENTS_SCRIPT], user="hermes")
        if result.exit_code != 0:
            return []
        return json.loads(result.output.decode("utf-8"))
    except Exception:
        return []


def create_agent_in_container(container_id: str, agent_id: str) -> dict:
    container = _get_container(container_id)

    profile_dir = f"{CONTAINER_PROFILES_DIR}/{agent_id}"
    result = container.exec_run(["test", "-d", profile_dir], user="hermes")
    if result.exit_code == 0:
        raise HTTPException(status_code=409, detail=f"Agent '{agent_id}' 已存在")

    script = f"""
import os
from pathlib import Path

profile = Path('{profile_dir}')
for sub in ['memories', 'sessions', 'skills', 'skins', 'logs', 'plans', 'workspace', 'cron', 'home']:
    (profile / sub).mkdir(parents=True, exist_ok=True)

(profile / 'SOUL.md').write_text(
    '# SOUL.md - {agent_id}\\n\\n'
    '你是 {agent_id}，一个专业的 AI 助手。\\n'
    '请认真、准确地回答用户的问题。\\n',
    encoding='utf-8',
)
(profile / 'workspace' / 'IDENTITY.md').write_text(
    '# IDENTITY.md - {agent_id}\\n\\n'
    '- **名字：** {agent_id}\\n'
    '- **角色：** AI 助手\\n'
    '- **Emoji：** 🤖\\n\\n'
    '## 一句话简介\\n\\n'
    '我是 {agent_id} 智能助手。\\n',
    encoding='utf-8',
)
(profile / 'workspace' / 'AGENTS.md').write_text(
    '# AGENTS.md - {agent_id}\\n\\n'
    '本文件夹是 {agent_id} 的工作区。\\n',
    encoding='utf-8',
)
(profile / 'memories' / 'USER.md').write_text(
    '# USER.md - {agent_id}\\n\\n'
    '用户信息将在交互中逐步更新。\\n',
    encoding='utf-8',
)
print('ok')
"""
    result = container.exec_run(["python3", "-c", script], user="hermes")
    if result.exit_code != 0:
        output = result.output.decode("utf-8", errors="replace") if result.output else ""
        raise HTTPException(status_code=500, detail=f"创建 Agent 失败: {output}")

    return {"id": agent_id, "name": agent_id, "workspace": f"profiles/{agent_id}"}


def delete_agent_from_container(container_id: str, agent_id: str) -> dict:
    container = _get_container(container_id)

    profile_dir = f"{CONTAINER_PROFILES_DIR}/{agent_id}"
    result = container.exec_run(["test", "-d", profile_dir], user="hermes")
    if result.exit_code != 0:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' 不存在")

    result = container.exec_run(["rm", "-rf", profile_dir], user="hermes")
    if result.exit_code != 0:
        raise HTTPException(status_code=500, detail="删除 Agent 失败")

    return {"ok": True, "name": agent_id}


def list_agent_files_from_container(container_id: str, agent_id: str) -> dict:
    container = _get_container(container_id)

    script = f"""
import json
from pathlib import Path

profile = Path('{CONTAINER_PROFILES_DIR}/{agent_id}')
if not profile.is_dir():
    print(json.dumps({{"error": "not found"}}))
    exit(1)

files = []
for d in [profile, profile / 'workspace', profile / 'memories']:
    if not d.is_dir():
        continue
    for f in sorted(d.iterdir()):
        if f.is_file() and not f.name.startswith('.'):
            st = f.stat()
            files.append({{
                "name": f.name,
                "path": str(f.relative_to(profile)),
                "missing": False,
                "size": st.st_size,
                "updatedAtMs": int(st.st_mtime * 1000),
            }})
print(json.dumps({{"agentId": "{agent_id}", "workspace": "profiles/{agent_id}", "files": files}}, ensure_ascii=False))
"""
    result = container.exec_run(["python3", "-c", script], user="hermes")
    if result.exit_code != 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    return json.loads(result.output.decode("utf-8"))


def get_agent_file_from_container(container_id: str, agent_id: str, name: str) -> dict:
    container = _get_container(container_id)

    safe_name = name.replace("\\", "/").strip("/")
    if ".." in safe_name or not safe_name:
        raise HTTPException(status_code=400, detail="Invalid file name")

    # Try workspace/ first, then root, then memories/
    for prefix in [f"workspace/{safe_name}", safe_name, f"memories/{safe_name}"]:
        file_path = f"{CONTAINER_PROFILES_DIR}/{agent_id}/{prefix}"
        result = container.exec_run(["cat", file_path], user="hermes")
        if result.exit_code == 0:
            return {
                "agentId": agent_id,
                "workspace": f"profiles/{agent_id}",
                "file": {
                    "name": safe_name,
                    "content": result.output.decode("utf-8"),
                },
            }

    raise HTTPException(status_code=404, detail="Agent file not found")


# ---------------------------------------------------------------------------
# Legacy local-file functions (kept for backward compat)
# ---------------------------------------------------------------------------

def list_agent_files(agent_id: str) -> dict[str, Any]:
    agent_dir = _resolve_agent_dir(agent_id)
    files = []
    for path in sorted(item for item in agent_dir.iterdir() if item.is_file() and not item.name.startswith(".")):
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "path": f"Agents/{agent_dir.name}/{path.name}",
                "missing": False,
                "size": stat.st_size,
                "updatedAtMs": int(stat.st_mtime * 1000),
            }
        )
    return {
        "agentId": agent_dir.name,
        "workspace": f"Agents/{agent_dir.name}",
        "files": files,
    }


def get_agent_file(agent_id: str, name: str) -> dict[str, Any]:
    path = _resolve_agent_file(agent_id, name)
    return {
        "agentId": path.parent.name,
        "workspace": f"Agents/{path.parent.name}",
        "file": {
            "name": path.name,
            "content": path.read_text(encoding="utf-8"),
        },
    }


def _configured_agent_ids() -> list[str]:
    defaults = _read_json(_defaults_path())
    ids = _agent_ids_from_defaults(defaults)
    for agent_id in _agent_dirs():
        if agent_id not in ids:
            ids.append(agent_id)
    return ids


def _available_model_ids(models: list[Any]) -> set[str]:
    ids: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id:
            ids.add(model_id)
    return ids


def _fallback_agents_from_models(models: list[Any]) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    defaults = _read_json(_defaults_path())
    config_by_id = _agent_config_by_id(defaults)
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        display_name = str(config_by_id.get(model_id, {}).get("name") or "").strip() or model_id
        agents.append({
            "id": model_id,
            "name": display_name,
            "identity": {"name": display_name},
            "object": item.get("object", "model"),
            "available": True,
        })
    return agents


def agent_id_from_session_key(session_key: str | None) -> str | None:
    if not session_key:
        return None
    match = _SESSION_AGENT_RE.match(str(session_key))
    if not match:
        return None
    agent_id = match.group(1).strip()
    return agent_id or None


def model_for_session_key(session_key: str | None, fallback: str = DEFAULT_HERMES_MODEL) -> str:
    agent_id = agent_id_from_session_key(session_key)
    if not agent_id:
        return fallback
    return fallback


def _agent_description(agent_id: str) -> str:
    config = _agent_config_by_id(_read_json(_defaults_path())).get(agent_id, {})
    configured_description = str(config.get("description") or "").strip()
    if configured_description:
        return configured_description

    identity = _agents_dir() / agent_id / "IDENTITY.md"
    try:
        text = identity.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if lines[0].startswith("#"):
        lines = lines[1:]
    return next((line for line in lines if not line.startswith("#")), "")


def agent_identity_prompt(agent_id: str | None) -> str | None:
    if not agent_id:
        return None
    agent_id = _safe_agent_id(agent_id)
    if agent_id not in _configured_agent_ids():
        return None
    display_name = _identity_name(agent_id)
    try:
        identity = get_agent_file(agent_id, "IDENTITY.md").get("file", {}).get("content", "")
    except HTTPException:
        identity = ""
    identity = str(identity or "").strip()
    description = _agent_description(agent_id)
    parts = [
        f"You are the custom Agent named {display_name}.",
        "Always follow this Agent identity when answering. If the user asks who you are, answer as this Agent rather than as the default Hermes assistant.",
    ]
    if identity:
        parts.append(f"Agent identity file:\n{identity}")
    elif description:
        parts.append(f"Agent description:\n{description}")
    return "\n\n".join(parts)


def build_agent_info(
    models: list[Any],
    *,
    scope: str,
    runtime_mode: str,
    default_id: str | None = None,
    container_agents: list[dict] | None = None,
) -> dict[str, Any]:
    if container_agents is not None:
        agents: list[dict[str, Any]] = []
        for ca in container_agents:
            agents.append({
                "id": ca["id"],
                "name": ca.get("name") or ca["id"],
                "identity": {
                    "name": ca.get("name") or ca["id"],
                    "description": ca.get("description", ""),
                },
                "workspace": f"profiles/{ca['id']}",
                "available": True,
                "default": ca["id"] == "main",
            })
        if not agents:
            agents = _fallback_agents_from_models(models)
        resolved_default = default_id or next(
            (a["id"] for a in agents if a.get("default")), agents[0]["id"] if agents else DEFAULT_HERMES_MODEL
        )
        return {
            "agents": agents,
            "defaultId": resolved_default,
            "mainKey": f"agent:{resolved_default}",
            "scope": scope,
            "runtime_mode": runtime_mode,
        }

    configured_ids = _configured_agent_ids()
    if not configured_ids:
        agents = _fallback_agents_from_models(models)
        resolved_default = default_id or (agents[0]["id"] if agents else DEFAULT_HERMES_MODEL)
        return {
            "agents": agents,
            "defaultId": resolved_default,
            "mainKey": f"agent:{resolved_default}",
            "scope": scope,
            "runtime_mode": runtime_mode,
        }

    defaults = _read_json(_defaults_path())
    config_by_id = _agent_config_by_id(defaults)
    available = _available_model_ids(models)
    agents = []
    for agent_id in configured_ids:
        config = config_by_id.get(agent_id, {})
        agents.append({
            "id": agent_id,
            "name": _identity_name(agent_id),
            "identity": {
                "name": _identity_name(agent_id),
                "description": _agent_description(agent_id),
            },
            "workspace": config.get("workspace") or f"Agents/{agent_id}",
            "available": True,
            "default": bool(config.get("default")),
        })

    configured_default = next((item["id"] for item in agents if item.get("default")), agents[0]["id"])
    resolved_default = default_id if default_id in {item["id"] for item in agents} else configured_default
    return {
        "agents": agents,
        "defaultId": resolved_default,
        "mainKey": f"agent:{resolved_default}",
        "scope": scope,
        "runtime_mode": runtime_mode,
    }
