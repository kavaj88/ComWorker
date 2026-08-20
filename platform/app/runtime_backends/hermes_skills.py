from __future__ import annotations

import io
import json
import logging
import mimetypes
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

from docker.errors import APIError as DockerAPIError
from docker.errors import NotFound as DockerNotFound
from fastapi import HTTPException, UploadFile, status

from app.container.manager import get_docker_container

logger = logging.getLogger(__name__)

HERMES_SKILLS_ROOT = "/opt/data/skills"
HERMES_PROFILES_ROOT = "/opt/data/profiles"
# User-level "installed" marker for the skill store. This is a pure
# organizational flag (independent of whether the skill is physically
# present / enabled) so the store can show the full catalog while the
# Installed tab lists only skills the user has chosen to install. Stored on
# the persistent volume so it survives container recreation.
INSTALLED_MARKER_PATH = "/opt/data/.skillstore-installed.json"
_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,80}$")
_GIT_SCAN_CACHE: dict[str, dict] = {}


def _catalog_skill_names(docker_id: str) -> set[str]:
    """Return the skill names registered in the bundled catalog."""
    try:
        container = get_docker_container(docker_id)
        out = container.exec_run(["cat", "/opt/hermes/skills/catalog.json"])
        if out.exit_code == 0 and out.output.strip():
            data = json.loads(out.output.decode("utf-8", "replace"))
            return set(data.get("skills", {}).keys())
    except Exception:
        logger.debug("_catalog_skill_names failed", exc_info=True)
    return set()


def read_installed_marker(docker_id: str) -> dict[str, set[str]]:
    """Return the installed-skill sets, split by source.

    Returns ``{"user": set, "managed": set}``. "user" skills were installed by
    the end user and can be uninstalled; "managed" skills were pushed by an
    admin and the user cannot uninstall them. Until a marker file exists we
    default to *every* catalog skill being installed as "user" — built-in
    skills are available out of the box and the user opts out via "卸载".
    Once a marker file exists we trust it literally.
    """
    container = get_docker_container(docker_id)
    try:
        out = container.exec_run(["cat", INSTALLED_MARKER_PATH])
        if out.exit_code == 0 and out.output.strip():
            data = json.loads(out.output.decode("utf-8", "replace"))
            user = set(data.get("user", []))
            # Legacy single-list format: fold "installed" into "user".
            if "installed" in data:
                user |= set(data["installed"])
            managed = set(data.get("managed", []))
            return {"user": user, "managed": managed}
    except Exception:
        logger.debug("read_installed_marker failed", exc_info=True)
    # No marker yet → default every catalog skill to "installed" (legacy behavior).
    return {"user": _catalog_skill_names(docker_id), "managed": set()}


def write_installed_marker(
    docker_id: str, user: set[str], managed: set[str]
) -> dict[str, list[str]]:
    """Persist the installed-skill sets to the user's container volume."""
    container = get_docker_container(docker_id)
    payload = json.dumps(
        {"user": sorted(user), "managed": sorted(managed)},
        ensure_ascii=False,
    ).encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=".skillstore-installed.json")
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    buf.seek(0)
    container.put_archive("/opt/data", buf.read())
    return {"user": sorted(user), "managed": sorted(managed)}


