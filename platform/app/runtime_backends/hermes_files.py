from __future__ import annotations

import io
import json
import mimetypes
import posixpath
import shlex
import tarfile
import time
import zipfile

from docker.errors import APIError as DockerAPIError
from docker.errors import NotFound as DockerNotFound
from fastapi import HTTPException, UploadFile, status

from app.container.manager import get_docker_container

DEFAULT_HERMES_UPLOAD_DIR = "profiles/main/workspace/uploads"
HERMES_DATA_ROOT = "/opt/data"
HERMES_DATA_ROOTS = (HERMES_DATA_ROOT, "/workspace")
_HERMES_PROFILE_PREFIX = "profiles/"
HERMES_USER = "hermes"
HERMES_UID = 10000
HERMES_GID = 10000


def _exec_output(result) -> tuple[int, bytes]:
    if isinstance(result, tuple):
        exit_code, output = result
    else:
        exit_code = getattr(result, "exit_code", 0)
        output = getattr(result, "output", b"")
    if isinstance(output, str):
        output = output.encode("utf-8")
    return int(exit_code or 0), output or b""


def _mark_hermes_owned(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = HERMES_UID
    info.gid = HERMES_GID
    info.uname = HERMES_USER
    info.gname = HERMES_USER
    return info


def chown_hermes_path(container, absolute_path: str) -> None:
    quoted = shlex.quote(absolute_path)
    result = container.exec_run(["sh", "-lc", f"chown -R hermes:hermes -- {quoted}"], user="root")
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=output.decode("utf-8", errors="replace") or "Failed to repair Hermes file ownership",
        )


def _normalize_profile_storage_path(path: str, default_agent: str = "main") -> str:
    normalized = posixpath.normpath((path or "").strip().replace("\\", "/").lstrip("/"))
    if normalized in {"", "."}:
        return f"profiles/{default_agent}/workspace"
    if normalized == ".." or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hermes file path cannot escape the workspace",
        )
    if normalized.startswith(_HERMES_PROFILE_PREFIX):
        parts = normalized.split("/")
        if len(parts) >= 3 and parts[0] == "profiles" and parts[1] and parts[2] in {"workspace", "skills", "memories"}:
            return normalized.rstrip("/")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hermes profile path is unavailable",
        )
    if normalized == "workspace":
        return "profiles/main/workspace"
    if normalized.startswith("workspace/"):
        return f"profiles/{default_agent}/workspace/{normalized[len('workspace/'):]}".rstrip("/")
    if normalized.startswith("workspace-"):
        head, _, tail = normalized.partition("/")
        agent = head.removeprefix("workspace-")
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hermes workspace path is unavailable",
            )
        suffix = f"/{tail}" if tail else ""
        return f"profiles/{agent}/workspace{suffix}".rstrip("/")
    return f"profiles/{default_agent}/workspace/{normalized}".rstrip("/")


def normalize_hermes_upload_dir(target_dir: str | None, default: str = DEFAULT_HERMES_UPLOAD_DIR) -> str:
    raw = (target_dir or default).strip().replace("\\", "/")
    raw = raw.removeprefix("~/.comworker/")
    raw = raw.removeprefix("/root/.comworker/")
    raw = raw.removeprefix("root/.comworker/")
    raw = raw.removeprefix("/opt/data/")
    normalized = _normalize_profile_storage_path(raw or default)
    if normalized in {"", "."}:
        normalized = default
    if normalized == ".." or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hermes upload path cannot escape the workspace",
        )
    if "/workspace" not in normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hermes upload path must be under a workspace directory",
        )
    return normalized.rstrip("/")


def normalize_hermes_workspace_path(requested_path: str | None) -> str:
    raw = (requested_path or "").strip().replace("\\", "/")
    raw = raw.removeprefix("~/.comworker/")
    raw = raw.removeprefix("/root/.comworker/")
    raw = raw.removeprefix("root/.comworker/")
    raw = raw.removeprefix("/opt/data/")
    normalized = _normalize_profile_storage_path(raw)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hermes file path is unavailable",
        )
    return normalized


