"""Internal training trace ingestion API."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.training_trace import append_jsonl_trace, redact_trace_record

router = APIRouter(prefix="/api/training", tags=["training-traces"])


class TrainingTraceIngestRequest(BaseModel):
    """One sanitized trace record submitted by an internal collector."""

    trace: dict[str, Any] = Field(default_factory=dict)
    privacy_level: Literal["L0", "L1"] = "L1"


def _require_ingest_enabled() -> None:
    if not getattr(settings, "training_trace_ingest_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Training trace ingestion is disabled",
        )


def _require_ingest_token(authorization: str) -> None:
    expected = getattr(settings, "training_trace_ingest_token", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Training trace ingestion token is not configured",
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bearer token",
        )


def _reject_raw_identity(trace: dict[str, Any]) -> None:
    privacy = trace.get("privacy")
    if isinstance(privacy, dict) and privacy.get("raw_user_identity_stored") is True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Raw user identity traces are not accepted by this ingest endpoint",
        )


def _build_ingested_record(body: TrainingTraceIngestRequest) -> dict[str, Any]:
    if not body.trace:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="trace must not be empty",
        )
    _reject_raw_identity(body.trace)
    record = redact_trace_record(body.trace)
    record["trace_id"] = str(record.get("trace_id") or uuid.uuid4())
    privacy = record.get("privacy") if isinstance(record.get("privacy"), dict) else {}
    record["privacy"] = {
        **privacy,
        "ingest_privacy_level": body.privacy_level,
        "raw_user_identity_stored": False,
    }
    record["ingest"] = {
        "interface": "api.training_traces",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "training_trace.v1",
    }
    return record


@router.post("/traces")
async def ingest_training_trace(
    body: TrainingTraceIngestRequest,
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    """Persist one internally submitted sanitized trace record."""
    _require_ingest_enabled()
    _require_ingest_token(authorization)
    record = _build_ingested_record(body)
    append_jsonl_trace(
        getattr(settings, "training_trace_dir", ".hermes/training_traces"),
        record,
        prefix="training_trace_ingest",
    )
    return {"ok": True, "trace_id": record["trace_id"], "stored": True}
