from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from app.auth.dependencies import get_current_user
from app.container.manager import ensure_running
from app.db.engine import async_session, get_db
from app.db.models import User
from app.runtime_backend import RuntimeContext

security = HTTPBearer()
from app.runtime_backends.hermes_agents import (
    create_agent_in_container,
    delete_agent_from_container,
    get_agent_file_from_container,
    list_agent_files_from_container,
)
from app.runtime_backends.hermes_files import (
    browse_hermes_filemanager,
    delete_hermes_filemanager_path,
    is_hermes_absolute_request,
    make_hermes_filemanager_directory,
    normalize_hermes_filemanager_path,
)
from app.runtime_backends.hermes_skills import (
    delete_skill_from_hermes_container,
    download_skill_from_hermes_container,
    list_skill_files_from_hermes_container,
    list_skills_from_hermes_container,
    read_installed_marker,
    read_skill_file_from_hermes_container,
    set_skill_disabled_in_hermes_container,
    skill_scopes,
    upload_skill_zip_to_hermes_container,
    write_installed_marker,
    write_skill_file_to_hermes_container,
)
from app.runtime_backends.hermes_knowledge import (
    knowledge_graph,
    list_knowledge_pages,
    read_knowledge_page,
    search_knowledge_pages,
)
from app.runtime_backends.skills_marketplace import load_recommended_skills, resolve_recommended_skill_dir
from app.runtime_router import get_runtime_backend

router = APIRouter(tags=["runtime-compat"])


class SessionTitleRequest(BaseModel):
    title: str


class SessionContextItem(BaseModel):
    """A context binding selected in the client's context selector.

    type: "skill" | "connector" (experts are bound by switching the active
    agent, so they are not passed as a session context).
    id:   platform identifier (skill name, connector id)
    name: human-readable display name shown in prompts
    """

    type: str
    id: str | None = None
    name: str | None = None


class SendMessageRequest(BaseModel):
    message: str
    model: str | None = None
    title: str | None = None
    context: SessionContextItem | None = None


class SkillSearchRequest(BaseModel):
    query: str = ""
    limit: int = 10


class SharedChatRequest(BaseModel):
    message: str
    session_key: str | None = None


class CreateAgentRequest(BaseModel):
    name: str
    workspace: str | None = None


class SkillToggleRequest(BaseModel):
    enabled: bool


class SkillDisabledRequest(BaseModel):
    scope: str | None = None
    agent_id: str | None = None
    disabled: bool


class WriteSkillFileRequest(BaseModel):
    scope: str | None = None
    agent_id: str | None = None
    path: str
    content: str


@router.get("/api/comworker/agents")
async def list_dedicated_agents(
    user: User = Depends(get_current_user),
):
    from app.runtime_backends.hermes_agents import list_agents_from_container
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    container_agents = list_agents_from_container(container.docker_id)
    backend = get_runtime_backend()
    return await backend.get_agent_info(
        RuntimeContext(user=user, scope="dedicated"),
        container_agents=container_agents,
    )


@router.post("/api/comworker/agents")
async def create_dedicated_agent(
    req: CreateAgentRequest,
    user: User = Depends(get_current_user),
):
    agent_id = req.name.strip()
    if not agent_id or not all(c.isalnum() or c in "_-" for c in agent_id):
        raise HTTPException(status_code=400, detail="Agent ID 只能包含字母、数字、下划线和连字符")
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return create_agent_in_container(container.docker_id, agent_id)


