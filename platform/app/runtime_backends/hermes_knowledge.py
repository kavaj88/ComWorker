from __future__ import annotations

import json
import posixpath
import re
import shlex
import textwrap

from docker.errors import NotFound as DockerNotFound
from fastapi import HTTPException, status

from app.container.manager import get_docker_container
from app.runtime_backends.hermes_files import HERMES_DATA_ROOT, _exec_output, chown_hermes_path, write_hermes_filemanager_file


def _safe_agent_id(agent_id: str | None) -> str:
    normalized = (agent_id or "main").strip().replace("\\", "/").strip("/")
    if not normalized or normalized in {".", ".."} or "/" in normalized or normalized.startswith("../"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid agent id")
    return normalized


def knowledge_root_for_agent(agent_id: str | None) -> str:
    return f"profiles/{_safe_agent_id(agent_id)}/workspace/knowledge"


def _safe_knowledge_page_path(page_path: str) -> str:
    normalized = posixpath.normpath((page_path or "").strip().replace("\\", "/").lstrip("/"))
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or not normalized.lower().endswith(".md")
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Knowledge page path is unavailable")
    return normalized


def ensure_knowledge_root(container, agent_id: str | None) -> str:
    root = f"{HERMES_DATA_ROOT}/{knowledge_root_for_agent(agent_id)}"
    result = container.exec_run(["sh", "-lc", f"mkdir -p -- {shlex.quote(root)}"], user="root")
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=output.decode("utf-8", errors="replace") or "Failed to prepare knowledge root",
        )
    chown_hermes_path(container, root)
    return root


