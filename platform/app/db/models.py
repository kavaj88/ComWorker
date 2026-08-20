"""SQLAlchemy ORM models for the platform."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    """Platform user account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # user | admin
    quota_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")  # free | basic | pro
    # 运行模式，dedicated表示独立容器，shared表示用户共享comworker
    runtime_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="dedicated", server_default="dedicated")  # dedicated | shared
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # When True, the user must change their password on next login (set for
    # auto-provisioned admin accounts whose password was randomly generated).
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Admin-assigned per-user default model (overrides platform default but is
    # overridden by the user's own model selection in the client).
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    # SSO fields (e.g. 如果需要SSO登录，需要这2个字段)
    sso_uid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    sso_token: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Container(Base):
    """Per-user Docker container metadata."""

    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    docker_id: Mapped[str] = mapped_column(String(128), nullable=True)  # Docker container ID
    container_token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    # Status: creating | running | paused | archived
    internal_host: Mapped[str] = mapped_column(String(64), nullable=True)
    internal_port: Mapped[int] = mapped_column(Integer, nullable=True, default=18080)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Durable source of truth for the user's *own* Hermes config edits
    # (custom_providers with secrets, model.default, model.provider). This is
    # persisted in Postgres so a container rebuild / volume loss never wipes
    # user data — config.yaml in the volume is always reconstructed from this.
    user_config: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class RuntimeRun(Base):
    """Tracks runtime run ownership for access control."""

    __tablename__ = "runtime_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    runtime_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="dedicated")
    backend: Mapped[str] = mapped_column(String(32), nullable=False, default="hermes")


class UserPortBinding(Base):
    """Per-user persisted host port preferences for recreated containers."""

    __tablename__ = "user_port_bindings"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    host_bind_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="0.0.0.0")
    host_port_browser: Mapped[int] = mapped_column(Integer, nullable=True, unique=True)
    host_port_service: Mapped[int] = mapped_column(Integer, nullable=True, unique=True)


class UserCapability(Base):
    """Per-user capability grant/override (explicit enable or revoke)."""

    __tablename__ = "user_capabilities"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PlatformCapabilityDefault(Base):
    """Platform-level default: auto-inject this capability at container build."""

    __tablename__ = "platform_capability_defaults"

    capability: Mapped[str] = mapped_column(String(32), primary_key=True)
    default_inject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UsageRecord(Base):
    """LLM token usage per request."""

    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    upstream_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelProviderConfig(Base):
    """Admin-managed LLM provider configuration."""

    __tablename__ = "model_provider_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    # API protocol for this provider: "anthropic_messages" | "chat_completions" |
    # "google_ai" (snake_case, mirrors hermes api_mode). Null => infer from
    # provider_type / base_url. When set, it authoritatively governs both the
    # connectivity probe and the live LLM proxy routing.
    api_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    api_base: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # LLM provider API key. May be either a Fernet-encrypted ciphertext (preferred)
    # or a legacy plaintext value written before encryption was introduced;
    # app.model_config.decrypt_api_key handles both transparently.
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True once the api_key column has been encrypted with the master key on
    # first read/write. Used by the lazy migration helper to avoid re-encrypting
    # the same row on every request.
    api_key_encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    models: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Per-user visibility scope for this provider. None / {"mode":"all"} => every
    # user may use it. {"mode":"allow","user_ids":[...]} => only listed users.
    # {"mode":"deny","user_ids":[...]} => everyone except listed users.
    user_scope: Mapped[dict] = mapped_column(JSON, nullable=True, default=lambda: {"mode": "all"})
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    """Audit trail for key operations."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # login | llm_call | container_create | ...
    resource: Mapped[str] = mapped_column(String(128), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=True)
    # Network/transport metadata captured by the audit middleware. Stored
    # separately from `detail` so that dashboards can filter / group on
    # these fields without parsing JSON every time.
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class SystemFlag(Base):
    """Simple key/value store for platform-wide admin toggles."""

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


DEFAULT_INSTALL_FLAG = "default_install_all"


# ─── MCP Connectors (platform-managed connector library) ─────────────────────

class McpConnector(Base):
    """Admin-managed MCP server catalog entry — the platform's connector library.

    Single source of truth for officially provided connectors. Each row maps to a
    ``mcp_servers.<name>`` entry in a user container's config.yaml. For the
    ``shared`` credential strategy the platform-held secret lives in
    ``shared_credential`` (treat as sensitive, never echoed to clients). For
    ``per_user_oauth`` the user authenticates inside the container; no secret here.
    """

    __tablename__ = "mcp_connectors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    icon: Mapped[str | None] = mapped_column(String(512), nullable=True)
    transport: Mapped[str] = mapped_column(String(32), nullable=False, default="streamable_http")
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cli_config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'::json"))
    credential_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="none", server_default="none")
    examples: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_credential: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class UserMcpConnector(Base):
    """Per-user enable/override state for platform connectors, plus pure-custom servers.

    - connector_id set  → records whether the user enabled the platform connector ``name``
                          (override of the platform default / mandatory state).
    - connector_id NULL  → ``name`` + ``personal_config_json`` fully describe a user-added
                          custom MCP server; platform re-injects it on container rebuild.
    """

    __tablename__ = "user_mcp_connectors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    connector_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    personal_config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    credential_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentTemplate(Base):
    """Platform-managed agent (专家/expert) templates admins can assign to users.

    System agents (main/manager/...) are merged at read time and are read-only;
    this table stores admin-created custom expert templates (persona + prompt).
    """

    __tablename__ = "agent_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avatar: Mapped[str | None] = mapped_column(String(512), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SkillOverride(Base):
    """Admin overrides for built-in catalog skills.

    Built-in skills live in a read-only mounted directory, so admins cannot edit
    their files directly. Instead, edits/deletes are recorded here and merged at
    read time by the catalog endpoint (and the admin "技能管理" page). ``content``
    optionally stores an overridden SKILL.md body (metadata-level; it does not
    change hermes runtime execution of the built-in skill).
    """

    __tablename__ = "skill_overrides"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    skill_name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