@router.delete("/api/comworker/agents/{agent_id}")
async def delete_dedicated_agent(
    agent_id: str,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return delete_agent_from_container(container.docker_id, agent_id)


@router.get("/api/comworker/agents/{agent_id}/files")
async def list_dedicated_agent_files(
    agent_id: str,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return list_agent_files_from_container(container.docker_id, agent_id)


@router.get("/api/comworker/agents/{agent_id}/files/{name:path}")
async def get_dedicated_agent_file(
    agent_id: str,
    name: str,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return get_agent_file_from_container(container.docker_id, agent_id, name)


@router.get("/api/comworker/skills")
async def list_dedicated_skills(
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.list_skills(RuntimeContext(user=user, scope="dedicated"))


class SkillInstalledRequest(BaseModel):
    name: str


@router.get("/api/comworker/skills/installed")
async def get_installed_skills(user: User = Depends(get_current_user)):
    """List the skill names the user has installed, split by source.

    Returns {"user": [...], "managed": [...]}. "user" = installed by the end
    user (uninstallable); "managed" = pushed by an admin (not uninstallable).
    """
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    marker = read_installed_marker(container.docker_id)
    return {
        "user": sorted(marker["user"]),
        "managed": sorted(marker["managed"]),
    }


@router.post("/api/comworker/skills/installed")
async def add_installed_skill(req: SkillInstalledRequest, user: User = Depends(get_current_user)):
    """Mark a skill as installed by the end user (organizational flag)."""
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    marker = read_installed_marker(container.docker_id)
    marker["user"].add(req.name)
    # Installing it yourself promotes it out of the "managed" set.
    marker["managed"].discard(req.name)
    return write_installed_marker(container.docker_id, marker["user"], marker["managed"])


@router.delete("/api/comworker/skills/installed/{name:path}")
async def remove_installed_skill(name: str, user: User = Depends(get_current_user)):
    """Unmark a skill as installed by the end user.

    Only "user" installs can be removed here; "managed" skills pushed by an
    admin are not uninstallable by the end user.

    IMPORTANT: this must be registered before the generic
    `DELETE /api/comworker/skills/{name:path}` route below, otherwise that
    catch-all route would match `/api/comworker/skills/installed/<name>` and
    physically delete the skill instead of just clearing the marker.
    """
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    marker = read_installed_marker(container.docker_id)
    marker["user"].discard(name)
    return write_installed_marker(container.docker_id, marker["user"], marker["managed"])


@router.delete("/api/comworker/skills/{name:path}")
async def delete_dedicated_skill(
    name: str,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    # System-managed skills (pushed by an admin) cannot be physically deleted
    # by the end user — they are "uninstallable" only from the admin side.
    marker = read_installed_marker(container.docker_id)
    if name in marker["managed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="系统安装的技能不可删除，请联系管理员卸载。",
        )
    return delete_skill_from_hermes_container(container.docker_id, name)


@router.get("/api/comworker/skills/{name:path}/download")
async def download_dedicated_skill(
    name: str,
    user: User = Depends(get_current_user),
):
    from fastapi.responses import Response

    async with async_session() as db:
        container = await ensure_running(db, user.id)
    zip_data = download_skill_from_hermes_container(container.docker_id, name)
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name.rsplit("/", 1)[-1]}.zip"'},
    )


@router.post("/api/comworker/skills/upload")
async def upload_dedicated_skill_zip(
    file: UploadFile = File(...),
    scope: str | None = Form(default=None),
    agentId: str | None = Form(default=None, alias="agentId"),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return await upload_skill_zip_to_hermes_container(
        container.docker_id, file, scope=scope, agent_id=agentId
    )


@router.get("/api/comworker/skills/scopes")
async def list_skill_scopes(
    user: User = Depends(get_current_user),
):
    from app.runtime_backends.hermes_agents import list_agents_from_container
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    agents = list_agents_from_container(container.docker_id)
    agent_ids = [a["id"] for a in agents if isinstance(a, dict) and a.get("id")]
    return skill_scopes(agent_ids)


@router.put("/api/comworker/skills/{name:path}/toggle")
async def toggle_dedicated_skill(
    name: str,
    body: SkillToggleRequest,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return set_skill_disabled_in_hermes_container(
        container.docker_id, name, disabled=not body.enabled
    )


@router.put("/api/comworker/skills/{name:path}/disabled")
async def set_dedicated_skill_disabled(
    name: str,
    body: SkillDisabledRequest,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return set_skill_disabled_in_hermes_container(
        container.docker_id, name, disabled=body.disabled, scope=body.scope, agent_id=body.agent_id
    )




@router.get("/api/comworker/skills/{name:path}/files")
async def list_dedicated_skill_files(
    name: str,
    scope: str | None = Query(default=None),
    agent_id: str | None = Query(default=None, alias="agentId"),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return list_skill_files_from_hermes_container(container.docker_id, name, scope=scope, agent_id=agent_id)


@router.get("/api/comworker/skills/{name:path}/files/content")
async def read_dedicated_skill_file(
    name: str,
    path: str = Query(...),
    scope: str | None = Query(default=None),
    agent_id: str | None = Query(default=None, alias="agentId"),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return read_skill_file_from_hermes_container(container.docker_id, name, path, scope=scope, agent_id=agent_id)


@router.put("/api/comworker/skills/{name:path}/files/content")
async def write_dedicated_skill_file(
    name: str,
    req: WriteSkillFileRequest,
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return write_skill_file_to_hermes_container(container.docker_id, name, req.path, req.content, scope=req.scope, agent_id=req.agent_id)


@router.post("/api/comworker/marketplaces/skills/search")
async def search_dedicated_skills(
    req: SkillSearchRequest,
    user: User = Depends(get_current_user),
):
    query = req.query.strip().lower()
    limit = max(1, min(req.limit or 10, 50))
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    skills = list_skills_from_hermes_container(container.docker_id)

    results = []
    for skill in skills:
        path = str(skill.get("path") or skill.get("name") or "")
        haystack = " ".join(
            str(skill.get(key) or "") for key in ("name", "description", "source", "path")
        ).lower()
        if query and query not in haystack:
            continue
        results.append(
            {
                "slug": str(skill.get("name") or path),
                "url": f"local://{path}",
                "installs": "installed",
                "description": str(skill.get("description") or ""),
                "source": str(skill.get("source") or "hermes"),
                "path": path,
            }
        )
        if len(results) >= limit:
            break
    return {"results": results, "runtime": "hermes"}


@router.get("/api/comworker/marketplaces/recommended")
async def list_recommended_skills(
    user: User = Depends(get_current_user),
):
    _ = user
    try:
        categories = load_recommended_skills()
    except Exception as exc:
        return {"categories": [], "error": str(exc)}
    return {"categories": categories}


class RecommendedSkillInstallRequest(BaseModel):
    category: str
    skillName: str


@router.post("/api/comworker/marketplaces/recommended/install")
async def install_recommended_skill(
    req: RecommendedSkillInstallRequest,
    user: User = Depends(get_current_user),
):
    from app.container.manager import get_docker_container
    from app.runtime_backends.hermes_skills import HERMES_SKILLS_ROOT, _build_skills_archive

    try:
        skill_dir = resolve_recommended_skill_dir(req.category, req.skillName)
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}

    # Read all files from the skill directory
    files: dict[str, bytes] = {}
    for file_path in skill_dir.rglob("*"):
        if file_path.is_file():
            rel = str(file_path.relative_to(skill_dir)).replace("\\", "/")
            files[rel] = file_path.read_bytes()

    # Build tar archive and put into container
    archive = _build_skills_archive({req.skillName: files})
    async with async_session() as db:
        container_info = await ensure_running(db, user.id)
    container = get_docker_container(container_info.docker_id)
    container.exec_run(["mkdir", "-p", HERMES_SKILLS_ROOT])
    container.put_archive(HERMES_SKILLS_ROOT, archive)

    return {"ok": True, "name": req.skillName}


@router.post("/api/comworker/runtime/prewarm")
async def prewarm_dedicated_runtime(
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.prewarm(RuntimeContext(user=user, scope="dedicated"))


@router.get("/api/comworker/sessions")
async def list_dedicated_sessions(
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.list_sessions(RuntimeContext(user=user, scope="dedicated"))


@router.get("/api/comworker/sessions/{session_key:path}")
async def get_dedicated_session(
    session_key: str,
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.get_session(RuntimeContext(user=user, scope="dedicated"), session_key)


@router.post("/api/comworker/sessions/{session_key:path}/messages")
async def send_dedicated_message(
    session_key: str,
    req: SendMessageRequest,
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.send_message(
        RuntimeContext(user=user, scope="dedicated"),
        session_key,
        req.message,
        model=req.model,
        title=req.title,
        context=req.context,
    )


async def get_current_user_detached(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Validate the JWT and return the user without holding a DB session open.

    Used by long-polling endpoints (e.g. /runs/{id}/wait) that must not occupy a
    DB connection for the full request duration — doing so exhausts the pool.
    """
    from app.auth.service import decode_token, get_user_by_id
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    async with async_session() as session:
        user = await get_user_by_id(session, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    # Detach from the session before returning so callers can use it without
    # the session staying open.
    from sqlalchemy import inspect as sa_inspect
    if sa_inspect(user).detached is False:
        session.expunge(user)
    return user


@router.get("/api/comworker/runs/{run_id}/wait")
async def wait_dedicated_run(
    run_id: str,
    timeout_ms: Annotated[int, Query(alias="timeoutMs")] = 25000,
    user: User = Depends(get_current_user_detached),
):
    # Long-polling endpoint: holds the HTTP connection for up to timeoutMs.
    # Must NOT hold a DB session during the wait, or concurrent requests
    # exhaust the connection pool. User validation already done above with
    # a session that has been released.
    backend = get_runtime_backend()
    return await backend.wait_run(RuntimeContext(user=user, scope="dedicated"), run_id, timeout_ms)


@router.get("/api/comworker/runs/{run_id}/events")
async def dedicated_run_events_stream(
    run_id: str,
    request: Request,
    token: str = "",
):
    user = User(id="", username="", email="", password_hash="", runtime_mode="dedicated")
    backend = get_runtime_backend()
    return await backend.stream_run_events(RuntimeContext(user=user, scope="dedicated"), request, token, run_id)


@router.put("/api/comworker/sessions/{session_key:path}/title")
async def rename_dedicated_session(
    session_key: str,
    req: SessionTitleRequest,
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.rename_session(RuntimeContext(user=user, scope="dedicated"), session_key, req.title)


@router.delete("/api/comworker/sessions/{session_key:path}")
async def delete_dedicated_session(
    session_key: str,
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.delete_session(RuntimeContext(user=user, scope="dedicated"), session_key)


class AbortRunRequest(BaseModel):
    sessionKey: str = ""


@router.post("/api/comworker/runs/{run_id}/abort")
async def abort_dedicated_run(
    run_id: str,
    req: AbortRunRequest | None = None,
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    session_key = req.sessionKey if req else ""
    return await backend.abort_run(RuntimeContext(user=user, scope="dedicated"), run_id, session_key)


@router.post("/api/comworker/sessions/{session_key:path}/abort-active")
async def abort_dedicated_active_session(
    session_key: str,
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.abort_active_session(RuntimeContext(user=user, scope="dedicated"), session_key)


@router.get("/api/comworker/commands")
async def list_dedicated_commands(
    agentId: str = "",
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    return await backend.list_commands(RuntimeContext(user=user, scope="dedicated"), agentId)


@router.post("/api/comworker/filemanager/upload")
@router.post("/api/comworker/files/upload")
async def upload_dedicated_file(
    file: UploadFile = File(...),
    path: str | None = Form(None),
    upload_dir: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    backend = get_runtime_backend()
    raw_target = path or upload_dir
    target_dir = raw_target if is_hermes_absolute_request(raw_target) else normalize_hermes_filemanager_path(raw_target)
    return await backend.upload_file(
        RuntimeContext(user=user, scope="dedicated"),
        file,
        target_dir=target_dir,
    )


@router.get("/api/comworker/filemanager/browse")
async def browse_dedicated_files(
    path: str = "",
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return browse_hermes_filemanager(container.docker_id, path)


@router.post("/api/comworker/filemanager/mkdir")
async def mkdir_dedicated_file(
    path: str = Query(...),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return make_hermes_filemanager_directory(container.docker_id, path)


@router.delete("/api/comworker/filemanager/delete")
async def delete_dedicated_file(
    path: str = Query(...),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return delete_hermes_filemanager_path(container.docker_id, path)


@router.get("/api/comworker/knowledge/list")
async def list_knowledge(
    agent_id: str = Query(default="main", alias="agentId"),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return list_knowledge_pages(container.docker_id, agent_id)


@router.get("/api/comworker/knowledge/read")
async def read_knowledge(
    agent_id: str = Query(default="main", alias="agentId"),
    path: str = Query(...),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return read_knowledge_page(container.docker_id, agent_id, path)


@router.get("/api/comworker/knowledge/search")
async def search_knowledge(
    agent_id: str = Query(default="main", alias="agentId"),
    q: str = Query(...),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return search_knowledge_pages(container.docker_id, agent_id, q)


@router.get("/api/comworker/knowledge/graph")
async def get_knowledge_graph(
    agent_id: str = Query(default="main", alias="agentId"),
    user: User = Depends(get_current_user),
):
    async with async_session() as db:
        container = await ensure_running(db, user.id)
    return knowledge_graph(container.docker_id, agent_id)


@router.get("/api/comworker/events/stream")
async def dedicated_events_stream(
    request: Request,
    token: str = "",
):
    # user is recovered inside backend from token for EventSource compatibility
    user = User(id="", username="", email="", password_hash="", runtime_mode="dedicated")
    backend = get_runtime_backend()
    return await backend.stream_events(RuntimeContext(user=user, scope="dedicated"), request, token)