def normalize_hermes_filemanager_path(requested_path: str | None) -> str:
    raw = (requested_path or "").strip().replace("\\", "/")
    raw = raw.removeprefix("~/.comworker/")
    raw = raw.removeprefix("/root/.comworker/")
    raw = raw.removeprefix("root/.comworker/")
    raw = raw.removeprefix("/opt/data/")
    return _normalize_profile_storage_path(raw)


def is_hermes_absolute_request(requested_path: str | None) -> bool:
    """Whether a file-manager path should be treated as an absolute container path.

    Absolute requests (starting with ``/``) browse the whole container filesystem
    rooted at ``/``. Relative requests keep the legacy ``/opt/data`` profile layout.
    """
    return (requested_path or "").strip().startswith("/")


def normalize_hermes_absolute_path(requested_path: str | None) -> str:
    raw = (requested_path or "/").strip().replace("\\", "/")
    if not raw.startswith("/"):
        raw = "/" + raw
    normalized = posixpath.normpath(raw)
    return normalized or "/"


def normalize_hermes_read_path(requested_path: str | None) -> str:
    raw = (requested_path or "").strip().replace("\\", "/")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hermes file path is unavailable",
        )
    raw = raw.removeprefix("~/.comworker/")
    raw = raw.removeprefix("/root/.comworker/")
    raw = raw.removeprefix("root/.comworker/")
    if raw.startswith("/"):
        normalized = posixpath.normpath(raw)
        if normalized == "/workspace" or normalized.startswith("/workspace/"):
            return f"{HERMES_DATA_ROOT}/profiles/main{normalized}"
        if normalized.startswith("/workspace-"):
            return f"{HERMES_DATA_ROOT}/{_normalize_profile_storage_path(normalized)}"
        if (
            normalized == "/tmp"
            or normalized.startswith("/tmp/")
            or normalized == "/scripts"
            or normalized.startswith("/scripts/")
            or normalized == "/opt/data"
            or normalized.startswith("/opt/data/")
            or normalized == "/root/.agent-browser/tmp"
            or normalized.startswith("/root/.agent-browser/tmp/")
            or normalized == "/home"
            or normalized.startswith("/home/")
            or normalized == "/opt/hermes"
            or normalized.startswith("/opt/hermes/")
        ):
            return normalized
        # Any other absolute path is served as-is so the file manager can
        # download files from anywhere in the container filesystem.
        return normalized
    else:
        raw = raw.lstrip("/")
        normalized = posixpath.normpath(raw)
        if normalized in {"", ".", ".."} or normalized.startswith("../"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Hermes file path is unavailable",
            )
        return f"{HERMES_DATA_ROOT}/{normalized}"
    normalized = posixpath.normpath(raw)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hermes file path is unavailable",
        )
    return f"{HERMES_DATA_ROOT}/{normalized}"