_LIST_SKILLS_SCRIPT = r"""
import hashlib
import json
from pathlib import Path

root = Path("/opt/data/skills")


def clean_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def frontmatter(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description"}:
            result[key] = clean_scalar(value)
    return result


def dir_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        try:
            rel = item.relative_to(path).as_posix()
            digest.update(rel.encode("utf-8", errors="replace"))
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
        except OSError:
            continue
    return digest.hexdigest()


def load_catalog(root):
    # Read skills/catalog.json. The catalog is the authoritative list of built-in
    # skills and their classification. It lives next to the bundled skills dir at
    # /opt/hermes/skills (mounted read-only from the host), so it is always fresh
    # and survives container recreation. We check the bundled location first, then
    # the synced user dir (root) as a fallback. Driving the listing from the
    # catalog means adding a category (or a skill) is a config change, not a code
    # change, and nested SKILL.md files (sub-agents/references) are never surfaced.
    bundled = Path("/opt/hermes/skills/catalog.json")
    for cand in (bundled, root / "catalog.json"):
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("skills"):
                return data
    return None


skills = []
catalog = load_catalog(root)
cat_map = {}
if isinstance(catalog, dict) and catalog.get("skills"):
    cat_map = catalog["skills"] or {}

# 1) Built-in skills registered in the catalog — classified, deduped by dir name.
covered_dirs = set()
for name, meta in cat_map.items():
    meta = meta or {}
    skill_file = root / name / "SKILL.md"
    if not skill_file.is_file():
        continue
    fm = frontmatter(skill_file)
    covered_dirs.add(name)
    skills.append(
        {
            "name": fm.get("name") or name,
            "title": meta.get("title") or (fm.get("name") or  name),
            "description": meta.get("description") or fm.get("description", ""),
            "category": meta.get("category", ""),
            "tags": meta.get("tags", []),
            "source": "hermes",
            "owner": "admin",
            "disabled": (skill_file.parent / ".comworker-disabled").exists(),
            "path": name,
            "fingerprint": dir_fingerprint(skill_file.parent),
        }
    )

# 2) Surface any skill present in the user dir but NOT in the catalog (e.g. a
#    user self-installed skill) so it stays visible in the store. A "skill" is a
#    directory that directly contains SKILL.md and is NOT nested inside another
#    skill (none of its ancestors under root also contain SKILL.md). This stops
#    nested sub-agent / bundle SKILL.md files from being surfaced as standalone
#    store entries, while still surfacing real sub-skills of bundle repos that
#    have no owning top-level SKILL.md of their own.
SUPPORT_DIRS = {"references", "templates", "assets", "scripts"}
EXCLUDED_DIRS = {".git", ".github", ".hub", ".archive", ".venv", "venv",
                 "node_modules", "site-packages", "__pycache__", ".tox", ".nox"}

candidate_parents = []
for skill_file in sorted(root.rglob("SKILL.md")):
    parent = skill_file.parent
    dir_name = parent.relative_to(root).parts[-1]
    if dir_name in EXCLUDED_DIRS or dir_name in SUPPORT_DIRS:
        continue
    # Does an ancestor (strictly above parent) own a SKILL.md? If so, this file
    # is nested inside another skill and must not be surfaced on its own.
    anc = parent.parent
    nested = False
    while anc != root:
        if (anc / "SKILL.md").is_file():
            nested = True
            break
        anc = anc.parent
    if nested:
        continue
    candidate_parents.append(parent)

# Dedupe by frontmatter name; prefer the shallowest (top-level) entry so a
# bundled copy never shadows its flat, catalog-classified counterpart.
by_name = {}
for parent in candidate_parents:
    dir_name = parent.relative_to(root).parts[-1]
    if dir_name in covered_dirs:
        continue
    fm = frontmatter(parent / "SKILL.md")
    name = fm.get("name") or dir_name
    depth = len(parent.relative_to(root).parts)
    prev = by_name.get(name)
    if prev is None or depth < prev[0]:
        by_name[name] = (depth, parent, dir_name, fm)

for depth, parent, dir_name, fm in by_name.values():
    skills.append(
        {
            "name": fm.get("name") or dir_name,
            "description": fm.get("description", ""),
            "category": "",
            "tags": [],
            "source": "hermes",
            "owner": "user",
            "disabled": (parent / ".comworker-disabled").exists(),
            "path": str(parent.relative_to(root)),
            "fingerprint": dir_fingerprint(parent),
        }
    )

print(json.dumps(skills, ensure_ascii=False))
"""

_RESOLVE_SKILL_SCRIPT = r"""
def clean_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def frontmatter_name(path):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        if key.strip() == "name":
            return clean_scalar(value)
    return ""


def resolve_skill_dir(root, skill):
    direct = os.path.realpath(os.path.join(root, skill))
    root_real = os.path.realpath(root)
    if direct.startswith(root_real + os.sep) and os.path.isdir(direct):
        return direct
    matches = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if "SKILL.md" not in filenames:
            continue
        skill_file = Path(dirpath) / "SKILL.md"
        if frontmatter_name(skill_file) == skill or os.path.basename(dirpath) == skill:
            real = os.path.realpath(dirpath)
            if real.startswith(root_real + os.sep):
                matches.append(real)
    return sorted(matches)[0] if matches else ""
"""


def _exec_output(result) -> tuple[int, bytes]:
    if isinstance(result, tuple):
        exit_code, output = result
    else:
        exit_code = getattr(result, "exit_code", 0)
        output = getattr(result, "output", b"")
    if isinstance(output, str):
        output = output.encode("utf-8")
    return int(exit_code or 0), output or b""


