"""令牌验证与严格的供应商模型路由。"""

import hashlib
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.db.models import GatewayRoute, GatewayToken, ModelProviderConfig, User
from app.gateway.protocol import provider_base, provider_protocols
from app.model_config import _provider_visible_to_user


def secret_hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


async def authenticate(db, headers):
    auth = headers.get("authorization", "")
    secret = (
        auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key", "")
    )
    if not secret or len(secret) > 256:
        raise HTTPException(401, "访问令牌无效")
    token = await db.scalar(
        select(GatewayToken).join(User, User.id == GatewayToken.user_id).where(
            GatewayToken.token_hash == secret_hash(secret), User.is_active.is_(True)
        )
    )
    if (
        not token
        or not token.enabled
        or token.revoked_at
        or (token.expires_at and token.expires_at <= datetime.utcnow())
    ):
        raise HTTPException(401, "访问令牌无效、已停用或已过期")
    return token


async def available_models(db, user_id=None):
    providers = (
        await db.scalars(select(ModelProviderConfig).where(ModelProviderConfig.enabled.is_(True)))
    ).all()
    overrides = {r.provider_id: r for r in (await db.scalars(select(GatewayRoute))).all()}
    result = []
    for provider in providers:
        if (
            not provider_base(provider)
            or not provider.api_key
            or (user_id and not _provider_visible_to_user(provider, user_id))
        ):
            continue
        for model in provider.models or []:
            if isinstance(model, dict) and model.get("id") and model.get("enabled") is not False:
                result.append(
                    {
                        "id": f"{provider.id}/{model['id']}",
                        "name": model.get("name") or model["id"],
                        "provider_id": provider.id,
                        "upstream_model": model["id"],
                        "protocols": provider_protocols(provider, overrides.get(provider.id)),
                    }
                )
    return result


async def resolve_route(db, token, model, protocol):
    # 只查询本次可能命中的供应商，不扫描全平台或解密其他供应商密钥。
    refs = [ref for ref in token.allowed_models if "/" in ref and (ref == model or ref.split("/", 1)[1] == model)]
    if model in refs:
        refs = [model]
    if not refs:
        raise HTTPException(403, "模型未授权或已不可用")
    rows = (await db.execute(select(ModelProviderConfig, GatewayRoute).outerjoin(
        GatewayRoute, GatewayRoute.provider_id == ModelProviderConfig.id
    ).where(ModelProviderConfig.id.in_({ref.split("/", 1)[0] for ref in refs}), ModelProviderConfig.enabled.is_(True)))).all()
    matches = []
    for provider, override in rows:
        if not provider.api_key or not provider_base(provider) or not _provider_visible_to_user(provider, token.user_id):
            continue
        for item in provider.models or []:
            if isinstance(item, dict) and item.get("enabled") is not False and f"{provider.id}/{item.get('id')}" in refs:
                matches.append((provider, item["id"], provider_protocols(provider, override)))
    if not matches:
        raise HTTPException(403, "模型未授权或已不可用")
    if len(matches) != 1:
        raise HTTPException(400, "模型名称对应多个供应商，请使用 provider/model 完整名称")
    provider, upstream_model, protocols = matches[0]
    if protocol not in protocols:
        raise HTTPException(400, "该供应商尚未声明支持此接口，请在 API 网关中配置协议能力")
    return provider, upstream_model