def _filemanager_script() -> str:
    return r"""
import json
import mimetypes
import os
import posixpath
import sys
from datetime import datetime, timezone

root = "/opt/data"
storage_path = sys.argv[1]
target = os.path.realpath(os.path.join(root, storage_path))
profiles = os.path.realpath(os.path.join(root, "profiles"))

def fail(code, detail):
    print(json.dumps({"detail": detail}))
    raise SystemExit(code)

parts = target.split(os.sep)
if not (target.startswith(profiles + os.sep) and "workspace" in parts):
    fail(2, "Hermes file path is unavailable")
if not os.path.exists(target):
    fail(4, "Hermes file not found")

def display_path(storage_rel):
    storage_rel = storage_rel.strip("/")
    return storage_rel

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def entry_for(path):
    stat = os.stat(path)
    storage_rel = os.path.relpath(path, root).replace(os.sep, "/")
    is_dir = os.path.isdir(path)
    content_type = None if is_dir else (mimetypes.guess_type(path)[0] or "application/octet-stream")
    return {
        "name": os.path.basename(path),
        "path": display_path(storage_rel),
        "type": "directory" if is_dir else "file",
        "size": None if is_dir else stat.st_size,
        "content_type": content_type,
        "modified": iso(stat.st_mtime),
    }

if os.path.isdir(target):
    items = [entry_for(os.path.join(target, name)) for name in os.listdir(target)]
    items.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    payload = {
        "type": "directory",
        "path": display_path(os.path.relpath(target, root).replace(os.sep, "/")),
        "root": "/opt/data/profiles",
        "items": items,
        "runtime": "hermes",
    }
else:
    stat = os.stat(target)
    content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
    payload = {
        "type": "file",
        "path": display_path(os.path.relpath(target, root).replace(os.sep, "/")),
        "name": os.path.basename(target),
        "size": stat.st_size,
        "content_type": content_type,
        "modified": iso(stat.st_mtime),
        "runtime": "hermes",
    }
    text_exts = {".csv", ".json", ".jsonl", ".log", ".md", ".py", ".sh", ".toml", ".ts", ".txt", ".xml", ".yaml", ".yml"}
    ext = os.path.splitext(target)[1].lower()
    if stat.st_size <= 1024 * 1024 and (content_type.startswith("text/") or content_type == "application/json" or ext in text_exts):
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            payload["content"] = fh.read()

print(json.dumps(payload))
"""


def _filemanager_root_script() -> str:
    return r"""
import json
import mimetypes
import os
import sys
from datetime import datetime, timezone

target = os.path.realpath(sys.argv[1] or "/")

def fail(code, detail):
    print(json.dumps({"detail": detail}))
    raise SystemExit(code)

if not os.path.exists(target):
    fail(4, "Hermes file not found")

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def entry_for(path):
    stat = os.stat(path)
    is_dir = os.path.isdir(path)
    content_type = None if is_dir else (mimetypes.guess_type(path)[0] or "application/octet-stream")
    return {
        "name": os.path.basename(path) or path,
        "path": path,
        "type": "directory" if is_dir else "file",
        "size": None if is_dir else stat.st_size,
        "content_type": content_type,
        "modified": iso(stat.st_mtime),
    }

if os.path.isdir(target):
    items = []
    for name in sorted(os.listdir(target)):
        full = os.path.join(target, name)
        try:
            items.append(entry_for(full))
        except OSError:
            continue
    items.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    payload = {
        "type": "directory",
        "path": target,
        "root": "/",
        "items": items,
        "runtime": "hermes",
    }
else:
    stat = os.stat(target)
    content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
    payload = {
        "type": "file",
        "path": target,
        "name": os.path.basename(target),
        "size": stat.st_size,
        "content_type": content_type,
        "modified": iso(stat.st_mtime),
        "runtime": "hermes",
    }
    text_exts = {".csv", ".json", ".jsonl", ".log", ".md", ".py", ".sh", ".toml", ".ts", ".txt", ".xml", ".yaml", ".yml"}
    ext = os.path.splitext(target)[1].lower()
    if stat.st_size <= 1024 * 1024 and (content_type.startswith("text/") or content_type == "application/json" or ext in text_exts):
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                payload["content"] = fh.read()
        except OSError:
            pass

print(json.dumps(payload))
"""


def browse_hermes_filemanager(container_id_or_name: str | None, requested_path: str | None) -> dict:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc

    if is_hermes_absolute_request(requested_path):
        absolute_path = normalize_hermes_absolute_path(requested_path)
        result = container.exec_run(["python3", "-c", _filemanager_root_script(), absolute_path])
    else:
        storage_path = normalize_hermes_filemanager_path(requested_path)
        result = container.exec_run(["python3", "-c", _filemanager_script(), storage_path])
    exit_code, output = _exec_output(result)
    if exit_code == 4:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hermes file not found")
    if exit_code != 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hermes file path is unavailable")
    try:
        payload = json.loads(output.decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected Hermes filemanager response",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected Hermes filemanager response")
    return payload


