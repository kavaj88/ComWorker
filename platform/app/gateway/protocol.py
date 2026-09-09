"""仅解析路由和用量；响应字节由代理原样转发。"""

import json
from urllib.parse import urlsplit

CONTENT_LIMIT = 256 * 1024
PARSE_LIMIT = 2 * 1024 * 1024
PROTOCOLS = {"chat_completions", "responses", "anthropic_messages"}
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
PRIVATE_HEADERS = {
    "authorization",
    "x-api-key",
    "cookie",
    "set-cookie",
    "x-auth-token",
    "api-key",
    "x-goog-api-key",
    "openai-organization",
    "openai-project",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
}


def forwarding_headers(headers) -> dict[str, str]:
    normalized = {k.lower(): v for k, v in headers.items()}
    blocked = (
        HOP_HEADERS
        | PRIVATE_HEADERS
        | {value.strip().lower() for value in normalized.get("connection", "").split(",")}
    )
    return {k: v for k, v in normalized.items() if k not in blocked}


def upstream_url(base: str, endpoint: str) -> str:
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("供应商 Base URL 必须是无凭据、无查询参数的 HTTP(S) 地址")
    base = base.rstrip("/")
    # Anthropic SDK 将 Base URL 视为根目录，Messages 路径自带 /v1。
    if endpoint.startswith("messages") and not base.endswith("/v1"):
        base += "/v1"
    return base + "/" + endpoint


def provider_base(provider):
    return provider.api_base or {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }.get(provider.provider_type, "")


def provider_protocols(provider, override=None) -> list[str]:
    if override is not None:
        return override.protocols
    mode = (provider.api_mode or provider.provider_type or "").lower().replace("-", "_")
    if mode in {"anthropic", "anthropic_messages"}:
        return ["anthropic_messages"]
    if mode in {"responses", "openai_responses"}:
        return ["responses"]
    if mode in {"google", "google_ai", "gemini"}:
        return []
    return ["chat_completions"]


def token_counts(usage):
    if not isinstance(usage, dict):
        return None, None

    def count(*keys):
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    return count("input_tokens", "prompt_tokens"), count("output_tokens", "completion_tokens")


class UsageCapture:
    def __init__(self, *, stream: bool, retain: bool):
        self.stream = stream
        self.retain = retain
        self.usage = None
        self.failed = False
        self.truncated = False
        self._content = bytearray()
        self._buffer = bytearray()
        self._discard = False
        self._event = bytearray()
        self._event_discard = False

    @property
    def content(self):
        return self._content.decode("utf-8", errors="replace") if self.retain else None

    def feed(self, chunk: bytes):
        if self.retain:
            remaining = CONTENT_LIMIT - len(self._content)
            self._content.extend(chunk[:remaining])
            self.truncated |= len(chunk) > remaining
        if not self.stream:
            if len(self._buffer) + len(chunk) <= PARSE_LIMIT:
                self._buffer.extend(chunk)
            else:
                self._buffer.clear()
                self._discard = True
            return
        # SSE 按行解析，跨任意网络分片；超大行丢弃至下一换行。
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            ends = index < len(parts) - 1
            if not self._discard:
                if len(self._buffer) + len(part) > PARSE_LIMIT:
                    self._discard = True
                    self._buffer.clear()
                else:
                    self._buffer.extend(part)
            if ends:
                if not self._discard:
                    line = bytes(self._buffer).rstrip(b"\r")
                    if not line:
                        self._finish_event()
                    elif line.startswith(b"data:") and not self._event_discard:
                        if len(self._event) + len(line) > PARSE_LIMIT:
                            self._event.clear()
                            self._event_discard = True
                        else:
                            self._event.extend(line[5:].lstrip(b" ") + b"\n")
                else:
                    self._event_discard = True
                self._buffer.clear()
                self._discard = False

    def _finish_event(self):
        if self._event and not self._event_discard:
            self._extract(bytes(self._event))
        self._event.clear()
        self._event_discard = False

    def _extract(self, raw):
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict):
            return
        if obj.get("error") or obj.get("type") in {"error", "response.failed"}:
            self.failed = True
        usage = obj.get("usage")
        if set(obj) == {"input_tokens"}:
            usage = obj
        for key in ("message", "response"):
            if isinstance(obj.get(key), dict) and isinstance(obj[key].get("usage"), dict):
                usage = obj[key]["usage"]
        if isinstance(usage, dict):
            self.usage = {**(self.usage or {}), **usage}

    def finish(self):
        if not self._discard and not self.stream:
            self._extract(bytes(self._buffer))
        elif self.stream:
            if self._buffer.startswith(b"data:") and not self._discard:
                self._event.extend(self._buffer[5:].strip())
            self._finish_event()
        self._buffer.clear()
