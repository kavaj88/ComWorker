"""Platform Gateway — main FastAPI application."""

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlparse, urlunparse

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api_compat import comworker_compat
from app.audit_middleware import AuditContextMiddleware
from app.config import settings
from app.db.engine import engine
from app.db.models import Base
from app.logging_setup import log_settings_summary, setup_logging
from app.routes import admin, auth, connectors, llm, platform_config, proxy, models, setup
from app.runtime_router import close_runtime_backends

setup_logging()
logger = logging.getLogger(__name__)


def _bootstrap_runtime_secrets() -> None:
    """Resolve secrets that must be present before the app starts serving
    traffic. If a secret is missing we generate a strong random value and
    optionally persist it to a data-volume file so subsequent restarts
    remain stable.

    Mutates ``settings`` in place via ``object.__setattr__`` because
    pydantic-settings defaults to frozen models.
    """

    from app.security import is_secret_weak, load_or_generate_secret

    if is_secret_weak(settings.jwt_secret):
        generated = load_or_generate_secret(
            name="jwt_secret",
            env_var="PLATFORM_JWT_SECRET",
            persist_path=os.environ.get("PLATFORM_JWT_SECRET_FILE", "/data/.jwt_secret"),
        )
        object.__setattr__(settings, "jwt_secret", generated)

    if is_secret_weak(settings.model_keys_master_key):
        generated = load_or_generate_secret(
            name="model_keys_master_key",
            env_var="PLATFORM_MODEL_KEYS_MASTER_KEY",
            persist_path=os.environ.get(
                "PLATFORM_MODEL_KEYS_MASTER_KEY_FILE", "/data/.model_keys_master_key"
            ),
        )
        object.__setattr__(settings, "model_keys_master_key", generated)


_bootstrap_runtime_secrets()


async def _ensure_database() -> None:
    """Connect to the default 'postgres' DB and create the target database if missing."""
    parsed = urlparse(settings.database_url)
    db_name = parsed.path.lstrip("/")
    # Build a URL pointing to the default 'postgres' database
    admin_url = urlunparse(parsed._replace(path="/postgres"))
    # asyncpg uses postgresql:// not postgresql+asyncpg://
    admin_url = admin_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    max_retries = 30
    for attempt in range(1, max_retries + 1):
        try:
            conn = await asyncpg.connect(admin_url)
            break
        except (OSError, asyncpg.PostgresError) as exc:
            if attempt == max_retries:
                raise RuntimeError(
                    f"Cannot connect to PostgreSQL after {max_retries} attempts"
                ) from exc
            logger.warning("Waiting for PostgreSQL (attempt %d/%d): %s", attempt, max_retries, exc)
            await asyncio.sleep(2)

    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if not exists:
            # CREATE DATABASE cannot run inside a transaction
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            logger.info("Created database '%s'", db_name)
        else:
            logger.info("Database '%s' already exists", db_name)
    finally:
        await conn.close()


# Passwords considered insecure for an admin account. If one of these ends up
# configured, we warn loudly instead of failing silently.
_WEAK_ADMIN_PASSWORDS = {"", "changeme", "change-me", "change-me-in-production",
                         "admin", "password", "123456", "admin123"}


async def _ensure_admin_user() -> None:
    """Guarantee at least one usable super-admin exists after first boot.

    Product rule: the platform must NEVER finish initialization with zero
    administrators. Behavior:
      * Username defaults to "admin" when not configured.
      * If no ADMIN_PASSWORD is provided, a cryptographically secure random
        password is generated, the account is flagged must_change_password, and
        the credentials are logged prominently so the operator can log in and
        rotate them immediately.
      * If a weak/default password is configured, a security warning is logged.
    This never silently no-ops.
    """
    username = settings.admin_username or "admin"
    password = settings.admin_password

    from app.auth.service import get_user_by_username, hash_password
    from app.db.engine import async_session
    from app.db.models import User

    async with async_session() as db:
        existing = await get_user_by_username(db, username)
        if existing:
            # Ensure the user has admin role
            if existing.role != "admin":
                existing.role = "admin"
                await db.commit()
                logger.info("Updated user '%s' role to admin", username)
            else:
                logger.info("Admin user '%s' already exists", username)
            return

        auto_generated = False
        if not password:
            password = secrets.token_urlsafe(16)
            auto_generated = True

        user = User(
            username=username,
            email=f"{username}@localhost",
            password_hash=hash_password(password),
            role="admin",
            must_change_password=auto_generated,
        )
        db.add(user)
        await db.commit()

        if auto_generated:
            banner = (
                "\n" + "═" * 64 + "\n"
                "  ⚠️  INITIAL ADMIN AUTO-PROVISIONED (random password)\n"
                f"  Username : {username}\n"
                f"  Password : {password}\n"
                "  This password was randomly generated because no ADMIN_PASSWORD\n"
                "  was configured. LOG IN and change it immediately.\n"
                + "═" * 64
            )
            logger.warning(banner)
        elif password in _WEAK_ADMIN_PASSWORDS or len(password) < 8:
            logger.warning(
                "Admin user '%s' created with a weak/default password. "
                "Change it before deploying to production.",
                username,
            )
        else:
            logger.info("Created admin user '%s'", username)


