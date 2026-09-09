"""API 网关管理接口，所有操作要求管理员身份。"""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit_log
from app.auth.dependencies import require_admin, require_setup_complete
from app.db.engine import get_db
from app.db.models import GatewayRequest, GatewayRoute, GatewayToken, ModelProviderConfig, User
from app.gateway.access import available_models, secret_hash
from app.gateway.protocol import PROTOCOLS, provider_protocols

router = APIRouter(
    prefix="/api/admin/gateway",
    tags=["gateway-admin"],
    dependencies=[Depends(require_admin), Depends(require_setup_complete)],
)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def token_json(token, username=None):
    return {
        **{
            key: getattr(token, key)
            for key in (
                "id",
                "name",
                "user_id",
                "token_hint",
                "allowed_models",
                "enabled",
                "retain_content",
                "retention_days",
            )
        },
        **{
            key: iso(getattr(token, key))
            for key in ("expires_at", "revoked_at", "created_at", "last_used_at")
        },
        "username": username,
    }


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    user_id: str
    allowed_models: list[str] = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None
    retain_content: bool = False
    retention_days: int = Field(default=7, ge=1, le=90)


class TokenUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_models: list[str] | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    expires_at: datetime | None = None
    retain_content: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=90)


async def validate_token(db, user_id, values):
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(400, "请选择有效用户")
    if "name" in values:
        if not values["name"] or not values["name"].strip():
            raise HTTPException(400, "令牌名称不能为空")
        values["name"] = values["name"].strip()
    if "allowed_models" in values:
        models = {m["id"] for m in await available_models(db, user_id)}
        if not values["allowed_models"] or not set(values["allowed_models"]).issubset(models):
            raise HTTPException(400, "白名单包含未配置、未启用或该用户无权使用的模型")
        values["allowed_models"] = list(dict.fromkeys(values["allowed_models"]))
    if values.get("expires_at"):
        date = values["expires_at"]
        if date.tzinfo:
            date = date.astimezone(timezone.utc).replace(tzinfo=None)
        if date <= datetime.utcnow():
            raise HTTPException(400, "有效期必须晚于当前时间")
        values["expires_at"] = date
    return user


@router.get("/options")
async def options(user_id: str | None = None, db: AsyncSession = Depends(get_db)):
    users = (
        await db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.username))
    ).all()
    return {
        "users": [{"id": u.id, "username": u.username} for u in users],
        "models": await available_models(db, user_id),
        "gateway_path": "/api/gateway/v1",
    }


@router.get("/tokens")
async def tokens(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(GatewayToken, User.username)
            .outerjoin(User, User.id == GatewayToken.user_id)
            .order_by(GatewayToken.created_at.desc())
        )
    ).all()
    return {"items": [token_json(t, name) for t, name in rows]}


@router.post("/tokens")
async def create_token(
    body: TokenCreate, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
):
    values = body.model_dump()
    user = await validate_token(db, body.user_id, values)
    secret = "cw_" + secrets.token_urlsafe(32)
    token = GatewayToken(
        **values, token_hash=secret_hash(secret), token_hint=secret[:7] + "…" + secret[-4:]
    )
    db.add(token)
    await db.flush()
    await write_audit_log(db, user_id=admin.id, action="gateway_token_create", resource=token.id)
    await db.commit()
    await db.refresh(token)
    return {"token": token_json(token, user.username), "secret": secret}


async def get_token(db, token_id):
    token = await db.get(GatewayToken, token_id)
    if token is None:
        raise HTTPException(404, "令牌不存在")
    return token


@router.patch("/tokens/{token_id}")
async def update_token(
    token_id: str,
    body: TokenUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    token = await get_token(db, token_id)
    if token.revoked_at:
        raise HTTPException(409, "已吊销的令牌不能修改或恢复")
    values = body.model_dump(exclude_unset=True)
    if any(v is None for k, v in values.items() if k != "expires_at"):
        raise HTTPException(400, "设置项不能为 null")
    user = await validate_token(db, token.user_id, values)
    for key, value in values.items():
        setattr(token, key, value)
    await write_audit_log(db, user_id=admin.id, action="gateway_token_update", resource=token.id)
    await db.commit()
    return token_json(token, user.username)


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(
    token_id: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
):
    token = await get_token(db, token_id)
    token.revoked_at = token.revoked_at or datetime.utcnow()
    token.enabled = False
    await write_audit_log(db, user_id=admin.id, action="gateway_token_revoke", resource=token.id)
    await db.commit()
    user = await db.get(User, token.user_id)
    return token_json(token, user.username if user else None)


class RouteUpdate(BaseModel):
    protocols: list[str] = Field(max_length=3)


@router.get("/routes")
async def routes(db: AsyncSession = Depends(get_db)):
    providers = (await db.scalars(select(ModelProviderConfig))).all()
    overrides = {r.provider_id: r for r in (await db.scalars(select(GatewayRoute))).all()}
    return {
        "items": [
            {
                "provider_id": p.id,
                "name": p.display_name,
                "protocols": provider_protocols(p, overrides.get(p.id)),
            }
            for p in providers
        ]
    }


@router.put("/routes/{provider_id}")
async def set_route(
    provider_id: str,
    body: RouteUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not set(body.protocols).issubset(PROTOCOLS):
        raise HTTPException(400, "不支持的接口类型")
    provider = await db.get(ModelProviderConfig, provider_id)
    if not provider:
        raise HTTPException(404, "供应商不存在")
    route = await db.get(GatewayRoute, provider_id)
    if not route:
        route = GatewayRoute(provider_id=provider_id)
        db.add(route)
    route.protocols = list(dict.fromkeys(body.protocols))
    await write_audit_log(db, user_id=admin.id, action="gateway_route_update", resource=provider_id)
    await db.commit()
    return {"provider_id": provider_id, "name": provider.display_name, "protocols": route.protocols}


def request_json(row, token_name, username):
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "token_id",
                "user_id",
                "model",
                "provider_id",
                "upstream_model",
                "protocol",
                "status_code",
                "outcome",
                "duration_ms",
                "first_byte_ms",
                "input_tokens",
                "output_tokens",
                "usage",
                "error_type",
            )
        },
        "started_at": iso(row.started_at),
        "token_name": token_name,
        "username": username,
    }


