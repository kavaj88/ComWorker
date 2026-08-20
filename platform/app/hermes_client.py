from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException, status


class HermesClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 120.0,
        api_key: str = "",
        connect_retries: int = 0,
        retry_delay_seconds: float = 0.25,
    ):
        if not base_url:
            raise ValueError("Hermes base URL is not configured")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key.strip()
        self.connect_retries = max(0, connect_retries)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or getattr(self._client, "is_closed", False):
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is None or getattr(self._client, "is_closed", False):
            self._client = None
            return
        await self._client.aclose()
        self._client = None

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def request(self, method: str, path: str, **kwargs) -> Any:
        timeout = kwargs.pop("timeout", self.timeout)
        headers = dict(self._auth_headers())
        if "headers" in kwargs and kwargs["headers"]:
            headers.update(kwargs["headers"])
        if headers:
            kwargs["headers"] = headers
        for attempt in range(self.connect_retries + 1):
            client = self._ensure_client()
            try:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    timeout=timeout,
                    **kwargs,
                )
                break
            except httpx.ConnectError as exc:
                if attempt < self.connect_retries:
                    await asyncio.sleep(self.retry_delay_seconds)
                    continue
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Hermes runtime is unavailable",
                ) from exc
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hermes runtime is unavailable",
            )

        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=self._extract_error_detail(payload))
        return payload

    async def get_models(self) -> dict:
        payload = await self.request("GET", "/v1/models")
        return payload if isinstance(payload, dict) else {"data": []}

    async def create_run(
        self,
        *,
        message: str,
        session_id: str | None = None,
        session_key: str | None = None,
        model: str = "hermes-agent",
        conversation_history: list[dict[str, str]] | None = None,
        instructions: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "input": message,
        }
        if session_id:
            body["session_id"] = session_id
        if conversation_history is not None:
            body["conversation_history"] = conversation_history
        if instructions:
            body["instructions"] = instructions
        headers = {}
        if session_key:
            headers["X-Hermes-Session-Key"] = session_key
        payload = await self.request(
            "POST",
            "/v1/runs",
            headers=headers,
            json=body,
            timeout=300.0,
        )
        if isinstance(payload, dict):
            if session_id and "session_id" not in payload:
                payload["session_id"] = session_id
            return payload
        return {"status": "started"}

    async def chat(self, *, message: str, session_id: str | None = None, model: str = "hermes-agent") -> dict:
        headers = {}
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id
        payload = await self.request(
            "POST",
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": message}],
            },
            timeout=300.0,
        )
        if isinstance(payload, dict):
            if session_id and "session_id" not in payload:
                payload["session_id"] = session_id
            return payload
        return {"choices": []}

    async def collect_run_events(
        self,
        run_id: str,
        timeout_ms: int = 25000,
        on_event: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        events: list[dict] = []
        client = self._ensure_client()
        try:
            async with client.stream(
                "GET",
                f"{self.base_url}/v1/runs/{run_id}/events",
                params={"timeout_ms": timeout_ms},
                headers=self._auth_headers(),
                timeout=None,
            ) as response:
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail="Hermes run event stream request failed",
                    )
                buffer = ""
                async for chunk in response.aiter_bytes():
                    buffer += chunk.decode("utf-8", errors="ignore")
                    while "\n\n" in buffer:
                        raw_event, buffer = buffer.split("\n\n", 1)
                        parsed = self._parse_sse_event(raw_event)
                        if parsed is not None:
                            events.append(parsed)
                            if on_event is not None:
                                on_event(parsed)
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hermes runtime is unavailable",
            ) from exc
        return events

    def _parse_sse_event(self, raw_event: str) -> dict | None:
        data_lines: list[str] = []
        for line in raw_event.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return None
        data = "\n".join(data_lines)
        if data == "[DONE]":
            return None
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return {"type": "message", "data": data}
        if not isinstance(parsed, dict):
            return {"type": "message", "data": parsed}
        if "type" not in parsed and isinstance(parsed.get("event"), str):
            parsed["type"] = parsed["event"]
        return parsed

    @staticmethod
    def _extract_error_detail(payload: Any) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if payload.get("detail"):
                return str(payload["detail"])
        if isinstance(payload, str) and payload:
            return payload
        return "Hermes request failed"
