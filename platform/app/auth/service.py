"""Authentication service: password hashing, JWT tokens, user CRUD."""

from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {"sub": user_id, "role": role, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_api_token(user_id: str, role: str, expire_days: int = 365) -> str:
    """Create a long-lived API token for programmatic access."""
    expire = datetime.now(timezone.utc) + timedelta(days=expire_days)
    payload = {"sub": user_id, "role": role, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT. Returns payload dict or None."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    username: str,
    email: str,
    password: str,
    *,
    runtime_mode: str = "dedicated",
    role: str = "user",
    quota_tier: str = "free",
    must_change_password: bool = False,
) -> User:
    """Single source of truth for creating a user account.

    Used by both client self-registration and admin user creation so that the
    two paths stay behaviourally identical (same table, hashing, defaults).
    """
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        runtime_mode=runtime_mode,
        role=role,
        quota_tier=quota_tier,
        must_change_password=must_change_password,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user_by_sso_uid(db: AsyncSession, sso_uid: str) -> User | None:
    result = await db.execute(select(User).where(User.sso_uid == sso_uid))
    return result.scalar_one_or_none()


async def create_or_update_sso_user(
    db: AsyncSession,
    sso_uid: str,
    sso_token: str,
    display_name: str = "",
    *,
    runtime_mode_for_new_user: str = "dedicated",
) -> User:
    """Create or update a user from InfoX-Med SSO login.

    Only stores sso_uid (for mapping) and sso_token (for container injection).
    """
    user = await get_user_by_sso_uid(db, sso_uid)
    if user is not None:
        # Update token on each login
        user.sso_token = sso_token
        await db.commit()
        await db.refresh(user)
        return user

    # Create new user — use display_name or uid as username
    import secrets
    username = display_name or f"infox_{sso_uid}"
    # Ensure unique username
    existing = await get_user_by_username(db, username)
    if existing:
        username = f"{username}_{sso_uid}"

    random_pw = secrets.token_urlsafe(24)
    user = User(
        username=username,
        email=f"{sso_uid}@infox-med.sso",
        password_hash=hash_password(random_pw),
        sso_uid=sso_uid,
        sso_token=sso_token,
        runtime_mode=runtime_mode_for_new_user,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


class AuthFailureReason:
    USER_NOT_FOUND = "user_not_found"
    ACCOUNT_DISABLED = "account_disabled"
    PASSWORD_INCORRECT = "password_incorrect"


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Verify credentials. Returns User on success, None on failure."""
    user = await get_user_by_username(db, username)
    if user is None:
        # Also try email as login identifier
        user = await get_user_by_email(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def authenticate_user_with_reason(
    db: AsyncSession, username: str, password: str
) -> tuple[User | None, str | None]:
    """Verify credentials and return a concrete failure reason when rejected."""
    user = await get_user_by_username(db, username)
    if user is None:
        # Also try email as login identifier
        user = await get_user_by_email(db, username)
    if user is None:
        return None, AuthFailureReason.USER_NOT_FOUND
    if not user.is_active:
        return None, AuthFailureReason.ACCOUNT_DISABLED
    if not verify_password(password, user.password_hash):
        return None, AuthFailureReason.PASSWORD_INCORRECT
    return user, None
