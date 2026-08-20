"""LLM Proxy API routes — OpenAI-compatible chat/completions endpoint.

User containers hit this endpoint instead of calling LLM providers
directly.  The container token is sent as the Bearer token.

Design: pass-through proxy. The original request body is forwarded to the
LLM provider with minimal modification (model routing + API key injection).
This preserves all OpenAI-compatible parameters that ComWorker sends:
reasoning_effort, thinking, top_p, response_format, service_tier, etc.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_db
from app.llm_proxy.service import proxy_chat_completion

logger = logging.getLogger("platform.routes.llm")
router = APIRouter(prefix="/llm/v1", tags=["llm-proxy"])


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    """OpenAI-compatible chat completions endpoint — pass-through proxy."""
    import json as _json

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer token")
    container_token = authorization[7:]

    raw_body = await request.body()
    try:
        raw_json = _json.loads(raw_body)
    except _json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    model = raw_json.get("model", "")
    stream = raw_json.get("stream", False)

    if not model:
        raise HTTPException(status_code=400, detail="Missing 'model' field")

    result = await proxy_chat_completion(
        db=db,
        container_token=container_token,
        raw_request=raw_json,
    )

    if stream:
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return result
