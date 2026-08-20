"""Routes that drive the empty-product first-boot setup wizard.

These endpoints are intentionally OUTSIDE ``routes/admin.py`` because they
must keep functioning even while setup is incomplete — they are how the
operator finishes setup in the first place.

``require_setup_complete`` is a separate dependency applied to the main
``/api/admin`` router (and to any future admin-only router) so that
setup-incomplete installs can only hit the wizard endpoints + change
password + model config.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import decode_token, get_user_by_id
from app.db.engine import get_db
from app.db.models import ModelProviderConfig, User
from app.setup_state import (
    bootstrap_setup_flags,
    compute_setup_status,
    mark_setup_completed,
    mark_setup_dismissed,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])

_bearer = HTTPBearer(auto_error=False)


class SetupStatus(BaseModel):
    required_steps: list[str]
    recommended_steps: list[str]
    completed_steps: list[str]
    missing_steps: list[str]
    missing_recommended_steps: list[str]
    is_complete: bool
    is_dismissed: bool


def _status_from_dict(status_: dict) -> SetupStatus:
    """Map the compute_setup_status() dict onto the response schema."""
    return SetupStatus(
        required_steps=list(status_["required_steps"]),
        recommended_steps=list(status_["recommended_steps"]),
        completed_steps=list(status_["completed_steps"]),
        missing_steps=list(status_["missing_steps"]),
        missing_recommended_steps=list(status_["missing_recommended_steps"]),
        is_complete=status_["is_complete"],
        is_dismissed=status_["is_dismissed"],
    )


async def _current_user_or_none(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve the current user if a valid access token is present.

    The setup endpoint must work both for logged-in admins (who need to
    see their own status) and for the moment just-after-login when the
    client may not yet have stored the access token.
    """

    if credentials is None:
        return None
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        return None
    user = await get_user_by_id(db, payload["sub"])
    if user is None or not user.is_active:
        return None
    return user


@router.get("/status", response_model=SetupStatus)
async def setup_status(db: AsyncSession = Depends(get_db)) -> SetupStatus:
    """Return the current first-boot setup status.

    Public endpoint — no auth required. Used by the client UI on every page
    load to decide whether to redirect into the wizard.
    """

    # Bootstrap: if the operator pre-seeded the install via env vars, the
    # ``add_model_key`` step is already done. ``bootstrap_setup_flags`` is
    # idempotent and cheap (single SELECT), so calling it on every status
    # read is fine.
    has_key = (
        await db.execute(sql_func.count(ModelProviderConfig.id))
    ).scalar_one() or 0
    await bootstrap_setup_flags(db, has_any_model_key=bool(has_key))

    status_ = await compute_setup_status(db)
    return _status_from_dict(status_)


@router.post("/complete", response_model=SetupStatus)
async def setup_complete(
    payload: SetupCompleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(_current_user_or_none),
) -> SetupStatus:
    """Mark setup as completed.

    Only the REQUIRED steps (currently: change admin password) must be
    satisfied. Recommended steps like ``add_model_key`` may be left for
    later — they surface as in-app hints, not login blocks.
    """

    if not payload.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true is required to finish setup",
        )
    status_ = await compute_setup_status(db)
    if status_["missing_steps"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Setup is not complete. Missing steps: {status_['missing_steps']}",
        )
    await mark_setup_completed(db)
    logger.info("Setup completed by user_id=%s", getattr(user, "id", None))
    refreshed = await compute_setup_status(db)
    return _status_from_dict(refreshed)


@router.post("/dismiss", response_model=SetupStatus)
async def setup_dismiss(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(_current_user_or_none),
) -> SetupStatus:
    """Skip the wizard entirely (e.g. for scripted/air-gapped installs)."""

    await mark_setup_dismissed(db)
    logger.info("Setup wizard dismissed by user_id=%s", getattr(user, "id", None))
    refreshed = await compute_setup_status(db)
    return _status_from_dict(refreshed)


# Imported late to avoid a Pydantic forward-reference surprise with the
# payload schema above.
class SetupCompleteRequest(BaseModel):  # noqa: E305  (kept here for clarity)
    confirm: bool = True