def request_select():
    # 列表不加载正文，减少数据库带宽和内存。
    from sqlalchemy.orm import defer

    return (
        select(GatewayRequest, GatewayToken.name, User.username)
        .options(
            defer(GatewayRequest.request_content),
            defer(GatewayRequest.response_content),
        )
        .outerjoin(GatewayToken, GatewayToken.id == GatewayRequest.token_id)
        .outerjoin(User, User.id == GatewayRequest.user_id)
    )


@router.get("/requests")
async def requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    token_id: str | None = None,
    user_id: str | None = None,
    model: str | None = None,
    protocol: str | None = None,
    status: str | None = None,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    filters = [GatewayRequest.started_at >= datetime.utcnow() - timedelta(days=days)]
    if model:
        filters.append(
            (GatewayRequest.model == model)
            | ((GatewayRequest.provider_id + "/" + GatewayRequest.upstream_model) == model)
        )
    for column, value in (
        (GatewayRequest.token_id, token_id),
        (GatewayRequest.user_id, user_id),
        (GatewayRequest.protocol, protocol),
        (GatewayRequest.outcome, status),
    ):
        if value:
            filters.append(column == value)
    total = await db.scalar(select(func.count()).select_from(GatewayRequest).where(*filters))
    rows = (
        await db.execute(
            request_select()
            .where(*filters)
            .order_by(GatewayRequest.started_at.desc(), GatewayRequest.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [request_json(*r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/requests/{request_id}")
async def request_detail(
    request_id: str, db: AsyncSession = Depends(get_db), admin: User = Depends(require_admin)
):
    found = (await db.execute(request_select().where(GatewayRequest.id == request_id))).first()
    if not found:
        raise HTTPException(404, "请求记录不存在")
    row, token_name, username = found
    data = request_json(row, token_name, username)
    state = row.content_state
    if row.content_expires_at and row.content_expires_at <= datetime.utcnow():
        state = "expired"
    data.update(content_state=state, request_content=None, response_content=None)
    if state in {"available", "truncated"}:
        await db.refresh(row, attribute_names=["request_content", "response_content"])
        data.update(request_content=row.request_content, response_content=row.response_content)
        await write_audit_log(db, user_id=admin.id, action="gateway_content_read", resource=row.id)
        await db.commit()
    return data


@router.get("/usage")
async def usage(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    token_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    r = GatewayRequest
    filters = [r.started_at >= datetime.utcnow() - timedelta(days=days)]
    if token_id:
        filters.append(r.token_id == token_id)
    count = func.count(r.id).label("requests")
    inputs = func.sum(r.input_tokens).label("input_tokens")
    outputs = func.sum(r.output_tokens).label("output_tokens")
    known = func.sum(
        case(((r.input_tokens.is_not(None)) & (r.output_tokens.is_not(None)), 1), else_=0)
    ).label("known_usage_requests")
    errors = func.sum(case((r.outcome != "success", 1), else_=0)).label("errors")
    summary = (
        (await db.execute(select(count, inputs, outputs, known, errors).where(*filters)))
        .mappings()
        .one()
    )
    result = dict(summary)
    result["errors"] = result["errors"] or 0
    result["known_usage_requests"] = result["known_usage_requests"] or 0
    day = func.date(r.started_at)
    daily = (
        (
            await db.execute(
                select(day.label("date"), count, inputs, outputs)
                .where(*filters)
                .group_by(day)
                .order_by(day)
            )
        )
        .mappings()
        .all()
    )
    result["daily"] = [dict(item, date=str(item["date"])) for item in daily]
    for key, group, label, table, on in (
        ("by_token", r.token_id, GatewayToken.name, GatewayToken, GatewayToken.id == r.token_id),
        ("by_user", r.user_id, User.username, User, User.id == r.user_id),
        ("by_model", r.model, r.model, None, None),
    ):
        query = select(label.label("name"), count, inputs, outputs).select_from(r)
        if table is not None:
            query = query.outerjoin(table, on)
        rows = (
            (
                await db.execute(
                    query.where(*filters).group_by(group, label).order_by(count.desc()).limit(20)
                )
            )
            .mappings()
            .all()
        )
        result[key] = [dict(item) for item in rows]
    result["log_queue_dropped"] = request.app.state.gateway.dropped
    return result
