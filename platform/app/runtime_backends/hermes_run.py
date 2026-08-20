from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)


class HermesEventSanitizer:
    """Sanitize Hermes events, stripping thinking blocks."""

    def __init__(self):
        self._in_thinking_block = False

    def filter_delta(self, text: str) -> str:
        """Filter delta text, stripping thinking blocks."""
        output: list[str] = []
        pos = 0
        changed = False

        while pos < len(text):
            if self._in_thinking_block:
                close_match = _THINK_CLOSE_RE.search(text, pos)
                changed = True
                if close_match is None:
                    return "".join(output)
                pos = close_match.end()
                self._in_thinking_block = False

            open_match = _THINK_OPEN_RE.search(text, pos)
            if open_match is None:
                output.append(text[pos:])
                break

            output.append(text[pos : open_match.start()])
            close_match = _THINK_CLOSE_RE.search(text, open_match.end())
            changed = True
            if close_match is None:
                self._in_thinking_block = True
                break
            pos = close_match.end()

        filtered = "".join(output)
        return filtered.strip() if changed else filtered

    def sanitize_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("type") or event.get("event")
        sanitized = dict(event)

        if event_type == "reasoning.available":
            text = event.get("text") or event.get("preview") or ""
            if isinstance(text, str):
                sanitized["text"] = strip_thinking_blocks(text) or "正在分析任务并规划下一步"
            return sanitized

        if isinstance(event_type, str) and event_type.startswith("reasoning."):
            return None

        if event_type == "message.delta":
            delta = event.get("delta")
            if not isinstance(delta, str):
                return sanitized
            filtered = self.filter_delta(delta)
            if not filtered:
                return None
            sanitized["delta"] = filtered
            return sanitized

        if event_type == "message.completed" and isinstance(event.get("message"), dict):
            message = sanitize_hermes_message(event["message"])
            content = message.get("content")
            if message.get("role") == "assistant" and isinstance(content, str) and is_tool_result_content(content):
                return None
            sanitized["message"] = message
            return sanitized

        if event_type == "run.completed":
            output = event.get("output")
            if isinstance(output, str):
                sanitized["output"] = strip_thinking_blocks(output)
            elif isinstance(output, dict):
                sanitized["output"] = sanitize_hermes_message(output)
            return sanitized

        return sanitized


