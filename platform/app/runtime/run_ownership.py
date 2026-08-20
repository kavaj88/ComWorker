from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RuntimeRun


async def record_runtime_run(
    db: AsyncSession,
    *,
    run_id: str,
    user_id: str,
    session_key: str,
    runtime_mode: str,
    backend: str,
) -> RuntimeRun | None:
    normalized_run_id = (run_id or "").strip()
    if not normalized_run_id:
        return None

    result = await db.execute(select(RuntimeRun).where(RuntimeRun.run_id == normalized_run_id))
    record = result.scalar_one_or_none()
    if record is None:
        record = RuntimeRun(run_id=normalized_run_id)
        db.add(record)

    record.user_id = user_id
    record.session_key = session_key
    record.runtime_mode = runtime_mode
    record.backend = backend
    await db.commit()
    await db.refresh(record)
    return record


async def ensure_runtime_run_owned(
    db: AsyncSession,
    *,
    run_id: str,
    user_id: str,
    runtime_mode: str,
    backend: str,
) -> RuntimeRun:
    normalized_run_id = (run_id or "").strip()
    result = await db.execute(select(RuntimeRun).where(RuntimeRun.run_id == normalized_run_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime run was not found",
        )

    if (
        record.user_id != user_id
        or record.runtime_mode != runtime_mode
        or record.backend != backend
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Run does not belong to current user",
        )

    return record
