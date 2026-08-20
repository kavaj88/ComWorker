"""Recommended skills marketplace — clones a Gitee repo and loads categorized skills."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".comworker" / "marketplaces" / "recommended-cache"
_REPO_DIR_NAME = "skills_marketplace"
_PULL_INTERVAL_SECONDS = 300  # Only pull at most once per 5 minutes
_last_pull_time: float = 0.0
_pull_lock = threading.Lock()

def _git_env() -> dict[str, str]:
    """Build env dict for git commands with SSH support.

    Docker bind-mounts from Windows have 0777 permissions and CRLF line endings,
    both of which cause SSH to reject the private key.  We copy the key to a
    temporary location, fix line endings and permissions, then point
    GIT_SSH_COMMAND at the fixed copy.
    """
    import os
    import stat

    mounted_key = Path("/root/.ssh/id_rsa")
    if not mounted_key.exists():
        return dict(os.environ)

    fixed_dir = Path("/tmp/.ssh_fix")
    fixed_key = fixed_dir / "id_rsa"

    if not fixed_key.exists() or fixed_key.stat().st_mtime < mounted_key.stat().st_mtime:
        fixed_dir.mkdir(parents=True, exist_ok=True)
        data = mounted_key.read_bytes().replace(b"\r\n", b"\n")
        fixed_key.write_bytes(data)
        fixed_key.chmod(stat.S_IRUSR)  # 0400

    return {
        **os.environ,
        "GIT_SSH_COMMAND": f"ssh -F /dev/null -i {fixed_key} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
    }


def _repo_dir() -> Path:
    return _CACHE_DIR / _REPO_DIR_NAME


def _clone_or_pull() -> Path:
    """Clone or update the marketplace repo. Returns the repo directory."""
    global _last_pull_time

    repo_dir = _repo_dir()
    repo_url = settings.skills_marketplace_repo
    branch = settings.skills_marketplace_branch

    if not repo_url:
        raise RuntimeError("skills_marketplace_repo is not configured")

    with _pull_lock:
        now = time.monotonic()
        if repo_dir.exists() and (now - _last_pull_time) < _PULL_INTERVAL_SECONDS:
            return repo_dir

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

        if (repo_dir / ".git").is_dir():
            try:
                subprocess.run(
                    ["git", "pull", "--rebase"],
                    cwd=str(repo_dir),
                    capture_output=True,
                    timeout=30,
                    check=True,
                    env=_git_env(),
                )
                _last_pull_time = time.monotonic()
                return repo_dir
            except Exception:
                logger.warning("git pull failed, re-cloning marketplace repo")
                import shutil
                shutil.rmtree(repo_dir, ignore_errors=True)

        if repo_dir.exists():
            import shutil
            shutil.rmtree(repo_dir, ignore_errors=True)

        subprocess.run(
            ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(repo_dir)],
            capture_output=True,
            timeout=60,
            check=True,
            env=_git_env(),
        )
        _last_pull_time = time.monotonic()
        return repo_dir


def _parse_skill_description(content: str) -> str:
    """Extract description from SKILL.md frontmatter or first non-empty line."""
    lines = content.split("\n")
    in_frontmatter = False
    description = ""

    for line in lines:
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            m = re.match(r"^description:\s*(.+)", line)
            if m:
                description = m.group(1).strip()

    if not description:
        for line in lines:
            stripped = line.strip()
            if stripped and stripped != "---":
                description = stripped
                break

    return description


def resolve_recommended_skill_dir(category: str, skill_name: str) -> Path:
    """Return the local filesystem path for a recommended skill."""
    repo_dir = _clone_or_pull()
    skills_dir = repo_dir / "skills"
    cat_file = skills_dir / "categories.json"

    # Resolve category path from categories.json
    cat_dir_name = category
    if cat_file.exists():
        try:
            cat_data = json.loads(cat_file.read_text(encoding="utf-8"))
            for cat in cat_data.get("categories", []):
                if isinstance(cat, dict) and cat.get("id") == category and cat.get("path"):
                    cat_dir_name = cat["path"]
                    break
        except (json.JSONDecodeError, OSError):
            pass

    skill_dir = skills_dir / cat_dir_name / skill_name
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
        raise FileNotFoundError(f"Skill '{skill_name}' not found in category '{category}'")
    return skill_dir


def load_recommended_skills() -> list[dict[str, Any]]:
    """Clone/pull the marketplace repo and return categorized skills."""
    repo_dir = _clone_or_pull()
    skills_dir = repo_dir / "skills"
    cat_file = skills_dir / "categories.json"

    if not cat_file.exists():
        return []

    try:
        cat_data = json.loads(cat_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    categories_meta = cat_data.get("categories", [])
    if not isinstance(categories_meta, list):
        return []

    results: list[dict[str, Any]] = []

    for cat in categories_meta:
        if not isinstance(cat, dict):
            continue
        cat_id = str(cat.get("id", ""))
        cat_path = str(cat.get("path", "") or cat_id)
        cat_dir = skills_dir / cat_path
        if not cat_dir.is_dir():
            continue

        skills: list[dict[str, str]] = []
        try:
            entries = sorted(cat_dir.iterdir())
        except OSError:
            continue

        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
            except OSError:
                content = ""
            desc = _parse_skill_description(content)
            skills.append({
                "name": entry.name,
                "description": desc,
                "category": cat_id,
            })

        if skills:
            results.append({
                "id": cat_id,
                "name": cat.get("name", cat_id),
                "name_en": cat.get("name_en", ""),
                "icon": cat.get("icon", ""),
                "description": cat.get("description", ""),
                "order": cat.get("order", 0),
                "skills": skills,
            })

    results.sort(key=lambda c: c.get("order", 0))
    return results
