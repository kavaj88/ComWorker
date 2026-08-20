"""Platform-level skill storage (file-based, no DB migration needed).

Skills are stored as directories under PLATFORM_SKILLS_DIR.
A JSON manifest at PLATFORM_SKILLS_DIR/.manifest.json tracks metadata
(name, description, enabled, created_at).

When a new hermes container is created, enabled platform skills are
copied into the container's global scope.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status

PLATFORM_SKILLS_DIR = Path(os.getenv("PLATFORM_SKILLS_DIR", "/app/platform_skills"))
MANIFEST_PATH = PLATFORM_SKILLS_DIR / ".manifest.json"


def _ensure_dir():
    PLATFORM_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _load_manifest() -> dict[str, dict]:
    _ensure_dir()
    if not MANIFEST_PATH.exists():
        return {}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(manifest: dict[str, dict]):
    _ensure_dir()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def list_platform_skills() -> list[dict]:
    manifest = _load_manifest()
    result = []
    for name, meta in manifest.items():
        skill_dir = PLATFORM_SKILLS_DIR / name
        has_skill_md = (skill_dir / "SKILL.md").exists()
        result.append(
            {
                "name": name,
                "title": meta.get("title", name),
                "description": meta.get("description", ""),
                "enabled": meta.get("enabled", True),
                "created_at": meta.get("created_at", ""),
                "installed": has_skill_md,
            }
        )
    return result


def get_platform_skill(name: str) -> dict | None:
    manifest = _load_manifest()
    meta = manifest.get(name)
    if not meta:
        return None
    skill_dir = PLATFORM_SKILLS_DIR / name
    files = []
    if skill_dir.exists():
        for f in skill_dir.rglob("*"):
            if f.is_file():
                files.append(str(f.relative_to(skill_dir)))
    return {
        "name": name,
        "description": meta.get("description", ""),
        "enabled": meta.get("enabled", True),
        "created_at": meta.get("created_at", ""),
        "files": files,
    }


def _safe_name(name: str) -> str:
    """Sanitize skill name for filesystem use."""
    safe = "".join(c for c in name if c.isalnum() or c in "_-.")
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid skill name")
    return safe


def _extract_skill_description(skill_dir: Path) -> str:
    """Read description from SKILL.md frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""
    try:
        content = skill_md.read_text(encoding="utf-8")
        # Simple frontmatter extraction
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                fm = content[3:end].strip()
                for line in fm.split("\n"):
                    if line.startswith("description:"):
                        return line.split(":", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def install_platform_skill(name: str, description: str = "", enabled: bool = True):
    manifest = _load_manifest()
    now = datetime.utcnow().isoformat()
    manifest[name] = {
        "description": description,
        "enabled": enabled,
        "created_at": manifest.get(name, {}).get("created_at", now),
    }
    _save_manifest(manifest)


def delete_platform_skill(name: str):
    manifest = _load_manifest()
    if name not in manifest:
        raise HTTPException(status_code=404, detail="Skill not found")
    del manifest[name]
    _save_manifest(manifest)
    skill_dir = PLATFORM_SKILLS_DIR / name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)


def set_platform_skill_enabled(name: str, enabled: bool):
    manifest = _load_manifest()
    if name not in manifest:
        raise HTTPException(status_code=404, detail="Skill not found")
    manifest[name]["enabled"] = enabled
    _save_manifest(manifest)


def set_platform_skill_meta(
    name: str,
    *,
    title: str | None = None,
    description: str | None = None,
    category: str | None = None,
) -> dict:
    """Update editable metadata of a platform skill (title/description/category)."""
    manifest = _load_manifest()
    if name not in manifest:
        raise HTTPException(status_code=404, detail="Skill not found")
    meta = manifest[name]
    if title is not None:
        meta["title"] = title
    if description is not None:
        meta["description"] = description
    if category is not None:
        meta["category"] = category
    _save_manifest(manifest)
    return {"ok": True, "name": name, **meta}


def upload_platform_skill_zip(file: UploadFile) -> dict:
    _ensure_dir()
    contents = file.file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Uploaded file must be a zip") from exc

    # Find SKILL.md to determine skill name
    names = [n for n in zf.namelist() if not n.endswith("/")]
    skill_md_names = [n for n in names if n.replace("\\", "/").strip("/").endswith("SKILL.md")]
    if not skill_md_names:
        raise HTTPException(status_code=400, detail="Skill zip must contain SKILL.md")

    # Use the directory containing SKILL.md as the skill name, or filename
    skill_md_path = skill_md_names[0].replace("\\", "/").strip("/")
    parts = skill_md_path.split("/")
    if len(parts) > 1:
        name = _safe_name(parts[0])
    else:
        name = _safe_name(Path(file.filename or "uploaded").stem)

    skill_dir = PLATFORM_SKILLS_DIR / name
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Extract files
    prefix = parts[0] if len(parts) > 1 else ""
    for member in zf.namelist():
        # Skip directory entries (zip stores them as names ending with "/")
        if member.endswith("/"):
            continue
        member_path = member.replace("\\", "/").strip("/")
        if prefix and not member_path.startswith(prefix):
            continue
        target = member_path[len(prefix):].lstrip("/") if prefix else member_path
        if not target:
            continue
        target_path = skill_dir / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target_path, "wb") as dst:
            dst.write(src.read())

    description = _extract_skill_description(skill_dir)
    install_platform_skill(name, description=description, enabled=True)
    return {"name": name, "description": description, "enabled": True}


def copy_enabled_skills_to_container(container_id: str):
    """Copy enabled platform skills into a hermes container's global scope.

    Called by container manager when creating a new container.
    """
    import docker

    manifest = _load_manifest()
    enabled_names = [n for n, m in manifest.items() if m.get("enabled", True)]
    if not enabled_names:
        return

    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
    except docker.errors.NotFound:
        return

    for name in enabled_names:
        skill_dir = PLATFORM_SKILLS_DIR / name
        if not skill_dir.exists():
            continue
        # Copy to container's global skills path
        target = "/opt/data/profiles/skills/" + name
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for f in skill_dir.rglob("*"):
                if f.is_file():
                    arcname = str(f.relative_to(skill_dir))
                    tar.add(f, arcname=arcname)
        tar_buffer.seek(0)
        container.put_archive(target, tar_buffer.read())
