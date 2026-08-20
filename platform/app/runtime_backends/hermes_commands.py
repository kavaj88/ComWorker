from __future__ import annotations

import json
import re
from typing import Any

from docker.errors import APIError as DockerAPIError
from docker.errors import NotFound as DockerNotFound
from fastapi import HTTPException, status

from app.container.manager import get_docker_container
from app.runtime_backends.hermes_files import _exec_output

_LIST_COMMANDS_SCRIPT = r"""
import json
import re
import sys
from pathlib import Path

try:
    from hermes_cli.commands import COMMAND_REGISTRY
except Exception as exc:
    print(json.dumps({"error": f"failed to import Hermes command registry: {exc}"}))
    raise SystemExit(2)

INVALID_SKILL_CHARS = re.compile(r"[^a-z0-9-]")
MULTI_HYPHEN = re.compile(r"-{2,}")


def clean_scalar(value):
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def frontmatter(path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description"}:
            out[key] = clean_scalar(value)
    return out


def skill_slug(name):
    slug = str(name or "").lower().replace(" ", "-").replace("_", "-")
    slug = INVALID_SKILL_CHARS.sub("", slug)
    slug = MULTI_HYPHEN.sub("-", slug).strip("-")
    return slug


def command_category(category):
    normalized = str(category or "").strip().lower()
    if normalized == "session":
        return "session"
    if normalized == "configuration":
        return "options"
    if normalized == "tools & skills":
        return "tools"
    if normalized == "info":
        return "status"
    if normalized == "exit":
        return "management"
    return normalized or "other"


commands = []
for cmd in COMMAND_REGISTRY:
    if getattr(cmd, "cli_only", False):
        continue
    commands.append({
        "name": cmd.name,
        "description": cmd.description,
        "argument_hint": getattr(cmd, "args_hint", "") or None,
        "aliases": list(getattr(cmd, "aliases", ()) or ()),
        "category": command_category(getattr(cmd, "category", "")),
        "scope": "both",
        "source": "builtin",
        "skill_name": None,
    })

seen_skills = set()
for root in [Path("/opt/data/skills"), Path("/opt/data/profiles") / sys.argv[1] / "skills"]:
    if not root.exists():
        continue
    for skill_file in sorted(root.rglob("SKILL.md")):
        if (skill_file.parent / ".comworker-disabled").exists():
            continue
        meta = frontmatter(skill_file)
        name = meta.get("name") or skill_file.parent.name
        slug = skill_slug(name)
        if not slug or slug in seen_skills:
            continue
        seen_skills.add(slug)
        commands.append({
            "name": slug,
            "description": meta.get("description", "") or f"Invoke the {name} skill",
            "argument_hint": None,
            "aliases": [],
            "category": "skills",
            "scope": "text",
            "source": "skill",
            "skill_name": name,
        })

print(json.dumps({"commands": commands}, ensure_ascii=False))
"""


def list_hermes_commands_from_container(container_id_or_name: str | None, agent_id: str = "") -> dict[str, Any]:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )

    safe_agent_id = _safe_agent_id(agent_id or "main")
    try:
        container = get_docker_container(container_id_or_name)
        result = None
        for python_bin in ("/opt/hermes/.venv/bin/python", "/usr/local/bin/python", "python3"):
            result = container.exec_run([python_bin, "-c", _LIST_COMMANDS_SCRIPT, safe_agent_id])
            exit_code, output = _exec_output(result)
            if exit_code in (126, 127) and python_bin != "python3":
                continue
            break
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    except DockerAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list Hermes commands",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to find Python in Hermes runtime container",
        )
    try:
        payload = json.loads(output.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected Hermes commands response",
        ) from exc

    if exit_code != 0:
        detail = str(payload.get("error") or "Failed to list Hermes commands") if isinstance(payload, dict) else "Failed to list Hermes commands"
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)

    commands = payload.get("commands") if isinstance(payload, dict) else []
    if not isinstance(commands, list):
        commands = []
    return {
        "agentId": safe_agent_id,
        "commands": [command for command in commands if isinstance(command, dict) and command.get("name")],
        "runtime": "hermes",
        "source": "hermes-container-registry",
    }


def _safe_agent_id(value: str) -> str:
    cleaned = (value or "main").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", cleaned):
        return cleaned
    return "main"
