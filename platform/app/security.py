"""Centralised helpers for loading secrets from docker-style /run/secrets
files and for symmetric encryption of persisted secrets such as LLM
provider API keys.

Docker compose (and Swarm) automatically mount every entry of the
``secrets:`` block at ``/run/secrets/<name>`` inside the container. This
module reads those files when present and falls back to the corresponding
``PLATFORM_*`` environment variables so that:

* in production (compose ``secrets:`` block) the values come from docker
  secrets and never appear in ``docker inspect``;
* in local dev (``docker compose up`` without a secrets block) the values
  can still be supplied via ``.env``.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

DOCKER_SECRETS_DIR: Final[str] = "/run/secrets"


def read_secret_file(name: str) -> str | None:
    """Read a secret from ``/run/secrets/<name>``.

    Returns ``None`` if the docker secrets directory does not exist (the
    typical case for plain ``docker compose up`` without ``secrets:``) or
    if the file is empty/whitespace-only.
    """

    path = Path(DOCKER_SECRETS_DIR) / name
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Failed to read docker secret %s: %s", path, exc)
        return None
    return value or None


def load_or_generate_secret(
    *,
    name: str,
    env_var: str,
    min_length: int = 32,
    persist_path: str | None = None,
) -> str:
    """Resolve a secret value with deterministic precedence:

    1. ``/run/secrets/<name>``  (docker secret)
    2. ``os.environ[env_var]`` (compose ``environment:`` / ``env_file:``)
    3. ``persist_path``        (a previously generated and persisted value)
    4. Auto-generated 32-byte url-safe token, optionally persisted

    A loud warning is logged whenever the value had to be auto-generated so
    operators notice that the secret will not survive a restart unless it is
    persisted via ``persist_path``.
    """

    file_value = read_secret_file(name)
    if file_value:
        if len(file_value) < min_length:
            logger.warning(
                "Docker secret %s is shorter than %d chars; using anyway",
                name, min_length,
            )
        return file_value

    env_value = os.environ.get(env_var, "").strip()
    if env_value:
        if len(env_value) < min_length:
            logger.warning(
                "%s is shorter than %d chars; using anyway", env_var, min_length,
            )
        return env_value

    if persist_path:
        persisted = Path(persist_path)
        if persisted.is_file():
            try:
                stored = persisted.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("Failed to read persisted secret %s: %s", persist_path, exc)
            else:
                if stored:
                    return stored

    generated = secrets.token_urlsafe(48)
    logger.warning(
        "Secret %s was empty — auto-generated strong random value (will NOT "
        "survive restart unless persist_path is writable). Set it via docker "
        "secret, %s env var, or persisted file at %s.",
        name, env_var, persist_path or "<none>",
    )

    if persist_path:
        try:
            persisted = Path(persist_path)
            persisted.parent.mkdir(parents=True, exist_ok=True)
            persisted.write_text(generated, encoding="utf-8")
            try:
                os.chmod(persist_path, 0o600)
            except OSError:
                pass  # not critical on every FS
        except OSError as exc:
            logger.error("Failed to persist generated secret to %s: %s", persist_path, exc)

    return generated


def bootstrap_secrets_into_env() -> None:
    """Copy docker secret values into ``os.environ`` so pydantic-settings
    picks them up. Only fills missing entries (existing env vars win).
    """

    mapping: dict[str, str] = {
        "jwt_secret": "PLATFORM_JWT_SECRET",
        "model_keys_master_key": "PLATFORM_MODEL_KEYS_MASTER_KEY",
        "admin_password": "PLATFORM_ADMIN_PASSWORD",
    }
    for secret_name, env_name in mapping.items():
        value = read_secret_file(secret_name)
        if value:
            os.environ.setdefault(env_name, value)


def is_secret_weak(value: str, *, min_length: int = 32) -> bool:
    """Return True if a secret value is empty or too short to be safe."""

    if not value:
        return True
    return len(value.strip()) < min_length


# ── Fernet helpers for LLM provider API keys ──────────────────────────
# Fernet uses AES-128-CBC + HMAC-SHA256 under the hood. The master key is
# a 32-byte url-safe base64 string derived from the docker secret.

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402  (after constants)


def _fernet_from_master_key(master_key: str) -> Fernet:
    """Derive a Fernet instance from an arbitrary-length master key.

    We derive a 32-byte key by SHA-256-hashing the master_key so that
    operators can rotate the master by simply changing the secret value
    without having to base64-encode it manually.
    """

    import base64
    import hashlib

    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt_api_key(plain: str, master_key: str) -> str:
    """Encrypt an LLM provider API key. Returns a Fernet token string."""

    if not plain:
        return ""
    fernet = _fernet_from_master_key(master_key)
    return fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_api_key(cipher: str | None, master_key: str) -> str:
    """Decrypt an LLM provider API key.

    Returns "" for empty input and falls back to the raw value (without
    decryption) when it does not parse as a Fernet token, which keeps the
    system compatible with values written before this feature existed.
    """

    if not cipher:
        return ""
    try:
        fernet = _fernet_from_master_key(master_key)
        return fernet.decrypt(cipher.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Legacy plaintext value — return as-is so old data still works.
        return cipher