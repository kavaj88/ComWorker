"""Tracks the per-installation "first-boot" setup state for the empty-product
release.

Customers receive a ComWorker install with NO model keys and a freshly
auto-generated admin password. The very first login guides them through:

    1) change the auto-generated admin password   ← REQUIRED (security)
    2) add at least one LLM provider API key      ← RECOMMENDED (business)

Changing the password is a hard gate: until it is done, admin-only API
routes are blocked (with a small allowlist for the wizard itself:
change-password, model-config, logout).

Adding a model key is deliberately NOT a hard gate — a customer may not
have a key at hand on first boot, and locking the whole product until they
do is a poor experience. Missing keys only surface as an in-app hint
(chat unavailable until configured), never as a login block.

This module is the single source of truth for what "done" means. It reads
and writes ``SystemFlag`` rows (a generic KV table) so no schema change is
required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemFlag


# ── Flag keys ──────────────────────────────────────────────────────────
PASSWORD_CHANGED_AT = "setup.password_changed_at"
FIRST_MODEL_KEY_ADDED_AT = "setup.first_model_key_added_at"
SETUP_COMPLETED_AT = "setup.completed_at"
SETUP_DISMISSED_AT = "setup.dismissed_at"  # operator opted out of the wizard


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_flag(db: AsyncSession, key: str) -> str | None:
    row = await db.get(SystemFlag, key)
    return row.value if row else None


async def _set_flag(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(SystemFlag, key)
    if row is None:
        row = SystemFlag(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    await db.commit()


# ── Public mutators ─────────────────────────────────────────────────────

async def mark_password_changed(db: AsyncSession) -> None:
    await _set_flag(db, PASSWORD_CHANGED_AT, _now_iso())


async def mark_model_key_added(db: AsyncSession) -> None:
    """Idempotent: only stamps the first time."""
    existing = await _get_flag(db, FIRST_MODEL_KEY_ADDED_AT)
    if existing:
        return
    await _set_flag(db, FIRST_MODEL_KEY_ADDED_AT, _now_iso())


async def mark_setup_completed(db: AsyncSession) -> None:
    await _set_flag(db, SETUP_COMPLETED_AT, _now_iso())


async def mark_setup_dismissed(db: AsyncSession) -> None:
    """Operator chooses to skip the wizard (e.g. for fully scripted installs)."""
    await _set_flag(db, SETUP_DISMISSED_AT, _now_iso())


# ── Status read ────────────────────────────────────────────────────────
#
# Hard gate — blocks admin endpoints until satisfied. Keep this list to
# security-essential steps only.
SETUP_REQUIRED_STEPS: list[str] = [
    "change_admin_password",
]

# Soft steps — shown as guidance in the wizard, never block the product.
# A customer can finish setup without them and configure them later from
# the workspace (e.g. model keys, without which chat is simply disabled).
SETUP_RECOMMENDED_STEPS: list[str] = [
    "add_model_key",
]


async def compute_setup_status(db: AsyncSession) -> dict:
    """Return the current setup status. Pure read — does not mutate.

    The frontend wizard and the ``require_setup_complete`` admin dependency
    both consume this.
    """

    flags = {
        "password_changed_at": await _get_flag(db, PASSWORD_CHANGED_AT),
        "first_model_key_added_at": await _get_flag(db, FIRST_MODEL_KEY_ADDED_AT),
        "completed_at": await _get_flag(db, SETUP_COMPLETED_AT),
        "dismissed_at": await _get_flag(db, SETUP_DISMISSED_AT),
    }
    completed_steps: list[str] = []
    if flags["password_changed_at"]:
        completed_steps.append("change_admin_password")
    if flags["first_model_key_added_at"]:
        completed_steps.append("add_model_key")
    missing = [s for s in SETUP_REQUIRED_STEPS if s not in completed_steps]
    missing_recommended = [s for s in SETUP_RECOMMENDED_STEPS if s not in completed_steps]
    return {
        "required_steps": list(SETUP_REQUIRED_STEPS),
        "recommended_steps": list(SETUP_RECOMMENDED_STEPS),
        "completed_steps": completed_steps,
        "missing_steps": missing,
        "missing_recommended_steps": missing_recommended,
        "is_complete": not missing,
        "is_dismissed": bool(flags["dismissed_at"]),
        "flags": flags,
    }


def is_setup_required(status: dict) -> bool:
    """Whether the wizard should still gate admin endpoints.

    Once the operator marks setup as completed (or dismisses the wizard),
    subsequent admin logins no longer hit the wizard unless ``completed_at``
    is explicitly cleared by an operator (e.g. after rotating the master
    key).
    """

    if status.get("is_dismissed"):
        return False
    return not status.get("is_complete", True)


# ── Bootstrap safety net ───────────────────────────────────────────────
# If the operator added a model key directly via environment variables
# before the very first login (the legacy "env-seeded" install path), we
# have already passed the ``add_model_key`` step on their behalf. Detect
# that case on first read and back-fill the flag.

async def bootstrap_setup_flags(db: AsyncSession, *, has_any_model_key: bool) -> None:
    """Back-fill setup flags when the operator pre-seeded the install.

    Called once at app startup; safe to invoke on every boot because the
    ``mark_*`` helpers only stamp on transition.
    """

    if has_any_model_key:
        await mark_model_key_added(db)
    existing = await _get_flag(db, PASSWORD_CHANGED_AT)
    # We can't tell from a flag alone whether the password was changed
    # away from the auto-generated one — admin users with a fresh DB
    # always start with must_change_password=True. Leave the password
    # flag alone here; the change-password endpoint sets it.