def make_hermes_filemanager_directory(container_id_or_name: str | None, requested_path: str | None) -> dict:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )
    if is_hermes_absolute_request(requested_path):
        absolute_path = normalize_hermes_absolute_path(requested_path)
        result_path = absolute_path
    else:
        storage_path = normalize_hermes_filemanager_path(requested_path)
        absolute_path = f"{HERMES_DATA_ROOT}/{storage_path}"
        result_path = storage_path
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    result = container.exec_run(["sh", "-lc", f"mkdir -p -- {shlex.quote(absolute_path)}"], user=HERMES_USER)
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        result = container.exec_run(["sh", "-lc", f"mkdir -p -- {shlex.quote(absolute_path)}"], user="root")
        exit_code, output = _exec_output(result)
        if exit_code != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=output.decode("utf-8", errors="replace") or "Failed to create Hermes directory",
            )
    chown_hermes_path(container, absolute_path)
    return {"ok": True, "path": result_path, "runtime": "hermes"}


def write_hermes_filemanager_file(container_id_or_name: str | None, requested_path: str | None, content: str) -> dict:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )
    storage_path = normalize_hermes_filemanager_path(requested_path)
    if storage_path.endswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hermes file path must include a file name")
    if storage_path.endswith("/workspace") or storage_path == "profiles/main/workspace":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hermes file path must include a file name")
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc

    archive = _build_upload_archive(storage_path, content.encode("utf-8"))
    try:
        _ensure_comworker_compat_links(container)
        ok = container.put_archive(HERMES_DATA_ROOT, archive)
        chown_hermes_path(container, f"{HERMES_DATA_ROOT}/{posixpath.dirname(storage_path)}")
    except DockerAPIError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to write Hermes file") from exc
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to write Hermes file")
    return browse_hermes_filemanager(container_id_or_name, storage_path)


def delete_hermes_filemanager_path(container_id_or_name: str | None, requested_path: str | None) -> dict:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )
    if is_hermes_absolute_request(requested_path):
        absolute_path = normalize_hermes_absolute_path(requested_path)
        if absolute_path == "/":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the filesystem root")
        result_path = absolute_path
    else:
        storage_path = normalize_hermes_filemanager_path(requested_path)
        if storage_path.endswith("/workspace") or storage_path == "profiles/main/workspace":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete Hermes workspace root")
        absolute_path = f"{HERMES_DATA_ROOT}/{storage_path}"
        result_path = storage_path
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    result = container.exec_run(["sh", "-lc", f"rm -rf -- {shlex.quote(absolute_path)}"])
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=output.decode("utf-8", errors="replace") or "Failed to delete Hermes path",
        )
    return {"ok": True, "path": result_path, "runtime": "hermes"}


def _legacy_script_fallback_path(container, requested_path: str) -> str | None:
    if not requested_path.startswith("/scripts/"):
        return None
    rel_path = requested_path.removeprefix("/scripts/").strip("/")
    normalized_rel = posixpath.normpath(rel_path)
    if normalized_rel in {"", ".", ".."} or normalized_rel.startswith("../"):
        return None

    quoted_rel = shlex.quote(normalized_rel)
    script = (
        "rel="
        f"{quoted_rel}; "
        "for root in /opt/data/skills /workspace/skills /opt/data/comworker_data/skills /opt/hermes/deploy_copy/skills; do "
        "[ -d \"$root\" ] || continue; "
        "find \"$root\" -path \"*/scripts/$rel\" -type f -print; "
        "done | head -n 1"
    )
    result = container.exec_run(["sh", "-lc", script])
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        return None
    candidate = output.decode("utf-8", errors="replace").splitlines()[0:1]
    if not candidate:
        return None
    found = candidate[0].strip()
    if not found.startswith(("/opt/data/skills/", "/workspace/skills/", "/opt/data/comworker_data/skills/", "/opt/hermes/deploy_copy/skills/")):
        return None
    return found