def _clean_skill_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _skill_frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"name", "description"}:
            result[key] = _clean_skill_scalar(value)
    return result


def _safe_skill_name(value: str) -> str:
    normalized = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    normalized = normalized.removesuffix(".zip").strip()
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", normalized).strip(".-")
    if not normalized or not _SKILL_NAME_RE.match(normalized):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid skill name")
    return normalized


def _zip_member_target(member_name: str, prefix: str) -> str | None:
    raw = member_name.replace("\\", "/").strip("/")
    if not raw or raw.endswith("/"):
        return None
    normalized = posixpath.normpath(raw)
    if normalized in {"", ".", ".."} or normalized.startswith("../") or "/../" in normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill zip contains unsafe paths")
    if prefix and (normalized == prefix or normalized.startswith(f"{prefix}/")):
        normalized = normalized[len(prefix) :].lstrip("/")
    if not normalized or normalized in {".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def _safe_skill_path(value: str) -> str:
    parts = [part for part in value.strip().replace("\\", "/").strip("/").split("/") if part]
    if not parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid skill path")
    return "/".join(_safe_skill_name(part) for part in parts)


def _archive_root_from_prefix(prefix: str, filename: str | None) -> str | None:
    first_part = prefix.strip("/").split("/", 1)[0].strip() if prefix.strip("/") else ""
    candidate = first_part or filename or ""
    if not candidate:
        return None
    try:
        return _safe_skill_name(candidate)
    except HTTPException:
        return None


def _build_skills_archive(skill_files: dict[str, dict[str, bytes]]) -> bytes:
    tar_buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        created_dirs: set[str] = set()

        def ensure_dirs(path: str) -> None:
            current = ""
            for part in path.split("/"):
                if not part:
                    continue
                current = f"{current}/{part}" if current else part
                if current in created_dirs:
                    continue
                directory = tarfile.TarInfo(name=current)
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                directory.mtime = now
                tar.addfile(directory)
                created_dirs.add(current)

        for skill_path, files in sorted(skill_files.items()):
            ensure_dirs(skill_path)
            for rel_path, contents in sorted(files.items()):
                target_path = f"{skill_path}/{rel_path}"
                ensure_dirs(posixpath.dirname(target_path))
                info = tarfile.TarInfo(name=target_path)
                info.size = len(contents)
                info.mode = 0o644
                info.mtime = now
                tar.addfile(info, io.BytesIO(contents))

    tar_buffer.seek(0)
    return tar_buffer.read()


def _build_skill_archive(skill_name: str, files: dict[str, bytes]) -> bytes:
    return _build_skills_archive({skill_name: files})


def _scope_root(scope: str | None, agent_id: str | None = None) -> str:
    if scope == "agent":
        agent = _safe_skill_name(agent_id or "")
        return f"{HERMES_PROFILES_ROOT}/{agent}/skills"
    return HERMES_SKILLS_ROOT


def _scope_metadata(scope: str | None, agent_id: str | None = None) -> tuple[str, str, str]:
    scope_type = "agent" if scope == "agent" else "global"
    scope_id = f"agent:{agent_id}" if scope_type == "agent" and agent_id else scope_type
    scope_label = f"{agent_id} 技能" if scope_type == "agent" and agent_id else "全局技能"
    return scope_type, scope_id, scope_label


def _safe_rel_path(value: str | None) -> str:
    raw = (value or "").strip().replace("\\", "/").strip("/")
    normalized = posixpath.normpath(raw)
    if not normalized or normalized in {".", ".."} or normalized.startswith("../") or "/../" in normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid skill file path")
    return normalized


def _exec_json(container_id_or_name: str | None, script: str, *args: str):
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )
    try:
        container = get_docker_container(container_id_or_name)
        result = container.exec_run(["python3", "-c", script, *args])
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    except DockerAPIError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Hermes skill operation failed") from exc
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        detail = output.decode("utf-8", errors="replace") or "Hermes skill operation failed"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    try:
        return json.loads(output.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected Hermes skills response") from exc


def _put_skill_archive(
    container_id_or_name: str | None,
    root: str,
    skill_files: dict[str, dict[str, bytes]],
) -> None:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )
    archive = _build_skills_archive(skill_files)
    try:
        container = get_docker_container(container_id_or_name)
        result = container.exec_run(["mkdir", "-p", root])
        exit_code, _output = _exec_output(result)
        if exit_code != 0:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to prepare Hermes skills directory")
        ok = container.put_archive(root, archive)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    except DockerAPIError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to install Hermes skill") from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to install Hermes skill")