class HermesRunTimingTracker:
    def __init__(self, elapsed_ms: Callable[[], float]):
        self._elapsed_ms = elapsed_ms
        self._sanitizer = HermesEventSanitizer()
        self.first_event_ms: float | None = None
        self.first_delta_ms: float | None = None
        self.first_visible_delta_ms: float | None = None

    def record(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return

        elapsed_ms: float | None = None

        def _event_elapsed_ms() -> float:
            nonlocal elapsed_ms
            if elapsed_ms is None:
                elapsed_ms = self._elapsed_ms()
            return elapsed_ms

        if self.first_event_ms is None:
            self.first_event_ms = _event_elapsed_ms()

        event_type = event.get("type") or event.get("event")
        if event_type != "message.delta":
            return

        delta = event.get("delta")
        if not isinstance(delta, str) or not delta:
            return

        if self.first_delta_ms is None:
            self.first_delta_ms = _event_elapsed_ms()

        if self.first_visible_delta_ms is None and self._sanitizer.filter_delta(delta):
            self.first_visible_delta_ms = _event_elapsed_ms()


def format_latency_ms(value: float | None) -> str:
    return "none" if value is None else f"{value:.1f}"


def strip_thinking_blocks(text: str) -> str:
    return HermesEventSanitizer().filter_delta(text)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def is_tool_result_content(text: str) -> bool:
    parsed = _parse_json_object(text)
    if parsed is None:
        return False
    return (
        ("output" in parsed and ("exit_code" in parsed or "approval" in parsed or "error" in parsed))
        or ("exit_code" in parsed and ("stdout" in parsed or "stderr" in parsed))
    )


def is_processing_prelude_content(text: str) -> bool:
    normalized = " ".join(text.strip().split()).lower()
    if not normalized:
        return True
    prelude_prefixes = (
        "let me check",
        "i'll check",
        "i will check",
        "i’m going to check",
        "i am going to check",
        "checking ",
        "好的，我来查",
        "好的老板，我查",
        "我来查一下",
        "我先查一下",
        "先查一下",
        "查一下",
    )
    return any(normalized.startswith(prefix) for prefix in prelude_prefixes)


def sanitize_hermes_message(message: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(message)
    if sanitized.get("role") == "assistant":
        content = sanitized.get("content")
        if isinstance(content, str):
            sanitized["content"] = strip_thinking_blocks(content)
        sanitized.pop("reasoning", None)
    return sanitized


def _is_verification_stop_nudge(message: dict) -> bool:
    """Detect hermes' internal turn-end verification nudge.

    When ``verify_on_stop`` is enabled on a conversational surface, hermes
    injects a synthetic ``user`` message starting with ``[System: You edited
    code in this turn...`` as a follow-up forcing the model to verify edits
    before answering. That text is engine-internal guidance and must never be
    shown to end users. We drop it both by its synthetic marker (if persisted)
    and by a content signature that is unique to this nudge.
    """
    if not isinstance(message, dict):
        return False
    if message.get("_verification_stop_synthetic"):
        return True
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        return False
    return "fresh passing verification evidence yet" in text


def sanitize_hermes_messages(messages: list[Any]) -> list[Any]:
    sanitized_messages: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            sanitized_messages.append(message)
            continue
        if _is_verification_stop_nudge(message):
            continue
        sanitized = sanitize_hermes_message(message)
        content = sanitized.get("content")
        if sanitized.get("role") == "assistant" and isinstance(content, str) and is_tool_result_content(content):
            continue
        sanitized_messages.append(sanitized)
    return sanitized_messages


def sanitize_run_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitizer = HermesEventSanitizer()
    sanitized_events: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        sanitized = sanitizer.sanitize_event(event)
        if sanitized is not None:
            sanitized_events.append(sanitized)
    return sanitized_events


def sanitize_sse_block(raw_event: str, sanitizer: HermesEventSanitizer) -> str | None:
    data_lines = [line[5:].lstrip() for line in raw_event.splitlines() if line.startswith("data:")]
    if not data_lines:
        return None
    data = "\n".join(data_lines)
    if data == "[DONE]":
        return "data: [DONE]\n\n"
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return f"{raw_event}\n\n"
    if not isinstance(parsed, dict):
        return f"{raw_event}\n\n"
    sanitized = sanitizer.sanitize_event(parsed)
    if sanitized is None:
        return None
    return f"data: {json.dumps(sanitized, ensure_ascii=False, separators=(',', ':'))}\n\n"


def summarize_run_events(events: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    final_message: dict[str, Any] = {}
    status_text = "pending"

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "message.completed" and isinstance(event.get("message"), dict):
            candidate = sanitize_hermes_message(event["message"])
            content = candidate.get("content")
            if not (isinstance(content, str) and is_tool_result_content(content)):
                final_message = candidate
        if event_type == "run.completed":
            status_text = "completed"
            if not final_message:
                output = event.get("output")
                if isinstance(output, str) and output:
                    final_message = {"role": "assistant", "content": strip_thinking_blocks(output)}
                elif isinstance(output, dict):
                    content = output.get("content")
                    if isinstance(content, str) and content:
                        final_message = {"role": "assistant", "content": strip_thinking_blocks(content)}
        elif event_type == "run.failed":
            status_text = "failed"
            # Surface the underlying error so the client can show a meaningful
            # message instead of a generic "Agent 执行出错". The error text is
            # produced by the hermes runtime (e.g. "No provider configured for
            # model 'X'" or an upstream LLM/auth failure).
            error_text = event.get("error")
            if isinstance(error_text, str) and error_text.strip():
                final_message = {"role": "system", "content": error_text.strip()}

    return status_text, final_message