def _legacy_profile_fallback_path(archive_path: str) -> str | None:
    prefix = f"{HERMES_DATA_ROOT}/profiles/"
    if not archive_path.startswith(prefix):
        return None
    rel = archive_path[len(prefix):]
    parts = rel.split("/", 3)
    if len(parts) < 3 or parts[1] != "workspace":
        return None
    agent = parts[0]
    tail = parts[2] if len(parts) == 3 else f"{parts[2]}/{parts[3]}"
    if not agent or agent in {".", ".."} or "/" in agent:
        return None
    legacy_base = "workspace" if agent == "main" else f"workspace-{agent}"
    return f"{HERMES_DATA_ROOT}/{legacy_base}/{tail}".rstrip("/")


def _safe_filename(filename: str | None) -> str:
    normalized = (filename or "upload.bin").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return normalized or "upload.bin"


def _build_upload_archive(relative_path: str, contents: bytes) -> bytes:
    tar_buffer = io.BytesIO()
    now = int(time.time())
    upload_dir = posixpath.dirname(relative_path)

    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        home_dir = tarfile.TarInfo(name="home")
        home_dir.type = tarfile.DIRTYPE
        home_dir.mode = 0o755
        home_dir.mtime = now
        tar.addfile(_mark_hermes_owned(home_dir))

        comworker_link = tarfile.TarInfo(name="home/.comworker")
        comworker_link.type = tarfile.SYMTYPE
        comworker_link.linkname = HERMES_DATA_ROOT
        comworker_link.mode = 0o777
        comworker_link.mtime = now
        tar.addfile(_mark_hermes_owned(comworker_link))

        current_dir = ""
        for part in upload_dir.split("/"):
            if not part:
                continue
            current_dir = f"{current_dir}/{part}" if current_dir else part
            directory = tarfile.TarInfo(name=current_dir)
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            directory.mtime = now
            tar.addfile(_mark_hermes_owned(directory))

        upload_file = tarfile.TarInfo(name=relative_path)
        upload_file.size = len(contents)
        upload_file.mode = 0o644
        upload_file.mtime = now
        tar.addfile(_mark_hermes_owned(upload_file), io.BytesIO(contents))

    tar_buffer.seek(0)
    return tar_buffer.read()


def _build_plain_archive(relative_path: str, contents: bytes) -> bytes:
    """Build a tar containing only the uploaded file, extracted relative to ``/``.

    Unlike ``_build_upload_archive`` this does not add parent directory members,
    so extracting at the filesystem root never rewrites ownership of existing
    system directories. The target directory must already exist.
    """
    tar_buffer = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        upload_file = tarfile.TarInfo(name=relative_path)
        upload_file.size = len(contents)
        upload_file.mode = 0o644
        upload_file.mtime = now
        tar.addfile(_mark_hermes_owned(upload_file), io.BytesIO(contents))
    tar_buffer.seek(0)
    return tar_buffer.read()


def _ensure_comworker_compat_links(container) -> None:
    result = container.exec_run(
        [
            "sh",
            "-lc",
            "mkdir -p /opt/data/home /root "
            "&& ln -sfn /opt/data /opt/data/home/.comworker "
            "&& ln -sfn /opt/data /root/.comworker",
        ]
    )
    exit_code = getattr(result, "exit_code", result[0] if isinstance(result, tuple) else 0)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to prepare Hermes ComWorker workspace compatibility links",
        )