async def _ensure_model_config() -> None:
    from app.db.engine import async_session
    from app.model_config import seed_model_config_from_env

    async with async_session() as db:
        await seed_model_config_from_env(db)


async def _check_runtime_image() -> None:
    """Ensure the user-runtime image is present so user containers spin up fast.

    On first install the runtime image must already be available (shipped via
    the offline tarball / private registry). If it's missing locally we attempt
    a pull (using the configured, registry-qualified name) and warn loudly
    instead of failing silently — otherwise the first user would hang on
    container creation.
    """
    try:
        from app.container.manager import _runtime_image
        import docker
        from docker.errors import APIError, NotFound
    except Exception as e:  # noqa: BLE001
        logger.warning("跳过运行时镜像检查（依赖不可用）: %s", e)
        return

    image = _runtime_image()
    if not image:
        logger.warning("未配置运行时镜像（hermes_image 为空），用户容器将无法创建")
        return

    try:
        client = docker.from_env()
    except Exception as e:  # noqa: BLE001
        logger.warning("无法连接 Docker，跳过运行时镜像检查: %s", e)
        return

    try:
        client.images.get(image)
        logger.info("运行时镜像就绪: %s", image)
        return
    except NotFound:
        pass
    except APIError as e:  # noqa: BLE001
        logger.warning("查询运行时镜像出错: %s", e)

    logger.warning(
        "运行时镜像 %s 本地不存在，尝试拉取（首次安装应随交付包预置）...", image
    )
    try:
        client.images.pull(image)
        logger.info("运行时镜像拉取成功: %s", image)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "⚠️ 运行时镜像 %s 拉取失败：%s\n"
            "请确认已通过离线包/私有仓库加载该镜像，否则用户首次使用时会卡在容器创建。",
            image, e,
        )


async def _migrate_add_missing_columns() -> None:
    """Detect columns defined in ORM models but missing from the DB, and ADD them.

    This is a lightweight auto-migration for simple column additions (no renames,
    no type changes, no drops).  Sufficient for iterative development without a
    full Alembic setup.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            try:
                db_columns = await conn.run_sync(
                    lambda sync_conn, t=table.name: {
                        c["name"] for c in sa_inspect(sync_conn).get_columns(t)
                    }
                )
            except Exception:
                # Table doesn't exist yet — create_all will handle it
                continue

            for col in table.columns:
                if col.name in db_columns:
                    continue

                # Build column type SQL
                col_type = col.type.compile(engine.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default_clause = ""
                if col.server_default is not None:
                    default_arg = getattr(col.server_default, "arg", None)
                    if hasattr(default_arg, "text"):
                        default_sql = default_arg.text
                    elif isinstance(default_arg, str):
                        escaped_default = default_arg.replace("'", "''")
                        default_sql = f"'{escaped_default}'"
                    else:
                        default_sql = str(default_arg)
                    default_clause = f" DEFAULT {default_sql}"

                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type} {nullable}{default_clause}'
                logger.info("Auto-migration: %s", ddl)
                await conn.execute(text(ddl))

        await conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_settings_summary()
    # Ensure the target database exists before creating tables
    await _ensure_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified")
    # Add any columns that exist in models but not yet in the DB
    await _migrate_add_missing_columns()
    await _ensure_admin_user()
    await _ensure_model_config()
    # 校验运行时镜像就绪，保证首次安装即可快速创建用户容器
    await _check_runtime_image()
    # Seed the built-in connector catalog (WorkBuddy-compatible) — idempotent.
    try:
        from app.db.engine import AsyncSession
        from app.routes.connectors import seed_builtin_connectors

        async with AsyncSession(engine) as seed_db:
            n = await seed_builtin_connectors(seed_db)
            if n:
                logger.info("Seeded %d built-in MCP connectors", n)
    except Exception:
        logger.exception("failed to seed built-in connectors")
    yield
    await close_runtime_backends()
    await engine.dispose()


app = FastAPI(
    title="ComWorker Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    AuditContextMiddleware,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount route groups
app.include_router(auth.router)
app.include_router(llm.router)
app.include_router(models.router)
app.include_router(comworker_compat.router)
# MUST be registered before proxy.router: /api/comworker/platform-config would
# otherwise be swallowed by the proxy catch-all and forwarded to a user container.
app.include_router(platform_config.client_router)
app.include_router(proxy.router)
app.include_router(admin.router)
app.include_router(connectors.router)
app.include_router(platform_config.admin_router)
app.include_router(setup.router)


@app.get("/api/ping")
async def ping():
    return {"message": "pong", "service": "comworker-platform"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