def _knowledge_script() -> str:
    return r"""
import json
import os
import re
import sys
from datetime import datetime, timezone

root = "/opt/data"
agent_id = sys.argv[1]
action = sys.argv[2]
query = sys.argv[3] if len(sys.argv) > 3 else ""
requested_path = sys.argv[4] if len(sys.argv) > 4 else ""

base = os.path.realpath(os.path.join(root, "profiles", agent_id, "workspace", "knowledge"))
profiles = os.path.realpath(os.path.join(root, "profiles"))

def fail(code, detail):
    print(json.dumps({"detail": detail}, ensure_ascii=False))
    raise SystemExit(code)

if not base.startswith(profiles + os.sep):
    fail(2, "Knowledge path is unavailable")

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def rel_for(path):
    return os.path.relpath(path, base).replace(os.sep, "/")

def clean_wikilink_target(value):
    return (value.split("|", 1)[0].split("#", 1)[0]).strip()

def extract_wikilinks(content):
    links = []
    seen = set()
    for match in re.finditer(r"\[\[([^\]]+)\]\]", content):
        target = clean_wikilink_target(match.group(1) or "")
        key = target.lower()
        if target and key not in seen:
            seen.add(key)
            links.append(target)
    return links

def parse_scalar(value):
    value = value.strip().strip("\"'")
    return value or None

def parse_tags(value):
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [part.strip().strip("\"'") for part in value.split(",") if part.strip().strip("\"'")]

def parse_frontmatter(raw):
    if not raw.startswith("---"):
        return {}, raw
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$", raw)
    if not match:
        return {}, raw
    data = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            data[key] = value.strip()
    return data, match.group(2) or ""

def title_from_content(name, content):
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or name.rsplit(".", 1)[0]
    return name.rsplit(".", 1)[0]

def page_meta(path, raw, stat):
    data, content = parse_frontmatter(raw)
    name = os.path.basename(path)
    title = parse_scalar(data.get("title", "")) or title_from_content(name, content)
    modified = iso(stat.st_mtime)
    return {
        "path": path,
        "name": name,
        "title": title,
        "type": parse_scalar(data.get("type", "")),
        "domain": parse_scalar(data.get("domain", "")),
        "status": parse_scalar(data.get("status", "")),
        "tags": parse_tags(data.get("tags", "")),
        "summary": parse_scalar(data.get("summary", "")),
        "created": parse_scalar(data.get("created", "")),
        "updated": parse_scalar(data.get("updated", "")) or modified,
        "size": stat.st_size,
        "modified": modified,
        "wikilinks": extract_wikilinks(content),
    }, content

def attachment_meta(path, stat):
    return {
        "path": path,
        "name": os.path.basename(path),
        "size": stat.st_size,
        "modified": iso(stat.st_mtime),
    }

def directory_meta(path, stat):
    return {
        "path": path,
        "name": os.path.basename(path),
        "modified": iso(stat.st_mtime),
    }

def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return None

def pages():
    results = []
    if not os.path.isdir(base):
        return results
    for current, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for filename in files:
            if not filename.lower().endswith(".md"):
                continue
            full = os.path.realpath(os.path.join(current, filename))
            if not full.startswith(base + os.sep):
                continue
            try:
                stat = os.stat(full)
                raw = read_text(full)
                if raw is None:
                    continue
                meta, content = page_meta(rel_for(full), raw, stat)
            except OSError:
                continue
            results.append({"meta": meta, "content": content, "raw": raw})
    results.sort(key=lambda item: (item["meta"].get("updated") or item["meta"].get("modified") or "", item["meta"]["path"]), reverse=True)
    return results

def normalized_token(value):
    value = value.strip().replace("\\", "/")
    if value.lower().endswith(".md"):
        value = value[:-3]
    return value.lower()

def resolver(items):
    mapping = {}
    for item in items:
        meta = item["meta"]
        path = meta["path"]
        name = meta["name"]
        title = meta["title"]
        for value in [path, name, title, name.rsplit(".", 1)[0], path.rsplit(".", 1)[0]]:
            mapping[normalized_token(value)] = path
    return lambda value: mapping.get(normalized_token(clean_wikilink_target(value)))

def safe_page_path(value):
    value = value.replace("\\", "/").strip().lstrip("/")
    normalized = os.path.normpath(value).replace("\\", "/")
    if not normalized or normalized in {".", ".."} or normalized.startswith("../") or not normalized.lower().endswith(".md"):
        fail(2, "Knowledge page path is unavailable")
    full = os.path.realpath(os.path.join(base, normalized))
    if not full.startswith(base + os.sep):
        fail(2, "Knowledge page path is unavailable")
    return full, normalized

items = pages()

if action == "list":
    attachments = []
    directories = []
    if os.path.isdir(base):
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
            for dirname in dirs:
                full = os.path.realpath(os.path.join(current, dirname))
                if not full.startswith(base + os.sep):
                    continue
                try:
                    directories.append(directory_meta(rel_for(full), os.stat(full)))
                except OSError:
                    continue
            for filename in files:
                if filename.lower().endswith(".md"):
                    continue
                full = os.path.realpath(os.path.join(current, filename))
                if not full.startswith(base + os.sep):
                    continue
                try:
                    attachments.append(attachment_meta(rel_for(full), os.stat(full)))
                except OSError:
                    continue
    directories.sort(key=lambda item: item["path"])
    attachments.sort(key=lambda item: (item.get("modified") or "", item["path"]), reverse=True)
    print(json.dumps({
        "agentId": agent_id,
        "knowledgeRoot": f"profiles/{agent_id}/workspace/knowledge",
        "exists": os.path.isdir(base),
        "pages": [item["meta"] for item in items],
        "directories": directories,
        "attachments": attachments,
    }, ensure_ascii=False))
elif action == "read":
    full, rel = safe_page_path(requested_path)
    raw = read_text(full)
    if raw is None:
        fail(4, "Knowledge page no longer exists")
    try:
        stat = os.stat(full)
    except OSError:
        fail(4, "Knowledge page no longer exists")
    meta, content = page_meta(rel, raw, stat)
    resolve = resolver(items)
    backlinks = []
    for item in items:
        if item["meta"]["path"] == rel:
            continue
        if any(resolve(link) == rel for link in item["meta"].get("wikilinks", [])):
            backlinks.append(item["meta"]["path"])
    print(json.dumps({"page": meta, "content": content, "backlinks": backlinks}, ensure_ascii=False))
elif action == "search":
    needle = query.strip().lower()
    results = []
    if needle:
        for item in items:
            for index, line in enumerate(item["raw"].splitlines(), start=1):
                haystack = line.lower()
                if needle not in haystack:
                    continue
                results.append({
                    "path": item["meta"]["path"],
                    "title": item["meta"]["title"],
                    "line": index,
                    "text": line,
                })
                if len(results) >= 200:
                    break
            if len(results) >= 200:
                break
    print(json.dumps({"results": results}, ensure_ascii=False))
elif action == "graph":
    resolve = resolver(items)
    edges = []
    seen = set()
    for item in items:
        source = item["meta"]["path"]
        for link in item["meta"].get("wikilinks", []):
            target = resolve(link)
            if not target:
                continue
            key = f"{source}=>{target}"
            if key in seen:
                continue
            seen.add(key)
            edges.append({"source": source, "target": target})
    print(json.dumps({
        "nodes": [{"id": item["meta"]["path"], "title": item["meta"]["title"], "type": item["meta"].get("type"), "tags": item["meta"].get("tags", [])} for item in items],
        "edges": edges,
    }, ensure_ascii=False))
else:
    fail(2, "Unsupported knowledge action")
"""