def skill_scopes(agent_ids: list[str] | None = None) -> list[dict]:
    scopes = [
        {
            "id": "global",
            "type": "global",
            "label": "全局技能",
            "path": HERMES_SKILLS_ROOT,
            "writable": True,
        }
    ]
    for agent_id in agent_ids or []:
        scopes.append(
            {
                "id": f"agent:{agent_id}",
                "type": "agent",
                "label": f"{agent_id} 技能",
                "path": f"{HERMES_PROFILES_ROOT}/{_safe_skill_name(agent_id)}/skills",
                "agentId": agent_id,
                "writable": True,
            }
        )
    return scopes


async def upload_skill_zip_to_hermes_container(
    container_id_or_name: str | None,
    file: UploadFile,
    scope: str | None = None,
    agent_id: str | None = None,
) -> dict:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )

    contents = await file.read()
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded skill must be a zip file") from exc

    names = [name for name in zip_file.namelist() if not name.endswith("/")]
    skill_md_names = sorted(
        name for name in names if name.replace("\\", "/").strip("/").endswith("SKILL.md")
    )
    if not skill_md_names:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill zip must contain SKILL.md")

    multi_skill = len(skill_md_names) > 1
    archive_root = _archive_root_from_prefix(skill_md_names[0], file.filename) if multi_skill else None
    skill_files: dict[str, dict[str, bytes]] = {}
    installed: list[dict] = []

    for skill_md_name in skill_md_names:
        skill_md_parts = skill_md_name.replace("\\", "/").strip("/").split("/")
        prefix = "/".join(skill_md_parts[:-1]) if len(skill_md_parts) > 1 else ""
        skill_md = zip_file.read(skill_md_name).decode("utf-8", errors="replace")
        meta = _skill_frontmatter(skill_md)
        skill_name = _safe_skill_name(meta.get("name") or prefix or file.filename or "uploaded-skill")
        install_path = _safe_skill_path(f"{archive_root}/{skill_name}" if archive_root else skill_name)

        extracted: dict[str, bytes] = {}
        for name in names:
            normalized_name = name.replace("\\", "/").strip("/")
            if prefix and not (normalized_name == prefix or normalized_name.startswith(f"{prefix}/")):
                continue
            target = _zip_member_target(name, prefix)
            if target is None:
                continue
            extracted[target] = zip_file.read(name)
        if "SKILL.md" not in extracted:
            extracted["SKILL.md"] = skill_md.encode("utf-8")
        skill_files[install_path] = extracted
        installed.append(
            {
                "name": skill_name,
                "description": meta.get("description", ""),
                "source": "hermes",
                "disabled": False,
                "path": install_path,
            }
        )

    scope_root = _scope_root(scope, agent_id)
    _put_skill_archive(container_id_or_name, scope_root, skill_files)
    scope_type, scope_id, scope_label = _scope_metadata(scope, agent_id)

    payload = {
        "name": installed[0]["name"],
        "description": installed[0]["description"],
        "source": "hermes",
        "disabled": False,
        "scope": scope_id,
        "scopeType": scope_type,
        "scopeLabel": scope_label,
        "agentId": agent_id if scope_type == "agent" else None,
        "path": installed[0].get("path", installed[0]["name"]),
        "dirPath": installed[0].get("path", installed[0]["name"]),
        "available": True,
        "writable": True,
    }
    if len(installed) > 1:
        payload["installed"] = installed
    return payload


def _raw_skills_from_hermes_container(
    container_id_or_name: str | None,
    scope: str | None = None,
    agent_id: str | None = None,
) -> list[dict]:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )

    try:
        container = get_docker_container(container_id_or_name)
        root = _scope_root(scope, agent_id)
        script = _LIST_SKILLS_SCRIPT.replace('root = Path("/opt/data/skills")', "import sys\nroot = Path(sys.argv[1])")
        result = container.exec_run(["python3", "-c", script, root])
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    except DockerAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list Hermes skills",
        ) from exc

    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list Hermes skills",
        )

    try:
        payload = json.loads(output.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected Hermes skills response",
        ) from exc

    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("name")]


