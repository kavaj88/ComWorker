"""Helper to keep ModelProviderConfig.api_key encrypted at rest.

The DB column ``api_key`` always stores an encrypted ciphertext when
``api_key_encrypted`` is True. Legacy rows may still contain a plaintext
value (with ``api_key_encrypted=False``) — those are decrypted lazily on
read and re-encrypted on the next write.

Use ``get_api_key(provider)`` everywhere instead of ``provider.api_key``
directly so this invariant is preserved.
"""

from __future__ import annotations

from app.config import settings
from app.db.models import ModelProviderConfig
from app.security import decrypt_api_key, encrypt_api_key


def get_api_key(provider: ModelProviderConfig) -> str:
    """Return the plaintext API key for ``provider``.

    Decrypts with the master key when ``api_key_encrypted`` is True;
    falls back to the raw column value for legacy plaintext rows so the
    system keeps working across the upgrade.
    """

    raw = provider.api_key or ""
    if not raw:
        return ""
    if not provider.api_key_encrypted:
        return raw
    return decrypt_api_key(raw, settings.model_keys_master_key)


def set_api_key(provider: ModelProviderConfig, plain: str | None) -> None:
    """Store ``plain`` encrypted on ``provider``. Passing empty/None clears."""

    plain = (plain or "").strip()
    if not plain:
        provider.api_key = None
        provider.api_key_encrypted = False
        return
    provider.api_key = encrypt_api_key(plain, settings.model_keys_master_key)
    provider.api_key_encrypted = True