def _run_knowledge_action(
    container_id_or_name: str | None,
    agent_id: str | None,
    action: str,
    query: str = "",
    page_path: str = "",
) -> dict:
    if not container_id_or_name:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hermes runtime container is unavailable")
    safe_agent = _safe_agent_id(agent_id)
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hermes runtime container is unavailable") from exc

    ensure_knowledge_root(container, safe_agent)
    result = container.exec_run(["python3", "-c", _knowledge_script(), safe_agent, action, query or "", page_path or ""])
    exit_code, output = _exec_output(result)
    if exit_code != 0:
        detail = output.decode("utf-8", errors="replace") or "Knowledge request failed"
        try:
            payload = json.loads(detail)
            detail = str(payload.get("detail") or detail)
        except ValueError:
            pass
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    try:
        payload = json.loads(output.decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected knowledge response") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unexpected knowledge response")
    return payload


def list_knowledge_pages(container_id_or_name: str | None, agent_id: str | None) -> dict:
    return _run_knowledge_action(container_id_or_name, agent_id, "list")


def read_knowledge_page(container_id_or_name: str | None, agent_id: str | None, page_path: str) -> dict:
    return _run_knowledge_action(container_id_or_name, agent_id, "read", page_path=page_path)


def search_knowledge_pages(container_id_or_name: str | None, agent_id: str | None, query: str) -> dict:
    return _run_knowledge_action(container_id_or_name, agent_id, "search", query=query)


def knowledge_graph(container_id_or_name: str | None, agent_id: str | None) -> dict:
    return _run_knowledge_action(container_id_or_name, agent_id, "graph")


def write_knowledge_page(container_id_or_name: str | None, agent_id: str | None, page_path: str, content: str) -> dict:
    if not container_id_or_name:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hermes runtime container is unavailable")
    safe_agent = _safe_agent_id(agent_id)
    safe_path = _safe_knowledge_page_path(page_path)
    try:
        container = get_docker_container(container_id_or_name)
    except DockerNotFound as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hermes runtime container is unavailable") from exc

    ensure_knowledge_root(container, safe_agent)
    storage_path = f"{knowledge_root_for_agent(safe_agent)}/{safe_path}"
    write_hermes_filemanager_file(container_id_or_name, storage_path, content)
    return read_knowledge_page(container_id_or_name, safe_agent, safe_path)


def build_knowledge_context(
    container_id_or_name: str | None,
    agent_id: str | None,
    query: str,
    *,
    max_results: int = 5,
    max_chars: int = 3500,
) -> str:
    if not query.strip():
        return ""
    results: list[object] = []
    seen_keys: set[tuple[str, object, str]] = set()
    for candidate in _knowledge_query_candidates(query):
        try:
            payload = search_knowledge_pages(container_id_or_name, agent_id, candidate)
        except HTTPException:
            continue
        for item in payload.get("results") if isinstance(payload.get("results"), list) else []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("path") or ""), item.get("line"), str(item.get("text") or ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(item)
            if len(results) >= max_results * 2:
                break
        if len(results) >= max_results:
            break
    if not isinstance(results, list) or not results:
        return ""

    sections: list[str] = []
    total = 0
    for item in results[:max_results]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        line = item.get("line")
        text = " ".join(str(item.get("text") or "").split())
        if not path or not text:
            continue
        block = f"- {path}:{line}\n  {text}"
        if total + len(block) > max_chars:
            break
        sections.append(block)
        total += len(block)
    if not sections:
        return ""
    return textwrap.dedent(
        """
        Current Agent knowledge base excerpts. Use them when relevant, and cite the source path when answering:
        {items}
        """
    ).strip().format(items="\n".join(sections))


def _knowledge_query_candidates(query: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", query.strip())
    candidates: list[str] = []
    if normalized:
        candidates.append(normalized)

    chunks = re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", normalized)
    for chunk in chunks:
        if chunk not in candidates:
            candidates.append(chunk)
        if re.search(r"[\u4e00-\u9fff]", chunk) and len(chunk) > 4:
            for size in (4, 3, 2):
                for index in range(0, max(0, len(chunk) - size + 1)):
                    piece = chunk[index:index + size]
                    if piece not in candidates:
                        candidates.append(piece)
                    if len(candidates) >= 24:
                        return candidates
    return candidates[:24]