def list_skills_from_hermes_container(
    container_id_or_name: str | None,
    scope: str | None = None,
    agent_id: str | None = None,
    *,
    inherited_global: bool = False,
) -> list[dict]:
    payload = _raw_skills_from_hermes_container(container_id_or_name, scope, agent_id)
    if scope == "agent" and not inherited_global:
        global_skills = _raw_skills_from_hermes_container(container_id_or_name, scope="global")
        inherited = {
            (str(item.get("name") or ""), str(item.get("fingerprint") or ""))
            for item in global_skills
            if item.get("name") and item.get("fingerprint")
        }
        payload = [
            item for item in payload
            if (str(item.get("name") or ""), str(item.get("fingerprint") or "")) not in inherited
        ]

    scope_type, scope_id, scope_label = _scope_metadata(scope, agent_id)
    normalized = []
    for item in payload:
        path = str(item.get("path") or item.get("name") or "")
        public_item = {key: value for key, value in item.items() if key != "fingerprint"}
        normalized.append({
            **public_item,
            "scope": scope_id,
            "scopeType": scope_type,
            "scopeLabel": scope_label,
            "agentId": agent_id if scope == "agent" else None,
            "available": True,
            "disabled": bool(item.get("disabled", False)),
            "writable": True,
            "dirPath": path,
        })
    return normalized


def install_existing_skill_to_hermes_scope(
    container_id_or_name: str | None,
    skill_name: str,
    scope: str | None = None,
    agent_id: str | None = None,
) -> dict:
    skill = _safe_skill_name(skill_name)
    source_root = HERMES_SKILLS_ROOT
    target_root = _scope_root(scope, agent_id)
    script = _RESOLVE_SKILL_SCRIPT + r"""
import json, os, shutil, sys
from pathlib import Path
source_root, target_root, skill = sys.argv[1], sys.argv[2], sys.argv[3]
source = resolve_skill_dir(source_root, skill)
if not source:
    raise SystemExit("skill not found")
source_real = os.path.realpath(source)
source_root_real = os.path.realpath(source_root)
target_root_real = os.path.realpath(target_root)
if not source_real.startswith(source_root_real + os.sep):
    raise SystemExit("invalid source")
target = os.path.realpath(os.path.join(target_root, os.path.basename(source_real)))
if not target.startswith(target_root_real + os.sep):
    raise SystemExit("invalid target")
os.makedirs(target_root, exist_ok=True)
if source_real != target:
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(source_real, target)
print(json.dumps({"ok": True, "name": skill, "path": os.path.relpath(target, target_root).replace(os.sep, "/")}, ensure_ascii=False))
"""
    payload = _exec_json(container_id_or_name, script, source_root, target_root, skill)
    scope_type, scope_id, scope_label = _scope_metadata(scope, agent_id)
    return {
        "ok": True,
        "output": "installed",
        "name": payload.get("name", skill) if isinstance(payload, dict) else skill,
        "scope": scope_id,
        "scopeType": scope_type,
        "scopeLabel": scope_label,
        "agentId": agent_id if scope_type == "agent" else None,
    }


def _scan_skill_dir(skill_dir: Path, root: Path) -> dict:
    skill_file = skill_dir / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8", errors="replace")
    meta = _skill_frontmatter(content)
    name = _safe_skill_name(meta.get("name") or skill_dir.name)
    return {
        "name": name,
        "description": meta.get("description", ""),
        "relativePath": skill_dir.relative_to(root).as_posix(),
    }


def scan_git_skills(url: str) -> dict:
    source_url = url.strip()
    if not source_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git URL is required")

    cache_key = f"git-{int(time.time() * 1000)}"
    temp_dir = Path(tempfile.mkdtemp(prefix="comworker-skill-git-"))
    repo_dir = temp_dir / "repo"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", source_url, str(repo_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = "Failed to clone Git repository"
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = exc.stderr.strip()[-500:]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from exc

    skills = []
    for skill_file in sorted(repo_dir.rglob("SKILL.md")):
        if ".git" in skill_file.parts:
            continue
        try:
            skills.append(_scan_skill_dir(skill_file.parent, repo_dir))
        except HTTPException:
            continue

    repo_name = source_url.rstrip("/").split("/")[-1].removesuffix(".git") or repo_dir.name
    _GIT_SCAN_CACHE[cache_key] = {
        "tempDir": str(temp_dir),
        "repoDir": str(repo_dir),
        "repo": source_url,
        "repoName": repo_name,
        "skills": skills,
        "createdAt": time.time(),
    }
    return {"repo": source_url, "repoName": repo_name, "skills": skills, "cacheKey": cache_key}


