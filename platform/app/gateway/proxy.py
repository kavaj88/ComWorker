"""同协议代理，不经模型适配 SDK，不重试或重组响应。"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.gateway.access import authenticate, available_models, resolve_route
from app.gateway.protocol import (
    CONTENT_LIMIT,
    UsageCapture,
    forwarding_headers,
    provider_base,
    token_counts,
    upstream_url,
)
from app.model_keys import get_api_key

router = APIRouter(prefix="/api/gateway/v1", tags=["model-gateway"])
ENDPOINTS = {
    "chat/completions": "chat_completions",
    "responses": "responses",
    "responses/compact": "responses",
    "messages": "anthropic_messages",
    "messages/count_tokens": "anthropic_messages",
}
MAX_REQUEST_BYTES = 32 * 1024 * 1024


class ManagedStream(StreamingResponse):
    def __init__(self, *args, finish, **kwargs):
        super().__init__(*args, **kwargs)
        self.finish = finish

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 取消请求时仍释放连接；即使尚未迭代响应体也记录状态。
            with anyio.CancelScope(shield=True):
                await self.finish()


@router.get("/models")
async def models(request: Request):
    runtime = request.app.state.gateway
    async with runtime.sessions() as db:
        token = await authenticate(db, request.headers)
        allowed = [
            m for m in await available_models(db, token.user_id) if m["id"] in token.allowed_models
        ]
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": 0, "owned_by": m["provider_id"]}
            for m in allowed
        ],
    }


@router.post("/{endpoint:path}")
async def forward(endpoint: str, request: Request):
    runtime = request.app.state.gateway
    begin = time.perf_counter()
    protocol = ENDPOINTS.get(endpoint)
    row = dict(
        id=str(uuid.uuid4()),
        token_id=None,
        user_id=None,
        model=None,
        provider_id=None,
        upstream_model=None,
        protocol=protocol or "unknown",
        status_code=500,
        outcome="error",
        started_at=datetime.utcnow(),
        duration_ms=0,
        first_byte_ms=None,
        input_tokens=None,
        output_tokens=None,
        usage=None,
        error_type=None,
        content_state="disabled",
        content_expires_at=None,
        request_content=None,
        response_content=None,
    )
    upstream = None
    capture = None
    finished = False

    async def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        try:
            if upstream is not None:
                await upstream.aclose()
        finally:
            if capture:
                capture.finish()
                if capture.failed and row["outcome"] == "success":
                    row.update(outcome="error", error_type="upstream_event")
                row["usage"] = capture.usage
                row["input_tokens"], row["output_tokens"] = token_counts(capture.usage)
                row["response_content"] = capture.content
                if capture.truncated:
                    row["content_state"] = "truncated"
            row["duration_ms"] = round((time.perf_counter() - begin) * 1000)
            runtime.record(row)

    try:
        async with runtime.sessions() as db:
            token = await authenticate(db, request.headers)
            row.update(token_id=token.id, user_id=token.user_id)
            # 上传正文时释放数据库连接；读完后重验，避免上传期间吊销失效。
            await db.close()
            if not protocol:
                raise HTTPException(404, "暂不支持该模型接口")
            if request.headers.get("content-encoding", "identity") != "identity":
                raise HTTPException(415, "请使用未压缩的 JSON 请求体")
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                    raise HTTPException(413, "请求体超过 32 MiB 上限")
                body.extend(chunk)
            raw = bytes(body)
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(400, "请求体必须是 JSON")
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("model"), str)
                or not 0 < len(payload["model"]) <= 256
            ):
                raise HTTPException(400, "请求必须包含有效的 model 字段")
            row["model"] = payload["model"]
            token = await authenticate(db, request.headers)
            provider, model = await resolve_route(db, token, payload["model"], protocol)
            row.update(provider_id=provider.id, upstream_model=model)
            try:
                url = upstream_url(provider_base(provider), endpoint)
            except ValueError as exc:
                raise HTTPException(503, str(exc))
            headers = forwarding_headers(request.headers)
            headers["content-type"] = "application/json"
            headers["accept-encoding"] = "identity"
            if protocol == "anthropic_messages":
                headers["x-api-key"] = get_api_key(provider)
                headers.setdefault("anthropic-version", "2023-06-01")
            else:
                headers["authorization"] = "Bearer " + get_api_key(provider)
            if token.retain_content:
                row.update(
                    content_state="truncated" if len(raw) > CONTENT_LIMIT else "available",
                    content_expires_at=row["started_at"] + timedelta(days=token.retention_days),
                    request_content=raw[:CONTENT_LIMIT].decode("utf-8", errors="replace"),
                )
            # 仅显式 provider/model 引用需要替换；真实模型名保留原始 JSON 字节。
            if model != payload["model"]:
                payload["model"] = model
                raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            retain = token.retain_content
        if request.url.query:
            url += "?" + request.url.query
        req = runtime.client.build_request("POST", url, headers=headers, content=raw)
        upstream = await runtime.client.send(req, stream=True)
        row["status_code"] = upstream.status_code
        row["outcome"] = "cancelled"
        response_headers = forwarding_headers(upstream.headers)
        response_headers["x-accel-buffering"] = "no"
        response_headers["x-gateway-request-id"] = row["id"]
        capture = UsageCapture(
            stream="text/event-stream" in upstream.headers.get("content-type", ""), retain=retain
        )

        async def chunks():
            try:
                async for chunk in upstream.aiter_raw():
                    if row["first_byte_ms"] is None:
                        row["first_byte_ms"] = round((time.perf_counter() - begin) * 1000)
                    capture.feed(chunk)
                    yield chunk
                row["outcome"] = "success" if upstream.status_code < 400 else "error"
                if upstream.status_code >= 400:
                    row["error_type"] = "upstream_http"
            except httpx.HTTPError:
                row["outcome"] = "error"
                row["error_type"] = "upstream_stream"
                raise

        return ManagedStream(
            chunks(), status_code=upstream.status_code, headers=response_headers, finish=finish
        )
    except HTTPException as exc:
        row.update(status_code=exc.status_code, error_type="gateway_rejection")
        await finish()
        error = {"type": "invalid_request_error", "message": str(exc.detail)}
        return JSONResponse(
            {"type": "error", "error": error},
            status_code=exc.status_code,
            headers={"x-gateway-request-id": row["id"]},
        )
    except httpx.HTTPError as exc:
        status = 504 if isinstance(exc, httpx.TimeoutException) else 502
        row.update(
            status_code=status,
            error_type="upstream_timeout" if status == 504 else "upstream_connection",
        )
        await finish()
        return JSONResponse(
            {"error": {"type": row["error_type"], "message": "上游连接失败，请稍后重试"}},
            status_code=status,
        )
    except BaseException as exc:
        row.update(
            outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
            error_type="cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else "gateway_internal",
        )
        with anyio.CancelScope(shield=True):
            await finish()
        raise
