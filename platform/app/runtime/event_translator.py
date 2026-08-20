"""Translate Hermes runtime events into ComWorker-compatible SSE payloads."""

import json
from typing import Any


_EVENT_STATE_MAP = {
    "response.output_text.delta": "delta",
    "response.completed": "final",
}


def hermes_event_to_comworker_sse(payload: dict[str, Any], *, session_key: str, platform_run_id: str) -> str | None:
    event_type = payload.get("type")
    state = _EVENT_STATE_MAP.get(event_type)

    text = payload.get("delta") or payload.get("text") or ""
    if state is None and not text:
        return None

    if state is None:
        state = "delta"

    body = {
        "event": "chat",
        "state": state,
        "sessionKey": session_key,
        "runId": platform_run_id,
    }
    if text:
        body["text"] = text
    if payload.get("run_id"):
        body["upstreamRunId"] = payload["run_id"]

    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