def install_git_skills_to_hermes_container(
    container_id_or_name: str | None,
    cache_key: str,
    skill_names: list[str],
    scope: str | None = None,
    agent_id: str | None = None,
) -> dict:
    cached = _GIT_SCAN_CACHE.get(cache_key)
    if not cached:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Git scan cache expired; scan the repository again")
    repo_dir = Path(str(cached["repoDir"])).resolve()
    selected = {_safe_skill_name(name) for name in skill_names}
    available = {str(item["name"]): item for item in cached.get("skills", []) if isinstance(item, dict)}
    if not selected:
        selected = set(available)

    skill_files: dict[str, dict[str, bytes]] = {}
    installed: list[str] = []
    errors: list[str] = []
    for name in sorted(selected):
        item = available.get(name)
        if not item:
            errors.append(f"{name}: not found")
            continue
        rel_path = _safe_skill_path(str(item.get("relativePath") or name))
        source_dir = (repo_dir / rel_path).resolve()
        try:
            source_dir.relative_to(repo_dir)
        except ValueError:
            errors.append(f"{name}: invalid path")
            continue
        files: dict[str, bytes] = {}
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel = path.relative_to(source_dir).as_posix()
            files[_safe_rel_path(rel)] = path.read_bytes()
        if "SKILL.md" not in files:
            errors.append(f"{name}: missing SKILL.md")
            continue
        skill_files[name] = files
        installed.append(name)

    if skill_files:
        _put_skill_archive(container_id_or_name, _scope_root(scope, agent_id), skill_files)
    return {"ok": len(errors) == 0, "installed": installed, "errors": errors}


def delete_skill_from_hermes_container(container_id_or_name: str | None, skill_name: str, scope: str | None = None, agent_id: str | None = None) -> dict:
    skill = _safe_skill_name(skill_name)
    root = _scope_root(scope, agent_id)
    script = _RESOLVE_SKILL_SCRIPT + r"""
import os, shutil, sys
from pathlib import Path
root, skill = sys.argv[1], sys.argv[2]
target = resolve_skill_dir(root, skill)
root_real = os.path.realpath(root)
if not target or not target.startswith(root_real + os.sep):
    raise SystemExit("skill not found")
shutil.rmtree(target)
print('{"ok": true}')
"""
    return _exec_json(container_id_or_name, script, root, skill)


def set_skill_disabled_in_hermes_container(
    container_id_or_name: str | None,
    skill_name: str,
    disabled: bool,
    scope: str | None = None,
    agent_id: str | None = None,
) -> dict:
    skill = _safe_skill_name(skill_name)
    root = _scope_root(scope, agent_id)
    script = _RESOLVE_SKILL_SCRIPT + r"""
import json, os, sys
root, skill, disabled_value = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
target = resolve_skill_dir(root, skill)
root_real = os.path.realpath(root)
if not target or not target.startswith(root_real + os.sep):
    raise SystemExit("skill not found")
marker = os.path.join(target, ".comworker-disabled")
if disabled_value:
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("disabled\n")
else:
    try:
        os.remove(marker)
    except FileNotFoundError:
        pass
print(json.dumps({"ok": True, "name": skill, "disabled": disabled_value}))
"""
    return _exec_json(container_id_or_name, script, root, skill, "1" if disabled else "0")


def list_skill_files_from_hermes_container(container_id_or_name: str | None, skill_name: str, scope: str | None = None, agent_id: str | None = None) -> dict:
    skill = _safe_skill_name(skill_name)
    root = _scope_root(scope, agent_id)
    script = _RESOLVE_SKILL_SCRIPT + r"""
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
root, skill = sys.argv[1], sys.argv[2]
target = resolve_skill_dir(root, skill)
root_real = os.path.realpath(root)
if not target or not target.startswith(root_real + os.sep) or not os.path.isdir(target):
    raise SystemExit("skill not found")
files = []
for dirpath, _dirnames, filenames in os.walk(target):
    for filename in filenames:
        path = os.path.join(dirpath, filename)
        rel = os.path.relpath(path, target).replace(os.sep, "/")
        stat = os.stat(path)
        ext = os.path.splitext(filename)[1].lower()
        files.append({
            "name": filename,
            "path": rel,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "editable": ext in {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sh"},
        })
print(json.dumps({"files": sorted(files, key=lambda item: item["path"])}, ensure_ascii=False))
"""
    payload = _exec_json(container_id_or_name, script, root, skill)
    return {"skill": {"name": skill}, "files": payload.get("files", []) if isinstance(payload, dict) else []}


