"""Admin API routes for user and system management."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import secrets
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

import docker
from docker.errors import NotFound as DockerNotFound
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from sqlalchemy import cast, Date, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.audit import write_audit_log
from app.auth.dependencies import require_admin, require_setup_complete
from app.auth.service import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    hash_password,
)
from app.capabilities import capability_registry, capability_states, known_capabilities
from app.config import settings
from app.container.manager import (
    _apply_mcp_servers_to_container,
    _data_volume_name,
    _docker,
    _hermes_home_volume_name,
    _missing_required_config_fields,
    apply_container_capabilities,
    create_container,
    destroy_container,
    ensure_running,
    get_container,
    pause_container,
    resume_container,
)
from app.routes.models import (
    _read_container_config,
    _write_container_config,
    _hermes_to_frontend,
    _load_user_config,
    _merge_db_user_config,
)
from app.platform_skills import (
    PLATFORM_SKILLS_DIR,
    delete_platform_skill,
    get_platform_skill,
    list_platform_skills,
    set_platform_skill_enabled,
    set_platform_skill_meta,
    upload_platform_skill_zip,
)
from app.runtime_backends.hermes_skills import (
    HERMES_SKILLS_ROOT,
    _put_skill_archive,
    read_installed_marker,
    write_installed_marker,
    delete_skill_from_hermes_container,
)
from app.runtime_backends.hermes_agents import (
    SYSTEM_AGENT_IDS,
    _safe_agent_id,
    create_agent_profile_in_hermes_container,
    delete_agent_profile_from_hermes_container,
)
from app.runtime_backends.hermes_files import (
    delete_hermes_filemanager_path,
    make_hermes_filemanager_directory,
    read_file_from_hermes_container,
    write_upload_to_hermes_container,
)
from app.runtime_backends.hermes_knowledge import (
    knowledge_graph,
    list_knowledge_pages,
    read_knowledge_page,
    search_knowledge_pages,
    write_knowledge_page,
)
from app.db.engine import get_db
from app.db.models import AgentTemplate, McpConnector, UserMcpConnector
from app.db.models import (
    AuditLog,
    Container,
    DEFAULT_INSTALL_FLAG,
    ModelProviderConfig,
    PlatformCapabilityDefault,
    SkillOverride,
    SystemFlag,
    UsageRecord,
    User,
    UserCapability,
)
from app.model_keys import get_api_key, set_api_key
from app.model_config import _provider_visible_to_user, get_model_config_payload, set_default_model

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin), Depends(require_setup_complete)])

class UserSummary(BaseModel):
    id: str
    username: str
    email: str
    role: str
    quota_tier: str
    runtime_mode: str
    is_active: bool
    created_at: str | None = None
    container_status: str | None = None
    container_docker_id: str | None = None
    container_created_at: str | None = None
    shared_agent_id: str | None = None
    shared_agent_status: str | None = None
    tokens_used_today: int = 0

class PaginatedUsers(BaseModel):
    items: list[UserSummary]
    total: int
    page: int
    page_size: int

class UpdateUserRequest(BaseModel):
    role: str | None = None
    quota_tier: str | None = None
    runtime_mode: str | None = None
    is_active: bool | None = None

class CreateUserRequest(BaseModel):
    username: str
    email: str
    password: str = ""  # 留空则由系统生成强口令，并强制首次登录改密
    role: str = "user"
    quota_tier: str = "free"
    runtime_mode: str = "dedicated"

class ResetPasswordRequest(BaseModel):
    new_password: str

class ModelItemRequest(BaseModel):
    id: str
    name: str | None = None
    enabled: bool = True

class ProviderConfigRequest(BaseModel):
    name: str | None = None
    providerType: str | None = None
    api: str | None = None
    baseUrl: str | None = None
    apiKey: str | None = None
    models: list[ModelItemRequest] = []
    enabled: bool = True

class ModelsConfigRequest(BaseModel):
    defaultModel: str | None = None
    providers: dict[str, ProviderConfigRequest] | None = None


class UserModelItem(BaseModel):
    id: str
    name: str
    allowed: bool
    models: list[dict] = []


class UserModelsResponse(BaseModel):
    providers: list[UserModelItem]
    defaultModel: str | None = None


class UpdateUserModelsRequest(BaseModel):
    providers: dict[str, bool] | None = None
    defaultModel: str | None = None

async def _sync_container_status(db: AsyncSession, docker_id: str, db_status: str | None) -> str | None:
    """Sync container status from Docker API to database.
    
    Returns the real status from Docker, or None if container doesn't exist.
    """
    if not docker_id:
        return db_status
    
    try:
        client = docker.from_env()
        container = client.containers.get(docker_id)
        real_status = container.status  # running, exited, paused, created, etc.
        
        # Map Docker status to our DB status
        if real_status == "running":
            new_status = "running"
        elif real_status == "paused":
            new_status = "paused"
        elif real_status in ("exited", "dead", "removing"):
            new_status = "stopped"
        else:
            new_status = db_status  # keep DB status for other states like "creating"
        
        # Update DB if different
        if new_status != db_status:
            await db.execute(
                update(Container)
                .where(Container.docker_id == docker_id)
                .values(status=new_status)
            )
        
        return new_status
    except DockerNotFound:
        # Container was deleted externally, mark as stopped
        if db_status != "stopped":
            await db.execute(
                update(Container)
                .where(Container.docker_id == docker_id)
                .values(status="stopped")
            )
        return "stopped"
    except Exception:
        return db_status

@router.get("/users", response_model=PaginatedUsers)
async def list_users(
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # Subquery: today's token usage per user
    usage_sub = (
        select(
            UsageRecord.user_id,
            func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("tokens_today"),
        )
        .where(UsageRecord.created_at >= today_start)
        .group_by(UsageRecord.user_id)
        .subquery()
    )

    # Base query with outerjoin to Container and usage subquery
    query = (
        select(
            User.id,
            User.username,
            User.email,
            User.role,
            User.quota_tier,
            User.runtime_mode,
            User.is_active,
            User.created_at.label("user_created_at"),
            Container.status.label("container_status"),
            Container.docker_id.label("container_docker_id"),
            Container.created_at.label("container_created_at"),
            func.coalesce(usage_sub.c.tokens_today, 0).label("tokens_used_today"),
        )
        .outerjoin(Container, Container.user_id == User.id)
        .outerjoin(usage_sub, usage_sub.c.user_id == User.id)
    )

    # Search filter – escape SQL LIKE wildcards to prevent injection
    if search:
        safe = search.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{safe}%"
        query = query.where(
            (User.username.ilike(pattern))
            | (User.email.ilike(pattern))
            | (Container.docker_id.ilike(pattern))
        )

    # Total count (before pagination)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    # Paginate
    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).all()

    items = [
        UserSummary(
            id=row.id,
            username=row.username,
            email=row.email,
            role=row.role,
            quota_tier=row.quota_tier,
            runtime_mode=row.runtime_mode,
            is_active=row.is_active,
            created_at=row.user_created_at.isoformat() if row.user_created_at else None,
            container_status=row.container_status,
            container_docker_id=row.container_docker_id,
            container_created_at=row.container_created_at.isoformat() if row.container_created_at else None,
            shared_agent_id=None,
            shared_agent_status=None,
            tokens_used_today=row.tokens_used_today,
        )
        for row in rows
    ]

    return PaginatedUsers(items=items, total=total, page=page, page_size=page_size)

@router.post("/users")
async def create_user_handler(
    req: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    # Validate
    if not req.username.strip():
        raise HTTPException(status_code=400, detail="Username is required")
    if not req.email.strip():
        raise HTTPException(status_code=400, detail="Email is required")
    if req.password and len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if req.role not in {"user", "admin"}:
        raise HTTPException(status_code=400, detail="role must be user or admin")
    if req.quota_tier not in {"free", "basic", "pro"}:
        raise HTTPException(status_code=400, detail="quota_tier must be free, basic, or pro")
    if req.runtime_mode not in {"dedicated", "shared"}:
        raise HTTPException(status_code=400, detail="runtime_mode must be dedicated or shared")

    # Check uniqueness
    if await get_user_by_username(db, req.username.strip()):
        raise HTTPException(status_code=409, detail="Username already exists")
    if await get_user_by_email(db, req.email.strip()):
        raise HTTPException(status_code=409, detail="Email already exists")

    # 密码留空 → 系统生成强口令并强制首次登录改密（与初始化管理员姿态一致）
    must_change_password = False
    initial_password = None
    if not req.password:
        initial_password = secrets.token_urlsafe(12)
        must_change_password = True
    else:
        initial_password = req.password

    # 复用与客户端注册完全相同的创建逻辑，保证两条路径行为一致
    user = await create_user(
        db,
        req.username.strip(),
        req.email.strip(),
        initial_password,
        role=req.role,
        quota_tier=req.quota_tier,
        runtime_mode=req.runtime_mode,
        must_change_password=must_change_password,
    )
    await write_audit_log(
        db,
        action="user_create",
        user_id=admin_user.id,
        resource=user.id,
        detail={"username": user.username, "email": user.email, "role": user.role, "by_admin": admin_user.username},
    )
    await db.commit()
    await db.refresh(user)

    # 建用户即预创建运行时容器，用户首次登录零等待（失败不阻断账号创建）
    container_status = "pending"
    try:
        await ensure_running(db, user.id)
        container_status = "running"
    except Exception as e:  # noqa: BLE001
        logger.warning("预创建用户 %s 容器失败（登录时再懒创建）: %s", user.username, e)

    return {
        "ok": True,
        "user_id": user.id,
        "must_change_password": must_change_password,
        # 仅当系统自动生成口令时返回一次，供管理员抄送用户
        "initial_password": initial_password if must_change_password else None,
        "container_status": container_status,
    }


@router.post("/users/bulk")
async def bulk_create_users(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """批量导入用户（CSV 文件上传）。

    CSV 表头：username,email,password?,role?,quota_tier?,runtime_mode?
    - password 留空则由系统生成强口令并强制首次登录改密
    - 已存在的用户名/邮箱跳过（不报错）
    - 单行校验/创建失败不影响其余行（失败隔离）
    - 建用户即预创建运行时容器，用户首次登录零等待
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码需为 UTF-8")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not {"username", "email"}.issubset(set(reader.fieldnames)):
        raise HTTPException(status_code=400, detail="CSV 必须包含 username,email 列")

    valid_roles = {"user", "admin"}
    valid_tiers = {"free", "basic", "pro"}
    valid_modes = {"dedicated", "shared"}
    results = {"total": 0, "created": 0, "skipped": 0, "failed": 0, "details": []}

    for row in reader:
        results["total"] += 1
        username = (row.get("username") or "").strip()
        email = (row.get("email") or "").strip()
        password = (row.get("password") or "").strip()
        role = (row.get("role") or "user").strip() or "user"
        quota_tier = (row.get("quota_tier") or "free").strip() or "free"
        runtime_mode = (row.get("runtime_mode") or "dedicated").strip() or "dedicated"

        if not username or not email:
            results["failed"] += 1
            results["details"].append({"username": username, "status": "failed", "reason": "username/email 必填"})
            continue
        if password and len(password) < 8:
            results["failed"] += 1
            results["details"].append({"username": username, "status": "failed", "reason": "密码至少 8 位"})
            continue
        if role not in valid_roles or quota_tier not in valid_tiers or runtime_mode not in valid_modes:
            results["failed"] += 1
            results["details"].append({"username": username, "status": "failed", "reason": "字段取值非法"})
            continue
        if await get_user_by_username(db, username) or await get_user_by_email(db, email):
            results["skipped"] += 1
            results["details"].append({"username": username, "status": "skipped", "reason": "已存在"})
            continue

        must_change = False
        if not password:
            password = secrets.token_urlsafe(12)
            must_change = True
        try:
            user = await create_user(
                db,
                username,
                email,
                password,
                role=role,
                quota_tier=quota_tier,
                runtime_mode=runtime_mode,
                must_change_password=must_change,
            )
            try:
                await ensure_running(db, user.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("批量导入：预创建用户 %s 容器失败（登录时懒创建）: %s", username, e)
            results["created"] += 1
            results["details"].append({
                "username": username,
                "status": "created",
                "must_change_password": must_change,
                "initial_password": password if must_change else None,
            })
        except Exception as e:  # noqa: BLE001
            results["failed"] += 1
            results["details"].append({"username": username, "status": "failed", "reason": str(e)})

    await write_audit_log(
        db,
        action="user_bulk_import",
        user_id=admin_user.id,
        detail={"total": results["total"], "created": results["created"], "skipped": results["skipped"], "failed": results["failed"]},
    )
    return results


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    values = {k: v for k, v in req.model_dump().items() if v is not None}
    if values:
        await db.execute(update(User).where(User.id == user_id).values(**values))
        await write_audit_log(
            db,
            action="user_update",
            user_id=admin_user.id,
            resource=user_id,
            detail={"fields": values},
        )
        await db.commit()
    return {"ok": True}

@router.put("/users/{user_id}/password")
async def reset_user_password(
    user_id: str,
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await db.execute(
        update(User).where(User.id == user_id).values(password_hash=hash_password(req.new_password))
    )
    await write_audit_log(
        db,
        action="password_reset",
        user_id=admin_user.id,
        resource=user_id,
        detail={"by_admin": admin_user.username},
    )
    await db.commit()
    return {"message": "Password updated"}

@router.delete("/users/{user_id}/container")
async def delete_user_container(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if await destroy_container(db, user_id):
        await write_audit_log(
            db,
            action="container_destroy",
            user_id=admin_user.id,
            resource=user_id,
            detail={"by_admin": admin_user.username},
        )
        await db.commit()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Container not found")

@router.post("/containers/sync")
async def sync_all_container_statuses(
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """Sync all container statuses from Docker to database.
    
    Returns the count of updated containers.
    """
    result = await db.execute(
        select(Container.id, Container.user_id, Container.docker_id, Container.status)
    )
    containers = result.all()
    
    if not containers:
        return {"updated": 0, "message": "No containers found"}
    
    updated_count = 0
    for container in containers:
        if container.docker_id:
            real_status = await _sync_container_status(db, container.docker_id, container.status)
            if real_status != container.status:
                updated_count += 1
    
    await write_audit_log(
        db,
        action="container_sync_all",
        user_id=admin_user.id,
        resource="all",
        detail={"updated": updated_count},
    )
    await db.commit()
    
    return {"updated": updated_count, "message": f"Synced {updated_count} containers"}

@router.post("/users/{user_id}/container/sync")
async def sync_single_container_status(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """Sync a single user's container status from Docker to database."""
    result = await db.execute(
        select(Container).where(Container.user_id == user_id)
    )
    container = result.scalar_one_or_none()
    
    if container is None:
        raise HTTPException(status_code=404, detail="Container not found")
    
    if not container.docker_id:
        raise HTTPException(status_code=400, detail="No docker_id for this container")
    
    real_status = await _sync_container_status(db, container.docker_id, container.status)
    await write_audit_log(
        db,
        action="container_sync",
        user_id=admin_user.id,
        resource=user_id,
        detail={"status": real_status, "docker_id": container.docker_id},
    )
    await db.commit()
    
    return {"status": real_status, "docker_id": container.docker_id}

@router.get("/models")
async def get_models_config(db: AsyncSession = Depends(get_db)):
    return await get_model_config_payload(db, include_secret=False)

@router.put("/models")
async def update_models_config(
    req: ModelsConfigRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if req.providers is not None:
        existing = {
            provider.id: provider
            for provider in (await db.execute(select(ModelProviderConfig))).scalars().all()
        }
        seen: set[str] = set()
        for provider_id, payload in req.providers.items():
            clean_id = provider_id.strip().lower()
            if not clean_id:
                raise HTTPException(status_code=400, detail="Provider id is required")
            seen.add(clean_id)
            models = [
                {"id": item.id.strip(), "name": (item.name or item.id).strip(), "enabled": item.enabled}
                for item in payload.models
                if item.id.strip()
            ]
            if not models:
                raise HTTPException(status_code=400, detail=f"Provider {clean_id} must include at least one model")
            provider = existing.get(clean_id)
            if provider is None:
                provider = ModelProviderConfig(id=clean_id, display_name=payload.name or clean_id)
                db.add(provider)
            provider.display_name = (payload.name or clean_id).strip()
            provider.provider_type = (payload.providerType or clean_id).strip()
            # Persist the explicit API protocol (if the admin set one). This is
            # the authoritative protocol for both the connectivity probe and the
            # live LLM proxy, overriding provider_type/base_url heuristics.
            provider.api_mode = (payload.api or "").strip() or None
            provider.api_base = (payload.baseUrl or "").strip() or None
            if payload.apiKey is not None and payload.apiKey.strip():
                from app.model_keys import set_api_key
                set_api_key(provider, payload.apiKey.strip())
            provider.models = models
            provider.enabled = payload.enabled

        for provider_id, provider in existing.items():
            if provider_id not in seen:
                await db.delete(provider)

        await db.commit()

    if req.defaultModel:
        default_pid = req.defaultModel.split("/", 1)[0].strip().lower()
        if default_pid not in seen:
            # The default model's provider was just deleted. Auto-reassign the
            # default to the first remaining provider/model that is enabled and
            # has a key, so the save does not 400 and leave a broken state.
            new_default = None
            for pid in seen:
                p = existing.get(pid)
                if p and p.enabled and get_api_key(p) and p.models:
                    for m in p.models:
                        if isinstance(m, dict) and m.get("enabled") is not False and m.get("id"):
                            new_default = f"{p.id}/{m['id']}"
                            break
                if new_default:
                    break
            req.defaultModel = new_default
        if req.defaultModel:
            await set_default_model(db, req.defaultModel.strip())

    await write_audit_log(
        db,
        action="model_config_update",
        user_id=admin_user.id,
        resource="models",
        detail={"defaultModel": req.defaultModel, "providersUpdated": req.providers is not None},
    )
    await db.commit()

    # Mark the "add at least one model API key" setup step as done.
    # Idempotent — only stamps the first time we see an active provider
    # with a real API key.
    try:
        from app.setup_state import mark_model_key_added

        await mark_model_key_added(db)
    except Exception:  # noqa: BLE001
        pass  # never fail a successful model-config save on the wizard flag

    return {"ok": True, **await get_model_config_payload(db, include_secret=False)}


def _apply_scope_toggle(scope: dict | None, user_id: str, allowed: bool) -> dict:
    """Transition a provider's user_scope so that `user_id` ends up allowed/not.

    Modes: all (everyone) | allow (only listed) | deny (everyone except listed).
    """
    scope = scope if isinstance(scope, dict) else {}
    mode = scope.get("mode", "all")
    ids = list(scope.get("user_ids") or [])

    if mode == "all":
        if allowed:
            return {"mode": "all"}
        return {"mode": "deny", "user_ids": [user_id]}
    if mode == "allow":
        if allowed:
            if user_id not in ids:
                ids.append(user_id)
            return {"mode": "allow", "user_ids": ids}
        if user_id in ids:
            ids.remove(user_id)
        return {"mode": "allow", "user_ids": ids}
    # deny
    if allowed:
        if user_id in ids:
            ids.remove(user_id)
        return {"mode": "all"} if not ids else {"mode": "deny", "user_ids": ids}
    if user_id not in ids:
        ids.append(user_id)
    return {"mode": "deny", "user_ids": ids}


@router.get("/users/{user_id}/models")
async def get_user_models(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result = await db.execute(select(ModelProviderConfig).order_by(ModelProviderConfig.created_at.asc()))
    providers = list(result.scalars().all())
    items = [
        UserModelItem(
            id=p.id,
            name=p.display_name,
            allowed=_provider_visible_to_user(p, user_id),
            models=p.models or [],
        )
        for p in providers
    ]
    return UserModelsResponse(providers=items, defaultModel=user.default_model)


@router.put("/users/{user_id}/models")
async def update_user_models(
    user_id: str,
    req: UpdateUserModelsRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if req.providers is not None:
        existing = {
            p.id: p
            for p in (await db.execute(select(ModelProviderConfig))).scalars().all()
        }
        for provider_id, allowed in req.providers.items():
            provider = existing.get(provider_id.strip().lower())
            if provider is None:
                continue
            provider.user_scope = _apply_scope_toggle(provider.user_scope, user_id, allowed)

    if req.defaultModel is not None:
        dm = (req.defaultModel or "").strip()
        if dm:
            if "/" not in dm:
                raise HTTPException(status_code=400, detail="defaultModel must be 'provider/model'")
            pid, mid = dm.split("/", 1)
            provider = await db.get(ModelProviderConfig, pid)
            if provider is None or not _provider_visible_to_user(provider, user_id):
                raise HTTPException(status_code=400, detail=f"Default model '{dm}' is not available to this user")
            if not any(
                isinstance(m, dict) and m.get("enabled") is not False and str(m.get("id") or "") == mid
                for m in (provider.models or [])
            ):
                raise HTTPException(status_code=400, detail=f"Model '{dm}' is not enabled")
            user.default_model = dm
        else:
            user.default_model = None

    await db.commit()
    await write_audit_log(
        db,
        action="user_model_assign",
        user_id=admin_user.id,
        resource=user_id,
        detail={"providers": req.providers, "defaultModel": req.defaultModel},
    )
    return await get_user_models(user_id, db)


@router.get("/users/{user_id}/models/configured")
async def get_user_configured_models(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """Read-only view of the user's *actual* model configuration stored in their
    personal hermes container config.yaml — i.e. the providers / API keys / models
    they added in the client (AI 模型 page). This is a different layer from the
    platform-level visibility & defaults returned by GET /users/{user_id}/models.

    API keys are masked: only whether a key is configured is exposed, never the
    raw secret.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    container_name = f"hermes-user-{user_id[:8]}"
    fe: dict = {"models": [], "configuredModel": "", "configuredProviders": {}}
    try:
        from app.routes.models import (
            _read_container_config,
            _hermes_to_frontend,
            _load_user_config,
            _merge_db_user_config,
        )
        try:
            await ensure_running(db, user_id)
        except Exception as e:  # container not startable — fall back to DB user_config only
            logger.warning("ensure_running skipped for %s: %s", user_id, e)
        config = _read_container_config(container_name)
        db_uc = await _load_user_config(db, user_id)
        config = _merge_db_user_config(config, db_uc)
        fe = await _hermes_to_frontend(config, db, user_id=user_id)
    except Exception as e:
        logger.warning("Failed to read configured models for %s: %s", user_id, e)

    disabled_set = set()
    try:
        _uc = await _load_user_config(db, user_id)
        disabled_set = set((_uc or {}).get("disabled_providers") or [])
    except Exception:
        pass

    providers = []
    for name, p in (fe.get("configuredProviders") or {}).items():
        if not isinstance(p, dict):
            continue
        providers.append({
            "name": name,
            "baseUrl": p.get("baseUrl", ""),
            "api": p.get("api", ""),
            "hasApiKey": bool(p.get("apiKey")),
            "system": bool(p.get("_system")),
            "disabled": name in disabled_set,
            "models": p.get("models") or [],
        })
    return {
        "defaultModel": fe.get("configuredModel", ""),
        "providers": providers,
        "models": fe.get("models") or [],
    }


@router.post("/users/{user_id}/container/pause")
async def pause_user_container(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if await pause_container(db, user_id):
        await write_audit_log(
            db,
            action="container_pause",
            user_id=admin_user.id,
            resource=user_id,
            detail={"by_admin": admin_user.username},
        )
        await db.commit()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Container not running")

@router.post("/users/{user_id}/container/resume")
async def resume_user_container(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if await resume_container(db, user_id):
        await write_audit_log(
            db,
            action="container_resume",
            user_id=admin_user.id,
            resource=user_id,
            detail={"by_admin": admin_user.username},
        )
        await db.commit()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="Container not found or cannot be resumed")


# ─── P0: 数据保全层（历史数据不丢 + 可管理） ───────────────────────────────

def _count_in_container(docker_id: str, sh_cmd: str) -> int:
    """在用户容器内执行 shell 命令并返回首个单词的整数（用于计数）。"""
    try:
        c = _docker().containers.get(docker_id)
        out = c.exec_run(["sh", "-c", sh_cmd])
        val = (out.output or b"0").decode().strip().split()[0] if (out.output or b"").strip() else "0"
        return int(val) if val.isdigit() else 0
    except Exception:  # noqa: BLE001
        return 0


@router.get("/users/{user_id}/data-footprint")
async def user_data_footprint(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """某用户历史数据足迹：会话数 / KB 文档数 / 连接器 / 技能 / 配置状态，供管理端『数据管理』展示。"""
    container = await get_container(db, user_id)
    footprint = {
        "user_id": user_id,
        "container_status": container.status if container else None,
        "user_config_present": bool(container and container.user_config),
        "volume_present": False,
        "connectors_count": 0,
        "connectors": [],
        "skills_user": [],
        "skills_managed": [],
        "sessions_count": 0,
        "kb_docs_count": 0,
    }
    if container is not None:
        res = await db.execute(
            select(UserMcpConnector).where(UserMcpConnector.user_id == user_id)
        )
        rows = res.scalars().all()
        footprint["connectors_count"] = len(rows)
        footprint["connectors"] = [
            {
                "id": r.id,
                "name": r.name,
                "connector_id": r.connector_id,
                "enabled": r.enabled,
                "personal": r.connector_id is None,
            }
            for r in rows
        ]
        if container.docker_id:
            footprint["sessions_count"] = _count_in_container(
                container.docker_id,
                "ls /opt/data/agents/main/sessions/*.jsonl 2>/dev/null | wc -l",
            )
            footprint["kb_docs_count"] = _count_in_container(
                container.docker_id,
                "find /opt/data/profiles/main/workspace/knowledge -type f 2>/dev/null | wc -l",
            )
            try:
                _docker().containers.get(container.docker_id)
                footprint["volume_present"] = True
            except Exception:  # noqa: BLE001
                footprint["volume_present"] = False
            try:
                marker = read_installed_marker(container.docker_id)
                footprint["skills_user"] = sorted(marker.get("user", []))
                footprint["skills_managed"] = sorted(marker.get("managed", []))
            except Exception:  # noqa: BLE001
                pass
    return footprint


@router.get("/users/{user_id}/data-integrity")
async def user_data_integrity(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """镜像/容器更新或重建后，核对历史数据完整性（配置/会话/KB 是否在），异常即时可见。"""
    container = await get_container(db, user_id)
    checks: list[dict] = [
        {
            "name": "user_config_in_db",
            "ok": bool(container and container.user_config),
            "detail": "provider key + 默认模型 已在 DB 持久，卷丢失也可恢复",
        }
    ]
    if container is None:
        return {"healthy": False, "checks": checks + [{"name": "container_exists", "ok": False, "detail": "无容器记录"}]}
    if container.docker_id:
        try:
            c = _docker().containers.get(container.docker_id)
            r = c.exec_run(["sh", "-c", "test -f /opt/data/config.yaml && echo yes || echo no"])
            cfg_in_vol = "yes" in (r.output or b"").decode()
            checks.append({"name": "config_yaml_in_volume", "ok": cfg_in_vol, "detail": "/opt/data/config.yaml"})
            n = _count_in_container(container.docker_id, "ls /opt/data/agents/main/sessions/*.jsonl 2>/dev/null | wc -l")
            checks.append({"name": "sessions_present", "ok": n >= 0, "detail": f"{n} 个会话文件"})
            kb = _count_in_container(container.docker_id, "find /opt/data/profiles/main/workspace/knowledge -type f 2>/dev/null | wc -l")
            checks.append({"name": "knowledge_present", "ok": True, "detail": f"{kb} 个知识库文件"})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "container_reachable", "ok": False, "detail": str(exc)})
    healthy = all(ch["ok"] for ch in checks)
    return {"healthy": healthy, "checks": checks}


@router.post("/users/{user_id}/container/recreate")
async def recreate_user_container(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """保留数据重建：销毁容器但保留同名数据卷，再按相同卷名 + 回灌 user_config 重建，卷内会话/KB/技能/配置全部保留。"""
    record = await get_container(db, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="container not found")
    saved_user_config = record.user_config
    await destroy_container(db, user_id)
    created = await create_container(db, user_id, preset_user_config=saved_user_config)
    if created is None:
        raise HTTPException(status_code=500, detail="recreate failed")
    await write_audit_log(db, action="container_recreate_preserve", user_id=admin_user.id, resource=user_id)
    await db.commit()
    return {"ok": True, "preserved": True}


class DestroyContainerRequest(BaseModel):
    wipe_data: bool = False
    confirm: bool = False


@router.post("/users/{user_id}/container/destroy")
async def destroy_user_container(
    user_id: str,
    req: DestroyContainerRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """销毁容器。默认保留数据卷（日后重建可恢复）；仅当 wipe_data=true 且 confirm=true 才真正删除卷（永久丢失）。"""
    if req.wipe_data and not req.confirm:
        raise HTTPException(status_code=400, detail="wipe_data 需要 confirm=true 二次确认")
    record = await get_container(db, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="container not found")
    await destroy_container(db, user_id)
    wiped = False
    if req.wipe_data:
        try:
            client = _docker()
            for v in (_data_volume_name(user_id[:8]), _hermes_home_volume_name(user_id[:8])):
                try:
                    client.volumes.get(v).remove(force=True)
                except DockerNotFound:
                    pass
            wiped = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("wipe volumes failed for %s: %s", user_id, exc)
    await write_audit_log(db, action="container_destroy", user_id=admin_user.id, resource=user_id,
                          detail={"wiped": wiped})
    await db.commit()
    return {"ok": True, "wiped": wiped}


# ---------------------------------------------------------------------------
# 用户知识库管理（按 user 解析容器，默认 main agent；复用 hermes_knowledge）
# 历史数据位于 /opt/data/profiles/<agent>/workspace/knowledge，落在同名数据卷，
# 容器重建/镜像更新均保留；管理端可查看/导出/清理，不影响持久化。
# ---------------------------------------------------------------------------

def _kb_agent_id(agent_id: str | None) -> str:
    return (agent_id or "main").strip() or "main"


def _kb_storage_path(agent_id: str, rel_path: str) -> str:
    rel = (rel_path or "").strip().lstrip("/")
    return f"profiles/{_kb_agent_id(agent_id)}/workspace/knowledge/{rel}".rstrip("/")


async def _kb_docker_id(db: AsyncSession, user_id: str) -> str:
    """解析用户容器 docker_id，必要时自动启动，保证知识库读写可用。"""
    try:
        record = await ensure_running(db, user_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"无法启动用户容器: {exc}") from exc
    if record is None or not record.docker_id:
        raise HTTPException(status_code=404, detail="container not found")
    return record.docker_id


@router.get("/users/{user_id}/knowledge")
async def admin_user_knowledge_list(
    user_id: str,
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """列出某用户知识库：pages / directories / attachments（默认 main agent）。"""
    docker_id = await _kb_docker_id(db, user_id)
    return list_knowledge_pages(docker_id, _kb_agent_id(agent_id))


@router.get("/users/{user_id}/knowledge/page")
async def admin_user_knowledge_read(
    user_id: str,
    path: str,
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """读取单篇知识库文档（含正文与反链）。"""
    docker_id = await _kb_docker_id(db, user_id)
    return read_knowledge_page(docker_id, _kb_agent_id(agent_id), path)


@router.get("/users/{user_id}/knowledge/search")
async def admin_user_knowledge_search(
    user_id: str,
    q: str = "",
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """在用户知识库中按关键词检索。"""
    docker_id = await _kb_docker_id(db, user_id)
    return search_knowledge_pages(docker_id, _kb_agent_id(agent_id), q)


@router.get("/users/{user_id}/knowledge/graph")
async def admin_user_knowledge_graph(
    user_id: str,
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """用户知识库 wikilink 关系图谱。"""
    docker_id = await _kb_docker_id(db, user_id)
    return knowledge_graph(docker_id, _kb_agent_id(agent_id))


class KnowledgeWriteRequest(BaseModel):
    path: str
    content: str
    agent_id: str | None = None


@router.post("/users/{user_id}/knowledge/page")
async def admin_user_knowledge_write(
    user_id: str,
    req: KnowledgeWriteRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """新建或覆盖知识库文档（Markdown）。"""
    docker_id = await _kb_docker_id(db, user_id)
    return write_knowledge_page(docker_id, _kb_agent_id(req.agent_id), req.path, req.content)


class KnowledgePathRequest(BaseModel):
    path: str
    agent_id: str | None = None


@router.post("/users/{user_id}/knowledge/mkdir")
async def admin_user_knowledge_mkdir(
    user_id: str,
    req: KnowledgePathRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """在知识库内创建文件夹。"""
    docker_id = await _kb_docker_id(db, user_id)
    return make_hermes_filemanager_directory(docker_id, _kb_storage_path(_kb_agent_id(req.agent_id), req.path))


@router.post("/users/{user_id}/knowledge/delete")
async def admin_user_knowledge_delete(
    user_id: str,
    req: KnowledgePathRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """删除知识库文档或文件夹（卷内数据删除，重建不可恢复，故需管理员显式操作）。"""
    docker_id = await _kb_docker_id(db, user_id)
    result = delete_hermes_filemanager_path(docker_id, _kb_storage_path(_kb_agent_id(req.agent_id), req.path))
    await write_audit_log(db, action="admin_knowledge_delete", user_id=admin_user.id, resource=user_id,
                          detail={"path": req.path, "agent": _kb_agent_id(req.agent_id)})
    await db.commit()
    return result


@router.post("/users/{user_id}/knowledge/upload")
async def admin_user_knowledge_upload(
    user_id: str,
    file: UploadFile = File(...),
    agent_id: str | None = Form(None),
    parent: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """上传文件到用户知识库（Markdown 自动进索引）。"""
    docker_id = await _kb_docker_id(db, user_id)
    target_dir = _kb_storage_path(_kb_agent_id(agent_id), parent)
    return await write_upload_to_hermes_container(docker_id, file, target_dir)


@router.get("/users/{user_id}/knowledge/file")
async def admin_user_knowledge_download(
    user_id: str,
    path: str,
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """下载单篇知识库文档/文件。"""
    docker_id = await _kb_docker_id(db, user_id)
    data, media_type = read_file_from_hermes_container(docker_id, _kb_storage_path(_kb_agent_id(agent_id), path))
    filename = path.rsplit("/", 1)[-1] or "download.bin"
    return StreamingResponse(
        iter([data]),
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/users/{user_id}/container/backup")
async def backup_user_volume(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """导出用户 /opt/data 卷为 tar.gz，封住『误删命名卷即永久丢失』的唯一漏洞。"""
    record = await get_container(db, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="container not found")
    vol = _hermes_home_volume_name(user_id[:8])
    client = _docker()
    helper = client.containers.run(
        settings.hermes_image,
        command=["tar", "czf", "-", "-C", "/opt/data", "."],
        mounts=[docker.types.Mount("/opt/data", vol, type="volume")],
        stdout=True, stderr=False, detach=True, remove=True,
    )

    def _stream():
        try:
            for chunk in helper.logs(stream=True, stdout=True, stderr=False):
                yield chunk
        except Exception:  # noqa: BLE001
            return

    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="user-{user_id}-data-backup.tar.gz"'},
    )


@router.post("/users/{user_id}/container/restore")
async def restore_user_volume(
    user_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """从备份 tar.gz 恢复用户 /opt/data 卷（需先暂停/停止容器，避免写入冲突）。"""
    record = await get_container(db, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="container not found")
    if record.status == "running":
        raise HTTPException(status_code=409, detail="恢复前请先暂停/停止容器")
    raw = await file.read()
    tar_bytes = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    client = _docker()
    vol = _hermes_home_volume_name(user_id[:8])
    helper = client.containers.run(
        settings.hermes_image,
        command=["sleep", "600"],
        mounts=[docker.types.Mount("/opt/data", vol, type="volume")],
        detach=True, remove=True,
    )
    try:
        ok, msg = helper.put_archive("/opt/data", tar_bytes)
        if not ok:
            raise HTTPException(status_code=500, detail=f"恢复失败: {msg}")
    finally:
        try:
            helper.remove(force=True)
        except Exception:  # noqa: BLE001
            pass
    await write_audit_log(db, action="container_restore", user_id=admin_user.id, resource=user_id)
    await db.commit()
    return {"ok": True}


@router.get("/usage/summary")
async def usage_summary(db: AsyncSession = Depends(get_db)):
    """Global usage summary for the platform."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total_today = (await db.execute(
        select(func.coalesce(func.sum(UsageRecord.total_tokens), 0)).where(
            UsageRecord.created_at >= today_start,
        )
    )).scalar_one()
    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    
    # Get containers with real running status from Docker
    try:
        client = docker.from_env()
        all_containers = client.containers.list(all=True)
        real_running = sum(1 for c in all_containers if c.status == "running")
    except Exception:
        real_running = 0
    
    # Fallback to DB status if Docker query fails
    db_active = (await db.execute(
        select(func.count(Container.id)).where(Container.status == "running")
    )).scalar_one()

    return {
        "total_tokens_today": total_today,
        "total_users": total_users,
        "active_containers": real_running or db_active,
    }

@router.get("/usage/history")
async def usage_history(
    days: int = 30,
    user_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Usage history with daily and by-model aggregations."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    # --- Daily aggregation ---
    daily_q = (
        select(
            cast(UsageRecord.created_at, Date).label("date"),
            func.coalesce(func.sum(UsageRecord.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
        )
        .where(UsageRecord.created_at >= cutoff)
        .group_by(cast(UsageRecord.created_at, Date))
        .order_by(cast(UsageRecord.created_at, Date))
    )
    if user_id:
        daily_q = daily_q.where(UsageRecord.user_id == user_id)

    daily_rows = (await db.execute(daily_q)).all()
    daily = [
        {
            "date": str(r.date),
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_tokens": r.total_tokens,
        }
        for r in daily_rows
    ]

    # --- By model aggregation ---
    model_q = (
        select(
            UsageRecord.user_id,
            User.username.label("username"),
            UsageRecord.model,
            UsageRecord.provider_id,
            UsageRecord.upstream_model,
            func.coalesce(func.sum(UsageRecord.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageRecord.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0).label("total_tokens"),
        )
        .join(User, User.id == UsageRecord.user_id, isouter=True)
        .where(UsageRecord.created_at >= cutoff)
        .group_by(UsageRecord.user_id, User.username, UsageRecord.model, UsageRecord.provider_id, UsageRecord.upstream_model)
        .order_by(func.sum(UsageRecord.total_tokens).desc())
    )
    if user_id:
        model_q = model_q.where(UsageRecord.user_id == user_id)

    model_rows = (await db.execute(model_q)).all()
    by_model = [
        {
            "user_id": r.user_id,
            "username": r.username,
            "model": r.model,
            "provider_id": r.provider_id,
            "upstream_model": r.upstream_model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_tokens": r.total_tokens,
        }
        for r in model_rows
    ]

    return {"daily": daily, "by_model": by_model}

# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

class AuditLogItem(BaseModel):
    id: str
    user_id: str | None
    username: str | None
    action: str
    resource: str | None
    detail: str | None
    ip: str | None
    user_agent: str | None
    request_id: str | None
    status_code: int | None
    created_at: str

class PaginatedAuditLogs(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int

@router.get("/audit", response_model=PaginatedAuditLogs)
async def list_audit_logs(
    page: int = 1,
    page_size: int = 20,
    user_id: str | None = None,
    action: str | None = None,
    request_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Paginated audit log with optional filters."""
    query = (
        select(
            AuditLog.id,
            AuditLog.user_id,
            User.username.label("username"),
            AuditLog.action,
            AuditLog.resource,
            AuditLog.detail,
            AuditLog.ip,
            AuditLog.user_agent,
            AuditLog.request_id,
            AuditLog.status_code,
            AuditLog.created_at,
        )
        .outerjoin(User, User.id == AuditLog.user_id)
    )

    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if request_id:
        query = query.where(AuditLog.request_id == request_id)

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    # Paginate
    query = query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(query)).all()

    items = [
        AuditLogItem(
            id=row.id,
            user_id=row.user_id,
            username=row.username,
            action=row.action,
            resource=row.resource,
            detail=row.detail,
            ip=row.ip,
            user_agent=row.user_agent,
            request_id=row.request_id,
            status_code=row.status_code,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]

    return PaginatedAuditLogs(items=items, total=total, page=page, page_size=page_size)

class CapabilityDefaultItem(BaseModel):
    capability: str
    default_inject: bool

class CapabilityDefaultsRequest(BaseModel):
    defaults: list[CapabilityDefaultItem]

@router.get("/capabilities/registry")
async def get_capability_registry(admin_user: User = Depends(require_admin)):
    return {"capabilities": capability_registry(settings)}

@router.get("/capabilities/defaults")
async def get_capability_defaults(
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    rows = (await db.execute(select(PlatformCapabilityDefault))).scalars().all()
    return {
        "defaults": [
            {
                "capability": r.capability,
                "default_inject": r.default_inject,
                "config": r.config,
            }
            for r in rows
        ]
    }

@router.put("/capabilities/defaults")
async def put_capability_defaults(
    req: CapabilityDefaultsRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    for item in req.defaults:
        stmt = (
            pg_insert(PlatformCapabilityDefault)
            .values(capability=item.capability, default_inject=item.default_inject)
            .on_conflict_do_update(
                index_elements=["capability"],
                set_={"default_inject": item.default_inject},
            )
        )
        await db.execute(stmt)
    await write_audit_log(
        db,
        action="capability_defaults_update",
        user_id=admin_user.id,
        resource="platform",
        detail={"defaults": [d.model_dump() for d in req.defaults]},
    )
    await db.commit()
    return {"ok": True}

class UserCapabilityItem(BaseModel):
    capability: str
    enabled: bool

class UserCapabilitiesRequest(BaseModel):
    capabilities: list[UserCapabilityItem]
    remove: list[str] = []  # capabilities whose per-user override should be dropped (back to default)

@router.get("/users/{user_id}/capabilities")
async def get_user_capabilities(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    uc_rows = (
        await db.execute(
            select(UserCapability).where(UserCapability.user_id == user_id)
        )
    ).scalars().all()
    user_caps = {r.capability: r for r in uc_rows}
    pd_rows = (await db.execute(select(PlatformCapabilityDefault))).scalars().all()
    platform_defaults = {r.capability: r for r in pd_rows}
    states = capability_states(settings, user_caps, platform_defaults)
    return {
        "states": states,
        "user_caps": [
            {"capability": r.capability, "enabled": r.enabled} for r in uc_rows
        ],
    }

@router.put("/users/{user_id}/capabilities")
async def put_user_capabilities(
    user_id: str,
    req: UserCapabilitiesRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    for item in req.capabilities:
        stmt = (
            pg_insert(UserCapability)
            .values(user_id=user_id, capability=item.capability, enabled=item.enabled)
            .on_conflict_do_update(
                index_elements=["user_id", "capability"],
                set_={"enabled": item.enabled},
            )
        )
        await db.execute(stmt)
    if req.remove:
        await db.execute(
            delete(UserCapability).where(
                UserCapability.user_id == user_id,
                UserCapability.capability.in_(req.remove),
            )
        )
    await write_audit_log(
        db,
        action="user_capabilities_update",
        user_id=admin_user.id,
        resource=user_id,
        detail={"capabilities": [c.model_dump() for c in req.capabilities], "remove": req.remove},
    )
    await db.commit()
    return {"ok": True}

class BulkCapabilityTarget(BaseModel):
    user_ids: list[str] | None = None
    all: bool = False
    tiers: list[str] | None = None

class BulkCapabilityRequest(BaseModel):
    capability: str
    enabled: bool
    target: BulkCapabilityTarget

@router.post("/capabilities/bulk")
async def bulk_set_capability(
    req: BulkCapabilityRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    if req.capability not in known_capabilities():
        raise HTTPException(status_code=400, detail=f"unknown capability: {req.capability}")

    if req.target.all:
        user_ids = (await db.execute(select(User.id))).scalars().all()
    elif req.target.tiers:
        user_ids = (
            await db.execute(select(User.id).where(User.quota_tier.in_(req.target.tiers)))
        ).scalars().all()
    elif req.target.user_ids:
        user_ids = req.target.user_ids
    else:
        raise HTTPException(
            status_code=400,
            detail="target required: user_ids | all | tiers",
        )

    if not user_ids:
        return {"ok": True, "affected": 0}

    values = [
        {"user_id": uid, "capability": req.capability, "enabled": req.enabled}
        for uid in user_ids
    ]
    stmt = (
        pg_insert(UserCapability)
        .values(values)
        .on_conflict_do_update(
            index_elements=["user_id", "capability"],
            set_={"enabled": req.enabled},
        )
    )
    await db.execute(stmt)
    await write_audit_log(
        db,
        action="capability_bulk_update",
        user_id=admin_user.id,
        resource="platform",
        detail={
            "capability": req.capability,
            "enabled": req.enabled,
            "target": req.target.model_dump(),
            "affected": len(user_ids),
        },
    )
    await db.commit()
    return {"ok": True, "affected": len(user_ids)}

# Delay between sequential container restarts during bulk-apply, to avoid a
# burst of concurrent restarts hammering the Docker daemon.
_BULK_APPLY_RESTART_DELAY = 1.0

class BulkApplyCapabilitiesRequest(BaseModel):
    target: BulkCapabilityTarget

@router.post("/users/{user_id}/capabilities/apply")
async def apply_user_capabilities(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """Hot-patch a running container's capability config and restart it.

    Refused (not applied) when the user has no container, the container is gone
    from Docker, or it is not running — applying to a stopped container would
    silently lose user-set keys, so it is rejected rather than risking data loss.
    """
    result = await apply_container_capabilities(db, user_id)
    if not result.get("applied"):
        reason = result.get("reason", "error")
        if reason in ("no_container", "container_missing"):
            raise HTTPException(status_code=404, detail="Container not found")
        if reason == "not_running":
            raise HTTPException(
                status_code=409,
                detail="Container is not running; start it before applying capabilities",
            )
        raise HTTPException(status_code=500, detail=f"apply failed: {reason}")

    await write_audit_log(
        db,
        action="capability_apply",
        user_id=admin_user.id,
        resource=user_id,
        detail={"toolsets": result.get("toolsets"), "by_admin": admin_user.username},
    )
    await db.commit()
    return {"ok": True, "applied": True, "toolsets": result.get("toolsets")}

@router.post("/capabilities/bulk-apply")
async def bulk_apply_capabilities(
    req: BulkApplyCapabilitiesRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """Apply each selected user's current capability state to their container.

    Restart-heavy: containers are restarted sequentially with a short delay so
    Docker is not hit with a burst of concurrent restarts. Users without a
    running container are reported as ``skipped`` (not an error). A per-user
    failure does not abort the batch.
    """
    if req.target.all:
        user_ids = (await db.execute(select(User.id))).scalars().all()
    elif req.target.tiers:
        user_ids = (
            await db.execute(select(User.id).where(User.quota_tier.in_(req.target.tiers)))
        ).scalars().all()
    elif req.target.user_ids:
        user_ids = req.target.user_ids
    else:
        raise HTTPException(
            status_code=400,
            detail="target required: user_ids | all | tiers",
        )

    applied: list[str] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    for index, uid in enumerate(user_ids):
        if index:
            await asyncio.sleep(_BULK_APPLY_RESTART_DELAY)
        try:
            result = await apply_container_capabilities(db, uid)
        except Exception as exc:  # noqa: BLE001 - isolate per-user failures
            failed.append({"user_id": uid, "error": str(exc)})
            continue
        if result.get("applied"):
            applied.append(uid)
        else:
            skipped.append({"user_id": uid, "reason": result.get("reason", "error")})

    await write_audit_log(
        db,
        action="capability_bulk_apply",
        user_id=admin_user.id,
        resource="platform",
        detail={
            "target": req.target.model_dump(),
            "applied": len(applied),
            "skipped": len(skipped),
            "failed": len(failed),
            "by_admin": admin_user.username,
        },
    )
    await db.commit()
    return {
        "ok": True,
        "applied": applied,
        "applied_count": len(applied),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "failed": failed,
        "failed_count": len(failed),
    }

# ---------------------------------------------------------------------------
# Platform skills (admin-managed, auto-installed into new containers)
# ---------------------------------------------------------------------------

@router.get("/skills")
async def admin_list_skills(admin_user: User = Depends(require_admin)):
    return {"skills": list_platform_skills()}

@router.post("/skills/upload")
async def admin_upload_skill(
    file: UploadFile = File(...),
    admin_user: User = Depends(require_admin),
):
    result = upload_platform_skill_zip(file)
    return {"ok": True, **result}

@router.delete("/skills/{name}")
async def admin_delete_skill(
    name: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除技能。平台技能物理删除；内置 catalog 技能因目录只读，改为标记覆盖（deleted）。"""
    catalog = _load_catalog()
    if name in (catalog.get("skills") or {}):
        await _upsert_skill_override(db, name, deleted=True)
        await write_audit_log(db, action="admin_delete_skill", user_id=admin_user.id, detail={"name": name, "kind": "builtin"})
        return {"ok": True, "name": name, "kind": "builtin"}
    delete_platform_skill(name)
    await write_audit_log(db, action="admin_delete_skill", user_id=admin_user.id, detail={"name": name, "kind": "platform"})
    return {"ok": True, "name": name, "kind": "platform"}


class SkillEditRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    content: str | None = None


async def _upsert_skill_override(db: AsyncSession, name: str, **fields) -> SkillOverride:
    row = (
        await db.execute(select(SkillOverride).where(SkillOverride.skill_name == name))
    ).scalar_one_or_none()
    if row is None:
        row = SkillOverride(skill_name=name, **fields)
        db.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/skills/{name}")
async def admin_edit_skill(
    name: str,
    payload: SkillEditRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """编辑技能。平台技能更新 manifest 元信息；内置 catalog 技能因目录只读，改为写覆盖（含可选 content）。"""
    catalog = _load_catalog()
    if name in (catalog.get("skills") or {}):
        row = await _upsert_skill_override(
            db,
            name,
            title=payload.title,
            description=payload.description,
            category=payload.category,
            content=payload.content,
        )
        await write_audit_log(db, action="admin_edit_skill", user_id=admin_user.id, detail={"name": name, "kind": "builtin"})
        return {"ok": True, "name": name, "kind": "builtin", "overridden": True}
    # 平台技能
    try:
        set_platform_skill_meta(
            name,
            title=payload.title,
            description=payload.description,
            category=payload.category,
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        raise
    await write_audit_log(db, action="admin_edit_skill", user_id=admin_user.id, detail={"name": name, "kind": "platform"})
    return {"ok": True, "name": name, "kind": "platform"}

@router.put("/skills/{name}/toggle")
async def admin_toggle_skill(
    name: str,
    enabled: bool = True,
    admin_user: User = Depends(require_admin),
):
    set_platform_skill_enabled(name, enabled)
    return {"ok": True, "name": name, "enabled": enabled}

# ── Admin-installed skills (C 方案: 管理员代装指定用户 + 平台默认推送给所有用户) ──

# Host path to the built-in skills source. In deployment this should point at the
# directory mounted into containers as /opt/data/skills (e.g. the hermes-agent/skills
# repo). Configurable via env so the same code works across deployments.
BUILTIN_SKILLS_DIR = Path(os.getenv("BUILTIN_SKILLS_DIR", "/app/hermes_skills"))

class AdminInstallSkillRequest(BaseModel):
    name: str
    # "auto" tries the platform skill library first, then the built-in set.
    source: str = "auto"

def _resolve_skill_source(name: str) -> tuple[Path, str]:
    """Return (host_skill_dir, container_root) for a skill name.

    Platform skills live under PLATFORM_SKILLS_DIR and are installed into the
    container's per-container path; built-in skills live under BUILTIN_SKILLS_DIR
    and go into the shared /opt/data/skills path.
    """
    platform_dir = PLATFORM_SKILLS_DIR / name
    builtin_dir = BUILTIN_SKILLS_DIR / name
    if platform_dir.is_dir() and (platform_dir / "SKILL.md").exists():
        return platform_dir, "/opt/data/profiles/skills"
    if builtin_dir.is_dir() and (builtin_dir / "SKILL.md").exists():
        return builtin_dir, HERMES_SKILLS_ROOT
    if platform_dir.is_dir():
        return platform_dir, "/opt/data/profiles/skills"
    if builtin_dir.is_dir():
        return builtin_dir, HERMES_SKILLS_ROOT
    raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

def _read_skill_files(skill_dir: Path) -> dict[str, dict[str, bytes]]:
    files: dict[str, dict[str, bytes]] = {}
    for f in skill_dir.rglob("*"):
        if f.is_file():
            files[str(f.relative_to(skill_dir)).replace(os.sep, "/")] = f.read_bytes()
    return files

@router.post("/skills/{name}/push")
async def admin_push_skill_to_all_users(
    name: str,
    db: AsyncSession = Depends(get_db),
):
    """把某个平台/内置技能推送给所有现存用户的容器 (C 方案 B)."""
    skill_dir, container_root = _resolve_skill_source(name)
    files = _read_skill_files(skill_dir)
    result = await db.execute(select(User.id))
    user_ids = [row[0] for row in result.all()]
    pushed = 0
    failed: list[dict] = []
    for uid in user_ids:
        try:
            container = await ensure_running(db, uid)
            _put_skill_archive(
                container.docker_id,
                container_root,
                {name: files},
            )
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": uid, "error": str(exc)})
    return {"ok": True, "name": name, "total": len(user_ids), "pushed": pushed, "failed": failed}

# ── 内置 catalog 技能管理（仓库 + 按用户安装/卸载 + 默认安装开关） ──

CATALOG_PATH = BUILTIN_SKILLS_DIR / "catalog.json"

def _load_catalog() -> dict:
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"categories": [], "skills": {}}


async def _load_skill_overrides(db: AsyncSession) -> dict[str, SkillOverride]:
    rows = (await db.execute(select(SkillOverride))).scalars().all()
    return {r.skill_name: r for r in rows}


def _merge_catalog_with_overrides(catalog: dict, overrides: dict[str, SkillOverride]) -> dict:
    """Apply admin SkillOverride rows onto the raw built-in catalog.

    Edits merge metadata; a row with deleted=True hides the skill. Returns a
    new catalog dict with an extra per-skill ``overridden`` flag.
    """
    skills: dict[str, dict] = {}
    for name, meta in (catalog.get("skills") or {}).items():
        meta = dict(meta or {})
        ov = overrides.get(name)
        if ov is not None and ov.deleted:
            continue
        if ov is not None:
            if ov.title is not None:
                meta["title"] = ov.title
            if ov.description is not None:
                meta["description"] = ov.description
            if ov.category is not None:
                meta["category"] = ov.category
            meta["overridden"] = True
        else:
            meta["overridden"] = False
        skills[name] = meta
    return {"categories": catalog.get("categories", []), "skills": skills}


@router.get("/skills/catalog")
async def admin_catalog_skills(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """返回全部 catalog 技能及分类（已合并管理员覆盖），供管理端「技能管理」展示。"""
    catalog = _load_catalog()
    overrides = await _load_skill_overrides(db)
    return _merge_catalog_with_overrides(catalog, overrides)

DEFAULT_OVERRIDE_FLAG = "default_install_overrides"


@router.get("/skills/default-install")
async def get_default_install(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """返回「新用户默认安装全部内置技能」开关（默认开）+ 单技能覆盖。"""
    flag = (
        await db.execute(
            select(SystemFlag).where(SystemFlag.key == DEFAULT_INSTALL_FLAG)
        )
    ).scalar_one_or_none()
    override_row = (
        await db.execute(
            select(SystemFlag).where(SystemFlag.key == DEFAULT_OVERRIDE_FLAG)
        )
    ).scalar_one_or_none()
    overrides = {}
    if override_row is not None and override_row.value:
        try:
            overrides = {k: bool(v) for k, v in json.loads(override_row.value).items()}
        except Exception:
            pass
    return {"enabled": flag is None or flag.value != "false", "overrides": overrides}

@router.put("/skills/default-install")
async def set_default_install(
    payload: dict,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置「新用户默认安装全部内置技能」开关。"""
    enabled = bool(payload.get("enabled", True))
    value = "true" if enabled else "false"
    await db.execute(
        pg_insert(SystemFlag)
        .values(key=DEFAULT_INSTALL_FLAG, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    await db.commit()
    return {"enabled": enabled}


@router.put("/skills/{name}/default-install")
async def set_skill_default_install(
    name: str,
    payload: dict,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置/清除单技能「默认安装（新用户）」覆盖，优先级高于总开关。

    payload.enabled: true=强制默认安装, false=强制默认不安装, null=清除覆盖(跟随总开关)。
    """
    enabled = payload.get("enabled", None)
    override_row = (
        await db.execute(
            select(SystemFlag).where(SystemFlag.key == DEFAULT_OVERRIDE_FLAG)
        )
    ).scalar_one_or_none()
    overrides = {}
    if override_row is not None and override_row.value:
        try:
            overrides = {k: bool(v) for k, v in json.loads(override_row.value).items()}
        except Exception:
            overrides = {}
    if enabled is None:
        overrides.pop(name, None)
    else:
        overrides[name] = bool(enabled)
    value = json.dumps(overrides, ensure_ascii=False)
    await db.execute(
        pg_insert(SystemFlag)
        .values(key=DEFAULT_OVERRIDE_FLAG, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    await db.commit()
    return {"ok": True, "name": name, "enabled": enabled, "overrides": overrides}


@router.get("/users/{user_id}/skills")
async def admin_user_skills(
    user_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """返回某用户已安装技能，区分 user（自装，可卸载）与 managed（系统装，不可卸载）。"""
    container = await ensure_running(db, user_id)
    marker = read_installed_marker(container.docker_id)
    return {"user": sorted(marker["user"]), "managed": sorted(marker["managed"])}

@router.post("/users/{user_id}/skills/install")
async def admin_install_managed_skill(
    user_id: str,
    req: AdminInstallSkillRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员把技能安装给指定用户（系统级，用户不可卸载）。

    内置 catalog 技能物理已存在于容器，拷贝幂等；非内置技能（平台/自定义）
    会先物理拷贝再标记。标记写入 managed 集合，用户端不显示卸载入口。
    """
    skill_dir, container_root = _resolve_skill_source(req.name)
    container = await ensure_running(db, user_id)
    _put_skill_archive(
        container.docker_id,
        container_root,
        {req.name: _read_skill_files(skill_dir)},
    )
    marker = read_installed_marker(container.docker_id)
    marker["managed"].add(req.name)
    marker["user"].discard(req.name)
    result = write_installed_marker(container.docker_id, marker["user"], marker["managed"])
    return {"ok": True, "user_id": user_id, "name": req.name, **result}

@router.delete("/users/{user_id}/skills/{name:path}")
async def admin_uninstall_user_skill(
    user_id: str,
    name: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员卸载某用户的技能：移除 user/managed 标记；若该技能不在 catalog
    （用户自装）则一并物理删除文件。内置 catalog 技能只去标记，不删文件。"""
    container = await ensure_running(db, user_id)
    marker = read_installed_marker(container.docker_id)
    marker["user"].discard(name)
    marker["managed"].discard(name)
    result = write_installed_marker(container.docker_id, marker["user"], marker["managed"])
    catalog = _load_catalog()
    if name not in catalog.get("skills", {}):
        try:
            delete_skill_from_hermes_container(container.docker_id, name)
        except Exception:
            # 物理删除失败不阻断去标记
            pass
    return {"ok": True, "user_id": user_id, "name": name, **result}


# ─── Platform MCP connectors (admin-managed) ────────────────────────────────

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_VALID_TRANSPORTS = {"stdio", "streamable_http", "sse"}
_VALID_CRED = {"none", "shared", "api_key", "oauth", "cli"}


class ConnectorCreate(BaseModel):
    name: str
    display_name: str
    description: str = ""
    icon: str | None = None
    transport: str = "streamable_http"
    config_json: dict = {}
    cli_config_json: dict = {}
    credential_strategy: str = "none"
    examples: str | None = None
    shared_credential: str | None = None
    is_default: bool = False
    is_mandatory: bool = False
    status: str = "active"


class ConnectorUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    icon: str | None = None
    transport: str | None = None
    config_json: dict | None = None
    cli_config_json: dict | None = None
    credential_strategy: str | None = None
    examples: str | None = None
    shared_credential: str | None = None
    is_default: bool | None = None
    is_mandatory: bool | None = None
    status: str | None = None


def _connector_to_dict(c: McpConnector) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "display_name": c.display_name,
        "description": c.description,
        "icon": c.icon,
        "transport": c.transport,
        "config_json": c.config_json,
        "cli_config_json": c.cli_config_json,
        "credential_strategy": c.credential_strategy,
        "examples": c.examples,
        "has_shared_credential": bool(c.shared_credential),
        "is_builtin": c.is_builtin,
        "is_default": c.is_default,
        "is_mandatory": c.is_mandatory,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


async def _get_connector_or_404(db: AsyncSession, connector_id: str) -> McpConnector:
    c = await db.get(McpConnector, connector_id)
    if c is None:
        raise HTTPException(status_code=404, detail="connector not found")
    return c


def _validate_connector_fields(name: str, transport: str, credential_strategy: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="invalid connector name (lowercase letters, digits, _ or -; 1-128 chars)",
        )
    if transport not in _VALID_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid transport (one of {sorted(_VALID_TRANSPORTS)})",
        )
    if credential_strategy not in _VALID_CRED:
        raise HTTPException(
            status_code=400,
            detail=f"invalid credential_strategy (one of {sorted(_VALID_CRED)})",
        )


@router.get("/connectors")
async def admin_list_connectors(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(McpConnector).order_by(McpConnector.display_name))
    return {"connectors": [_connector_to_dict(c) for c in result.scalars().all()]}


@router.post("/connectors")
async def admin_create_connector(
    payload: ConnectorCreate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    _validate_connector_fields(payload.name, payload.transport, payload.credential_strategy)
    dup = (
        await db.execute(select(McpConnector).where(McpConnector.name == payload.name))
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"connector name '{payload.name}' already exists")
    obj = McpConnector(**payload.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return {"ok": True, "connector": _connector_to_dict(obj)}


@router.get("/connectors/{connector_id}")
async def admin_get_connector(
    connector_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return {"connector": _connector_to_dict(await _get_connector_or_404(db, connector_id))}


@router.put("/connectors/{connector_id}")
async def admin_update_connector(
    connector_id: str,
    payload: ConnectorUpdate,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    c = await _get_connector_or_404(db, connector_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        _validate_connector_fields(
            data["name"],
            data.get("transport", c.transport),
            data.get("credential_strategy", c.credential_strategy),
        )
        if data["name"] != c.name:
            dup = (
                await db.execute(select(McpConnector).where(McpConnector.name == data["name"]))
            ).scalar_one_or_none()
            if dup is not None:
                raise HTTPException(status_code=409, detail="connector name already exists")
    for field, val in data.items():
        setattr(c, field, val)
    if c.is_default or c.is_mandatory:
        missing = _missing_required_config_fields(c.transport, c.config_json)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"cannot enable connector: config missing required field(s) for transport '{c.transport}': {missing}",
            )
    c.updated_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "connector": _connector_to_dict(c)}


@router.delete("/connectors/{connector_id}")
async def admin_delete_connector(
    connector_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    c = await _get_connector_or_404(db, connector_id)
    await db.delete(c)
    await db.commit()
    return {"ok": True}


@router.put("/connectors/{connector_id}/default")
async def admin_set_connector_default(
    connector_id: str,
    payload: dict,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    c = await _get_connector_or_404(db, connector_id)
    new_default = bool(payload.get("enabled", not c.is_default))
    if new_default:
        missing = _missing_required_config_fields(c.transport, c.config_json)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"cannot mark connector default: config missing required field(s) for transport '{c.transport}': {missing}",
            )
    c.is_default = new_default
    c.updated_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "is_default": c.is_default}


@router.put("/connectors/{connector_id}/mandatory")
async def admin_set_connector_mandatory(
    connector_id: str,
    payload: dict,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    c = await _get_connector_or_404(db, connector_id)
    new_mandatory = bool(payload.get("enabled", not c.is_mandatory))
    if new_mandatory:
        missing = _missing_required_config_fields(c.transport, c.config_json)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"cannot mark connector mandatory: config missing required field(s) for transport '{c.transport}': {missing}",
            )
    c.is_mandatory = new_mandatory
    c.updated_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "is_mandatory": c.is_mandatory}


@router.post("/connectors/{connector_id}/push")
async def admin_push_connector_to_all_users(
    connector_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """立即把该连接器（按各用户启用状态）写入所有在线用户的容器 config.yaml。"""
    c = await _get_connector_or_404(db, connector_id)
    result = await db.execute(select(User.id))
    user_ids = [row[0] for row in result.all()]
    pushed = 0
    failed: list[dict] = []
    for uid in user_ids:
        try:
            container = await ensure_running(db, uid)
            await _apply_mcp_servers_to_container(container, db, uid)
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": uid, "error": str(exc)})
    return {"ok": True, "name": c.name, "total": len(user_ids), "pushed": pushed, "failed": failed}



# ─── Admin: install connector for a specific user ───────────────────────────

@router.post("/users/{user_id}/connectors/{connector_id}/install")
async def admin_install_connector_for_user(
    user_id: str,
    connector_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员把某个连接器代装（启用）到指定用户的容器。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    c = await _get_connector_or_404(db, connector_id)
    row = (
        await db.execute(
            select(UserMcpConnector).where(
                UserMcpConnector.user_id == user.id,
                UserMcpConnector.name == c.name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = UserMcpConnector(
            user_id=user.id,
            connector_id=c.id,
            name=c.name,
            enabled=True,
        )
        db.add(row)
    row.enabled = True
    await db.commit()
    try:
        container = await ensure_running(db, user.id)
        await _apply_mcp_servers_to_container(container, db, user.id)
    except Exception:  # noqa: BLE001
        logger.exception("failed to apply connector %s for user %s", c.name, user.id)
    await write_audit_log(
        db, action="admin_install_connector", user_id=admin_user.id,
        detail={"user_id": user_id, "connector_id": connector_id, "name": c.name},
    )
    return {"ok": True, "name": c.name, "user_id": user_id}


@router.delete("/users/{user_id}/connectors/{row_id}")
async def admin_uninstall_connector_for_user(
    user_id: str,
    row_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """从指定用户移除（卸载）某个已装连接器。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    row = (
        await db.execute(
            select(UserMcpConnector).where(
                UserMcpConnector.id == row_id,
                UserMcpConnector.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="connector not found for user")
    name = row.name
    await db.delete(row)
    await db.commit()
    try:
        container = await ensure_running(db, user.id)
        await _apply_mcp_servers_to_container(container, db, user.id)
    except Exception:  # noqa: BLE001
        logger.exception("failed to re-apply MCP servers after removing %s for %s", name, user.id)
    await write_audit_log(
        db, action="admin_uninstall_connector", user_id=admin_user.id,
        detail={"user_id": user_id, "row_id": row_id, "name": name},
    )
    return {"ok": True, "name": name, "user_id": user_id}


# ─── Admin: agent (专家) template management ───────────────────────────────

class AgentTemplateRequest(BaseModel):
    agent_id: str  # 技术标识（小写字母/数字/-/_，唯一，不可与内置 agent 重名）
    name: str  # 显示名称（对外称呼）
    description: str | None = ""
    avatar: str | None = None
    system_prompt: str | None = ""
    is_default: bool = False
    is_enabled: bool = True


class AgentTemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    avatar: str | None = None
    system_prompt: str | None = None
    is_default: bool | None = None
    is_enabled: bool | None = None


def _agent_response(agent_id: str, name: str, *, description: str = "", avatar: str | None = None,
                    system_prompt: str = "", builtin: bool = False, readonly: bool = False,
                    is_default: bool = False, is_enabled: bool = True) -> dict:
    return {
        "id": agent_id,
        "agent_id": agent_id,
        "name": name,
        "description": description,
        "avatar": avatar,
        "system_prompt": system_prompt,
        "builtin": builtin,
        "readonly": readonly,
        "system": builtin,
        "is_default": is_default,
        "is_enabled": is_enabled,
    }


@router.get("/agents")
async def admin_list_agents(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出平台专家模板：内置系统 agent（只读）+ 管理员自建模板。"""
    items = [
        _agent_response(aid, aid, builtin=True, readonly=True, is_default=(aid == "main"))
        for aid in sorted(SYSTEM_AGENT_IDS)
    ]
    result = await db.execute(select(AgentTemplate).order_by(AgentTemplate.created_at))
    for t in result.scalars().all():
        items.append(_agent_response(
            t.agent_id, t.name, description=t.description, avatar=t.avatar,
            system_prompt=t.system_prompt, builtin=False, readonly=False,
            is_default=t.is_default, is_enabled=t.is_enabled,
        ))
    return {"agents": items}


@router.post("/agents")
async def admin_create_agent(
    body: AgentTemplateRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    agent_id = _safe_agent_id(body.agent_id)
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent id 非法")
    if agent_id in SYSTEM_AGENT_IDS:
        raise HTTPException(status_code=400, detail="不能覆盖系统内置 agent")
    exists = (
        await db.execute(select(AgentTemplate).where(AgentTemplate.agent_id == agent_id))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="agent 已存在")
    t = AgentTemplate(
        agent_id=agent_id,
        name=(body.name or "").strip(),
        description=(body.description or "").strip(),
        avatar=(body.avatar or "").strip() or None,
        system_prompt=(body.system_prompt or "").strip(),
        is_default=body.is_default,
        is_enabled=body.is_enabled,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    await write_audit_log(db, action="admin_create_agent", user_id=admin_user.id, detail={"agent_id": agent_id})
    return {"ok": True, **_agent_response(
        t.agent_id, t.name, description=t.description, avatar=t.avatar,
        system_prompt=t.system_prompt, is_default=t.is_default, is_enabled=t.is_enabled,
    )}


@router.put("/agents/{agent_id}")
async def admin_update_agent(
    agent_id: str,
    body: AgentTemplateUpdateRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    t = (
        await db.execute(select(AgentTemplate).where(AgentTemplate.agent_id == agent_id))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if body.name is not None:
        t.name = body.name.strip()
    if body.description is not None:
        t.description = body.description.strip()
    if body.avatar is not None:
        t.avatar = body.avatar.strip() or None
    if body.system_prompt is not None:
        t.system_prompt = body.system_prompt.strip()
    if body.is_default is not None:
        t.is_default = body.is_default
    if body.is_enabled is not None:
        t.is_enabled = body.is_enabled
    t.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(t)
    await write_audit_log(db, action="admin_update_agent", user_id=admin_user.id, detail={"agent_id": agent_id})
    return {"ok": True, **_agent_response(
        t.agent_id, t.name, description=t.description, avatar=t.avatar,
        system_prompt=t.system_prompt, is_default=t.is_default, is_enabled=t.is_enabled,
    )}


@router.delete("/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    t = (
        await db.execute(select(AgentTemplate).where(AgentTemplate.agent_id == agent_id))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="agent not found")
    await db.delete(t)
    await db.commit()
    await write_audit_log(db, action="admin_delete_agent", user_id=admin_user.id, detail={"agent_id": agent_id})
    return {"ok": True}


async def _assign_agent_to_user_container(
    db: AsyncSession, user_id: str, *, agent_id: str, name: str,
    description: str = "", avatar: str | None = None, system_prompt: str = "",
) -> dict:
    container = await ensure_running(db, user_id)
    try:
        result = await asyncio.to_thread(
            create_agent_profile_in_hermes_container,
            container.docker_id, agent_id,
            display_name=name, description=description, avatar=avatar, system_prompt=system_prompt,
        )
        return {"user_id": user_id, "agent_id": agent_id, "status": "created", **result}
    except HTTPException as exc:  # already exists
        if exc.status_code == 409:
            return {"user_id": user_id, "agent_id": agent_id, "status": "exists"}
        raise


@router.post("/agents/{agent_id}/push")
async def admin_push_agent_to_all_users(
    agent_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """把专家模板（或系统 agent）推送给所有现存用户。"""
    name = agent_id
    description = ""
    avatar = None
    system_prompt = ""
    if agent_id not in SYSTEM_AGENT_IDS:
        t = (
            await db.execute(select(AgentTemplate).where(AgentTemplate.agent_id == agent_id))
        ).scalar_one_or_none()
        if t is None:
            raise HTTPException(status_code=404, detail="agent not found")
        name, description, avatar, system_prompt = t.name, t.description, t.avatar, t.system_prompt
    result = await db.execute(select(User.id))
    user_ids = [row[0] for row in result.all()]
    ok = 0
    failed: list[dict] = []
    for uid in user_ids:
        try:
            await _assign_agent_to_user_container(
                db, uid, agent_id=agent_id, name=name, description=description,
                avatar=avatar, system_prompt=system_prompt,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": uid, "error": str(exc)})
    await write_audit_log(db, action="admin_push_agent", user_id=admin_user.id, detail={"agent_id": agent_id})
    return {"ok": True, "agent_id": agent_id, "total": len(user_ids), "pushed": ok, "failed": failed}


# ─── P1: 用户级 list/detail + 指定用户推送 ────────────────────────────────

@router.get("/users/{user_id}/connectors")
async def admin_user_connectors(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """列出某用户已启用/已装的 MCP 连接器（含个人自定义）。"""
    res = await db.execute(
        select(UserMcpConnector).where(UserMcpConnector.user_id == user_id)
    )
    rows = res.scalars().all()
    out = []
    for r in rows:
        name = r.name
        if r.connector_id:
            mc = await db.get(McpConnector, r.connector_id)
            if mc is not None:
                name = mc.name
        out.append({
            "id": r.id,
            "name": name,
            "connector_id": r.connector_id,
            "enabled": r.enabled,
            "personal": r.connector_id is None,
            "has_credential": bool(r.credential_json),
        })
    return out


@router.get("/users/{user_id}/agents")
async def admin_user_agents(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """列出某用户已分配的专家（容器 profiles 目录，main 记为系统默认）。"""
    container = await get_container(db, user_id)
    if container is None or not container.docker_id:
        return []
    try:
        c = _docker().containers.get(container.docker_id)
        out = c.exec_run(["sh", "-c", "ls -1 /opt/data/profiles 2>/dev/null"])
        names = (out.output or b"").decode().split()
        return [{"agent_id": n, "system": n in SYSTEM_AGENT_IDS} for n in names if n]
    except Exception:  # noqa: BLE001
        return []


@router.get("/users/{user_id}/container")
async def admin_user_container_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """某用户容器详情：状态/镜像/挂载/资源/创建时间/配置键。"""
    record = await get_container(db, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="container not found")
    detail = {
        "user_id": user_id,
        "status": record.status,
        "docker_id": record.docker_id,
        "internal_host": record.internal_host,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "user_config_keys": list((record.user_config or {}).keys()),
    }
    if record.docker_id:
        try:
            c = _docker().containers.get(record.docker_id)
            attrs = c.attrs
            detail["image"] = attrs.get("Config", {}).get("Image")
            detail["state"] = attrs.get("State", {}).get("Status")
            detail["mounts"] = [m.get("Name") or m.get("Source") for m in attrs.get("Mounts", [])]
            detail["created"] = attrs.get("Created")
        except Exception:  # noqa: BLE001
            pass
    return detail


class PushUsersRequest(BaseModel):
    user_ids: list[str]


@router.post("/skills/{name}/push-users")
async def admin_push_skill_to_users(
    name: str,
    req: PushUsersRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """把技能推送给指定用户（而非全量）。"""
    skill_dir, container_root = _resolve_skill_source(name)
    files = _read_skill_files(skill_dir)
    pushed = 0
    failed: list[dict] = []
    for uid in req.user_ids:
        try:
            container = await ensure_running(db, uid)
            _put_skill_archive(container.docker_id, container_root, {name: files})
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": uid, "error": str(exc)})
    await write_audit_log(db, action="admin_push_skill_users", user_id=admin_user.id,
                          detail={"name": name, "count": len(req.user_ids)})
    await db.commit()
    return {"ok": True, "name": name, "total": len(req.user_ids), "pushed": pushed, "failed": failed}


@router.post("/connectors/{connector_id}/push-users")
async def admin_push_connector_to_users(
    connector_id: str,
    req: PushUsersRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """把连接器推送给指定用户（按各用户启用状态写入容器 config.yaml）。"""
    await _get_connector_or_404(db, connector_id)
    pushed = 0
    failed: list[dict] = []
    for uid in req.user_ids:
        try:
            container = await ensure_running(db, uid)
            await _apply_mcp_servers_to_container(container, db, uid)
            pushed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": uid, "error": str(exc)})
    await write_audit_log(db, action="admin_push_connector_users", user_id=admin_user.id,
                          detail={"connector_id": connector_id, "count": len(req.user_ids)})
    await db.commit()
    return {"ok": True, "connector_id": connector_id, "total": len(req.user_ids), "pushed": pushed, "failed": failed}


@router.post("/agents/{agent_id}/push-users")
async def admin_push_agent_to_users(
    agent_id: str,
    req: PushUsersRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """把专家推送给指定用户。"""
    name = agent_id
    description = ""
    avatar = None
    system_prompt = ""
    if agent_id not in SYSTEM_AGENT_IDS:
        t = (
            await db.execute(select(AgentTemplate).where(AgentTemplate.agent_id == agent_id))
        ).scalar_one_or_none()
        if t is None:
            raise HTTPException(status_code=404, detail="agent not found")
        name, description, avatar, system_prompt = t.name, t.description, t.avatar, t.system_prompt
    ok = 0
    failed: list[dict] = []
    for uid in req.user_ids:
        try:
            await _assign_agent_to_user_container(
                db, uid, agent_id=agent_id, name=name, description=description,
                avatar=avatar, system_prompt=system_prompt,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"user_id": uid, "error": str(exc)})
    await write_audit_log(db, action="admin_push_agent_users", user_id=admin_user.id,
                          detail={"agent_id": agent_id, "count": len(req.user_ids)})
    await db.commit()
    return {"ok": True, "agent_id": agent_id, "total": len(req.user_ids), "pushed": ok, "failed": failed}


@router.post("/users/{user_id}/agents/{agent_id}/assign")
async def admin_assign_agent_to_user(
    user_id: str,
    agent_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """把专家模板（或系统 agent）指派给指定用户。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    name = agent_id
    description = ""
    avatar = None
    system_prompt = ""
    if agent_id not in SYSTEM_AGENT_IDS:
        t = (
            await db.execute(select(AgentTemplate).where(AgentTemplate.agent_id == agent_id))
        ).scalar_one_or_none()
        if t is None:
            raise HTTPException(status_code=404, detail="agent not found")
        name, description, avatar, system_prompt = t.name, t.description, t.avatar, t.system_prompt
    res = await _assign_agent_to_user_container(
        db, user_id, agent_id=agent_id, name=name, description=description,
        avatar=avatar, system_prompt=system_prompt,
    )
    await write_audit_log(db, action="admin_assign_agent", user_id=admin_user.id,
                          detail={"user_id": user_id, "agent_id": agent_id})
    return {"ok": True, **res}


@router.post("/users/{user_id}/agents/{agent_id}/unassign")
async def admin_unassign_agent_from_user(
    user_id: str,
    agent_id: str,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """从指定用户移除已分配的专家（删除其容器 profile 目录）。核心工作区 main 不可移除。"""
    if agent_id == "main":
        raise HTTPException(status_code=400, detail="核心工作区 main 不可移除")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    container = await get_container(db, user_id)
    if container is None or not container.docker_id:
        raise HTTPException(status_code=404, detail="container not found")
    try:
        await asyncio.to_thread(
            delete_agent_profile_from_hermes_container,
            container.docker_id, agent_id,
        )
    except HTTPException as exc:  # already gone
        if exc.status_code != 404:
            raise
    await write_audit_log(db, action="admin_unassign_agent", user_id=admin_user.id,
                          detail={"user_id": user_id, "agent_id": agent_id})
    return {"ok": True, "user_id": user_id, "agent_id": agent_id, "status": "removed"}


class ToggleProviderRequest(BaseModel):
    provider: str
    disabled: bool


@router.put("/users/{user_id}/models/provider")
async def admin_toggle_user_provider(
    user_id: str,
    req: ToggleProviderRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """禁用/启用某用户实际配置的模型提供商（写入 DB user_config，重建后持久生效）。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if not req.provider:
        raise HTTPException(status_code=400, detail="provider required")
    from sqlalchemy import select as _select
    from app.db.models import Container
    rec = (await db.execute(_select(Container).where(Container.user_id == user_id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="container not found")
    uc = dict(rec.user_config or {})
    disabled = list(uc.get("disabled_providers") or [])
    if req.disabled:
        if req.provider not in disabled:
            disabled.append(req.provider)
    else:
        disabled = [p for p in disabled if p != req.provider]
    uc["disabled_providers"] = disabled
    rec.user_config = uc
    await db.commit()
    # Hot-apply to the running container so the client (which reads config.yaml)
    # reflects the disabled state immediately. DB remains the source of truth
    # persisted across rebuilds (_apply_user_config re-applies it on rebuild).
    try:
        container_name = f"hermes-user-{user_id[:8]}"
        cfg = _read_container_config(container_name)
        cps = cfg.get("custom_providers") or []
        touched = False
        for p in cps:
            if isinstance(p, dict) and p.get("name") == req.provider:
                if req.disabled:
                    p["disabled"] = True
                else:
                    p.pop("disabled", None)
                touched = True
        if touched:
            _write_container_config(container_name, cfg)
    except Exception as e:  # noqa: BLE001
        logger.warning("hot-apply disabled provider %s failed for %s: %s", req.provider, user_id, e)
    return {"ok": True, "provider": req.provider, "disabled": req.disabled}


@router.get("/users/{user_id}/usage")
async def admin_user_usage(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    """某用户本周与累计 token 用量（来自 UsageRecord）。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    now = datetime.utcnow()
    week_start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total = (await db.execute(
        select(func.coalesce(func.sum(UsageRecord.total_tokens), 0)).where(UsageRecord.user_id == user_id)
    )).scalar_one()
    week = (await db.execute(
        select(func.coalesce(func.sum(UsageRecord.total_tokens), 0))
        .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= week_start)
    )).scalar_one()
    today = (await db.execute(
        select(func.coalesce(func.sum(UsageRecord.total_tokens), 0))
        .where(UsageRecord.user_id == user_id, UsageRecord.created_at >= today_start)
    )).scalar_one()

    return {
        "tokens_today": int(today),
        "tokens_this_week": int(week),
        "tokens_total": int(total),
    }
