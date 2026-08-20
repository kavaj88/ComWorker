"""FastAPI dependencies for authentication."""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import decode_token, get_user_by_id
from app.db.engine import get_db
from app.db.models import User

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the JWT from the Authorization header."""
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = await get_user_by_id(db, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the current user to have admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# ── First-boot setup guard ─────────────────────────────────────────────
# Admin-only routes are blocked while the platform has not finished its
# empty-product onboarding wizard, with a small allowlist for the routes
# the wizard itself needs.

_SETUP_WHITELIST_PREFIXES: tuple[str, ...] = (
    # The wizard's own endpoints — defined in routes/setup.py — already
    # live outside the /api/admin router so they don't need to be listed.
    # But the model-config CRUD is part of the wizard (step 2: "add at
    # least one LLM provider API key"), so let it through.
    "/api/admin/models",
)


async def require_setup_complete(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Block admin-only API access until first-boot setup is finished.

    Applied as a router-level dependency on ``routes/admin.py``. Exits
    early for any path under ``_SETUP_WHITELIST_PREFIXES`` so the wizard
    can complete its own work even while the rest of the admin UI is
    locked down.

    The platform itself stays up and serves non-admin traffic; only admin
    write paths are gated.
    """

    if any(request.url.path.startswith(p) for p in _SETUP_WHITELIST_PREFIXES):
        return

    from app.setup_state import compute_setup_status, is_setup_required

    status_ = await compute_setup_status(db)
    if is_setup_required(status_):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "setup_required",
                "message": "Platform setup is not complete. Visit /setup to finish onboarding.",
                "missing_steps": status_["missing_steps"],
            },
        )