async def write_upload_to_hermes_container(
    container_id_or_name: str | None,
    file: UploadFile,
    target_dir: str | None,
) -> dict:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )

    contents = await file.read()
    original_name = _safe_filename(file.filename)
    stored_name = f"{int(time.time() * 1000)}-{original_name}"

    if is_hermes_absolute_request(target_dir):
        absolute_dir = normalize_hermes_absolute_path(target_dir)
        absolute_path = posixpath.join(absolute_dir, stored_name)
        archive = _build_plain_archive(absolute_path.lstrip("/"), contents)
        try:
            container = get_docker_container(container_id_or_name)
            ok = container.put_archive("/", archive)
        except DockerNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hermes runtime container is unavailable",
            ) from exc
        except DockerAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to write upload into Hermes container",
            ) from exc
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to write upload into Hermes container",
            )
        return {
            "path": absolute_path,
            "name": stored_name,
            "original_name": original_name,
            "size": len(contents),
            "content_type": file.content_type or "application/octet-stream",
        }

    upload_dir = normalize_hermes_upload_dir(target_dir)
    relative_path = f"{upload_dir}/{stored_name}"
    archive = _build_upload_archive(relative_path, contents)

    try:
        container = get_docker_container(container_id_or_name)
        _ensure_comworker_compat_links(container)
        ok = container.put_archive(HERMES_DATA_ROOT, archive)
        chown_hermes_path(container, f"{HERMES_DATA_ROOT}/{upload_dir}")
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc
    except DockerAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write upload into Hermes workspace",
        ) from exc

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write upload into Hermes workspace",
        )

    return {
        "path": relative_path,
        "name": stored_name,
        "original_name": original_name,
        "size": len(contents),
        "content_type": file.content_type or "application/octet-stream",
    }


def read_file_from_hermes_container(
    container_id_or_name: str | None,
    requested_path: str | None,
) -> tuple[bytes, str]:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )

    archive_path = normalize_hermes_read_path(requested_path)
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc

    media_path = archive_path
    try:
        stream, _stat = container.get_archive(archive_path)
    except DockerNotFound as exc:
        fallback_path = _legacy_script_fallback_path(container, archive_path) or _legacy_profile_fallback_path(archive_path)
        if not fallback_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Hermes file not found",
            ) from exc
        media_path = fallback_path
        try:
            stream, _stat = container.get_archive(fallback_path)
        except DockerNotFound as fallback_exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Hermes file not found",
            ) from fallback_exc
        except DockerAPIError as fallback_exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to read Hermes file",
            ) from fallback_exc
    except DockerAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read Hermes file",
        ) from exc

    archive = b"".join(stream)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        members = tar.getmembers()
        files = [m for m in members if m.isfile()]
        # A directory download always carries a directory member in the tar;
        # only a genuine single-file download (no directory member) should be
        # returned raw, otherwise a single-file directory must still be zipped.
        is_directory = any(m.isdir() for m in members)

        if not is_directory and len(files) == 1:
            extracted = tar.extractfile(files[0])
            if extracted is not None:
                media_type = mimetypes.guess_type(media_path)[0] or "application/octet-stream"
                return extracted.read(), media_type

        if files:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for member in files:
                    f = tar.extractfile(member)
                    if f is None:
                        continue
                    zf.writestr(member.name, f.read())
            zip_buffer.seek(0)
            return zip_buffer.read(), "application/zip"

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Hermes file not found",
    )


def read_data_file_from_hermes_container(
    container_id_or_name: str | None,
    relative_path: str,
) -> bytes:
    if not container_id_or_name:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        )

    normalized = posixpath.normpath((relative_path or "").strip().replace("\\", "/").lstrip("/"))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hermes data file is unavailable",
        )

    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hermes runtime container is unavailable",
        ) from exc

    stream = None
    last_not_found: DockerNotFound | None = None
    for root in HERMES_DATA_ROOTS:
        try:
            stream, _stat = container.get_archive(f"{root}/{normalized}")
            break
        except DockerNotFound as exc:
            last_not_found = exc
            continue
        except DockerAPIError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to read Hermes data file",
            ) from exc

    if stream is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hermes data file not found",
        ) from last_not_found

    archive = b"".join(stream)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            return extracted.read()

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Hermes data file not found",
    )