def read_skill_file_from_hermes_container(container_id_or_name: str | None, skill_name: str, file_path: str, scope: str | None = None, agent_id: str | None = None) -> dict:
    skill = _safe_skill_name(skill_name)
    rel = _safe_rel_path(file_path)
    root = _scope_root(scope, agent_id)
    script = _RESOLVE_SKILL_SCRIPT + r"""
import json, os, sys
from pathlib import Path
root, skill, rel = sys.argv[1], sys.argv[2], sys.argv[3]
base = resolve_skill_dir(root, skill)
if not base:
    raise SystemExit("skill not found")
target = os.path.realpath(os.path.join(base, rel))
if not target.startswith(base + os.sep) or not os.path.isfile(target):
    raise SystemExit("file not found")
with open(target, "r", encoding="utf-8", errors="replace") as fh:
    content = fh.read()
print(json.dumps({"path": rel, "name": os.path.basename(target), "content": content}, ensure_ascii=False))
"""
    return _exec_json(container_id_or_name, script, root, skill, rel)


def write_skill_file_to_hermes_container(container_id_or_name: str | None, skill_name: str, file_path: str, content: str, scope: str | None = None, agent_id: str | None = None) -> dict:
    skill = _safe_skill_name(skill_name)
    rel = _safe_rel_path(file_path)
    root = _scope_root(scope, agent_id)
    script = _RESOLVE_SKILL_SCRIPT + r"""
import json, os, sys
from pathlib import Path
root, skill, rel, content = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
base = resolve_skill_dir(root, skill)
if not base:
    raise SystemExit("skill not found")
target = os.path.realpath(os.path.join(base, rel))
if not target.startswith(base + os.sep):
    raise SystemExit("invalid path")
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as fh:
    fh.write(content)
print(json.dumps({"ok": True, "path": rel}, ensure_ascii=False))
"""
    return _exec_json(container_id_or_name, script, root, skill, rel, content)


def skill_zip_from_hermes_container(container_id_or_name: str | None, skill_name: str, scope: str | None = None, agent_id: str | None = None) -> tuple[bytes, str]:
    if not container_id_or_name:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hermes runtime container is unavailable")
    skill = _safe_skill_name(skill_name)
    root = _scope_root(scope, agent_id)
    try:
        container = get_docker_container(container_id_or_name)
        resolve_script = _RESOLVE_SKILL_SCRIPT + r"""
import json, os, sys
from pathlib import Path
root, skill = sys.argv[1], sys.argv[2]
target = resolve_skill_dir(root, skill)
if not target:
    raise SystemExit("skill not found")
print(json.dumps({"path": target}, ensure_ascii=False))
"""
        result = container.exec_run(["python3", "-c", resolve_script, root, skill])
        exit_code, output = _exec_output(result)
        if exit_code != 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hermes skill not found")
        payload = json.loads(output.decode("utf-8"))
        stream, _stat = container.get_archive(payload["path"])
    except DockerNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hermes skill not found") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected Hermes skills response") from exc
    except DockerAPIError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to download Hermes skill") from exc

    source_tar = b"".join(stream)
    zip_buffer = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(source_tar), mode="r:*") as tar, zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in tar:
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            rel = member.name.split("/", 1)[-1] if "/" in member.name else member.name
            zf.writestr(f"{skill}/{rel}", extracted.read())
    return zip_buffer.getvalue(), mimetypes.types_map.get(".zip", "application/zip")


def download_skill_from_hermes_container(container_id_or_name: str | None, skill_name: str, scope: str | None = None, agent_id: str | None = None) -> bytes:
    """Download a skill from a Hermes container as a zip archive (bytes)."""
    data, _ = skill_zip_from_hermes_container(container_id_or_name, skill_name, scope, agent_id)
    return data
