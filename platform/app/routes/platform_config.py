"""Platform branding configuration (display name + logo).

Stored in the ``system_flags`` key/value table so it survives container
rebuilds without touching the filesystem:

- ``platform.name`` → display name shown in the client sidebar (default "ComWorker")
- ``platform.logo`` → logo image as a data URL (``data:<mime>;base64,...``), or absent

Admin endpoints (require admin auth) manage the values; the client endpoint
(requires login) exposes them read-only to the CCA frontend.
"""

from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.db.engine import get_db
from app.db.models import SystemFlag

logger = logging.getLogger(__name__)

DEFAULT_PLATFORM_NAME = "ComWorker"
FLAG_NAME = "platform.name"
FLAG_LOGO = "platform.logo"

# Logo upload constraints
MAX_LOGO_BYTES = 512 * 1024  # 512 KB
ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml", "image/gif"}

admin_router = APIRouter(
    prefix="/api/admin/platform-config",
    tags=["platform-config"],
    dependencies=[Depends(require_admin)],
)
client_router = APIRouter(
    prefix="/api/comworker/platform-config",
    tags=["platform-config"],
)


# ---------------------------------------------------------------------------
# Storage helpers (SystemFlag key/value)
# ---------------------------------------------------------------------------

async def _get_flag(session: AsyncSession, key: str) -> str | None:
    row = await session.get(SystemFlag, key)
    return row.value if row else None


async def _set_flag(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(SystemFlag, key)
    if row is None:
        session.add(SystemFlag(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def _read_config(session: AsyncSession) -> dict:
    name = (await _get_flag(session, FLAG_NAME) or "").strip() or DEFAULT_PLATFORM_NAME
    logo = await _get_flag(session, FLAG_LOGO)
    return {"name": name, "logo": logo or None}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@admin_router.get("")
async def get_admin_config(session: AsyncSession = Depends(get_db)) -> dict:
    return await _read_config(session)


class UpdatePlatformConfigBody(BaseModel):
    name: str = DEFAULT_PLATFORM_NAME
    # logo: omitted / None → leave unchanged; "" → clear the custom logo
    logo: str | None = None


@admin_router.put("")
async def update_admin_config(
    body: UpdatePlatformConfigBody,
    session: AsyncSession = Depends(get_db),
) -> dict:
    name = body.name.strip() or DEFAULT_PLATFORM_NAME
    await _set_flag(session, FLAG_NAME, name)
    if body.logo is not None:
        if body.logo.strip():
            await _set_flag(session, FLAG_LOGO, body.logo.strip())
        else:
            # empty string → remove custom logo
            row = await session.get(SystemFlag, FLAG_LOGO)
            if row is not None:
                await session.delete(row)
                await session.commit()
    return {"ok": True, "name": name}


@admin_router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if file.content_type not in ALLOWED_LOGO_TYPES:
        raise HTTPException(
            status_code=400,
            detail="仅支持 PNG / JPEG / WebP / SVG / GIF 格式的图片",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传的图片为空")
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="Logo 图片不能超过 512KB")
    data_url = f"data:{file.content_type};base64,{base64.b64encode(data).decode('ascii')}"
    await _set_flag(session, FLAG_LOGO, data_url)
    return {"ok": True, "logo": data_url}


# ---------------------------------------------------------------------------
# Client (read-only, requires login)
# ---------------------------------------------------------------------------

@client_router.get("")
async def get_client_config(
    session: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
) -> dict:
    return await _read_config(session)
