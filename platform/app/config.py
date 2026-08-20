"""Platform gateway configuration."""

import os

from pydantic_settings import BaseSettings


def _bootstrap_docker_secrets_into_env() -> None:
    """Pre-load /run/secrets/<name> files into os.environ before Settings()
    is constructed, so that pydantic-settings picks them up automatically.
    This keeps docker secrets transparent to the rest of the codebase while
    keeping the env-var fallback working for plain ``docker compose up``.
    """

    try:
        from app.security import bootstrap_secrets_into_env
    except Exception:  # pragma: no cover - defensive: avoid import cycles
        return
    bootstrap_secrets_into_env()


_bootstrap_docker_secrets_into_env()


class Settings(BaseSettings):
    """Platform configuration loaded from environment variables."""

    # Database
    database_url: str = "postgresql+asyncpg://comworker:comworker@localhost:5432/comworker_platform"

    # JWT — default is empty so the value MUST come from one of:
    #   1) /run/secrets/jwt_secret  (docker secret, recommended)
    #   2) PLATFORM_JWT_SECRET env var
    #   3) a strong random key auto-generated at startup and persisted
    #      to /data/.jwt_secret (see app.main)
    # The legacy value "change-me-in-production" is intentionally removed —
    # shipping it would let every customer share the same signing key.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24  # 24 hours
    jwt_refresh_token_expire_days: int = 30

    # Master key used by app.security.encrypt_api_key / decrypt_api_key to
    # encrypt LLM provider API keys at rest in the database. Same resolution
    # rules as jwt_secret (docker secret → env → persisted file →
    # auto-generated).
    model_keys_master_key: str = ""

    # LLM Provider API Keys (platform-level, never exposed to containers)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_api_base: str = ""  # Custom OpenAI-compatible base URL
    deepseek_api_key: str = ""
    openrouter_api_key: str = ""
    dashscope_api_key: str = ""
    minimax_api_key: str = ""
    minimax_api_base: str = "https://api.minimax.io/v1"
    minimax_m27_use_highspeed: bool = True
    aihubmix_api_key: str = ""
    evolink_api_key: str = ""
    moonshot_api_key: str = ""
    kimi_api_key: str = ""
    zhipu_api_key: str = ""
    doubao_api_key: str = ""

    # Web search tool key (Tavily) — platform pool key for the web_search
    # capability. Injected into a user container only when that user is granted
    # web_search and has no own key. Configured via PLATFORM_TAVILY_API_KEY.
    tavily_api_key: str = ""

    # Self-hosted vLLM / OpenAI-compatible local model
    hosted_vllm_api_key: str = ""
    hosted_vllm_api_base: str = ""  # e.g. "http://117.133.60.219:8900/v1"

    # Default model for new users
    default_model: str = "claude-sonnet-4-5"

    # Runtime backend selection
    dedicated_runtime_backend: str = "hermes"
    shared_runtime_backend: str = "hermes"
    hermes_connect_retries: int = 60
    hermes_retry_delay_seconds: float = 0.5
    hermes_api_toolsets: str = "full"
    hermes_reasoning_effort: str = "none"
    hermes_service_tier: str = ""

    # Dedicated runtime endpoints/images
    hermes_image: str = "comworker-hermes-agent:latest"
    dedicated_hermes_url: str = ""
    dedicated_hermes_internal_port: int = 18080
    dedicated_hermes_api_key: str = "dev-hermes-bridge-key"
    dedicated_hermes_default_provider: str = "custom"
    dedicated_hermes_default_base_url: str = "http://gateway:8080/llm/v1"
    dedicated_hermes_default_api_key: str = "platform-proxy"
    dedicated_runtime_container_name_prefix: str = "hermes-user"
    dedicated_runtime_data_volume_prefix: str = "hermes-data"
    container_network: str = "comworker-internal"

    # Shared runtime endpoints/tokens (Hermes shared runtime)
    shared_hermes_url: str = "http://shared-comworker:8080"
    shared_hermes_api_key: str = "dev-hermes-bridge-key"
    user_container_publish_ports: bool = True
    user_container_bind_ip: str = "0.0.0.0"
    container_tz: str = "Asia/Shanghai"
    # 🟢 提升资源限制（适合浏览器/agent）
    container_memory_limit: str = "2g"  # 原来 512m
    container_cpu_limit: float = 4.0  # 原来 1.0
    container_pids_limit: int = 1024  # 原来 100

    # 建议增加 shm（非常重要，防止 Chromium 崩溃）
    container_shm_size: str = "1g"

    # Idle management
    container_idle_pause_minutes: int = 30
    container_idle_archive_days: int = 30

    # Quotas (tokens per day)
    quota_free: int = 20000000
    quota_basic: int = 1_000_000
    quota_pro: int = 10_000_000

    # Admin account (auto-created on first startup)
    admin_username: str = ""
    admin_password: str = ""

    # Self-registration (client-side sign-up). Default off: users are created
    # by the administrator only; set ALLOW_SELF_REGISTER=true to open sign-up.
    allow_self_register: bool = False

    # Platform gateway
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    # Public-facing base URL (used to generate external access URLs in port mapping).
    # Leave empty unless deployed behind a public domain; configure via PLATFORM_PUBLIC_BASE_URL.
    public_base_url: str = ""

    # Skills marketplace (Gitee repo with categories)
    skills_marketplace_repo: str = "https://github.com/johnson7788/collect_skills.git"
    skills_marketplace_branch: str = "main"

    # Local dev: set to e.g. "http://127.0.0.1:18080" to skip Docker containers
    dev_comworker_url: str = ""

    # Local dev: runtime Gateway WS URL for direct WS proxy (e.g. "ws://127.0.0.1:18789")
    dev_gateway_url: str = ""

    # Local training trace capture, disabled by default.
    training_trace_enabled: bool = False
    training_trace_ingest_enabled: bool = False
    training_trace_ingest_token: str = ""
    training_trace_dir: str = ".hermes/training_traces"
    training_trace_hash_salt: str = ""

    model_config = {"env_prefix": "PLATFORM_"}


settings = Settings()
