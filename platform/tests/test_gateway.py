"""验证独立模型网关的透传与权限边界。"""

import json

from app.gateway.protocol import UsageCapture, forwarding_headers, upstream_url


def test_url_preserves_provider_prefix():
    assert (
        upstream_url("https://vendor.test/api/v3", "responses")
        == "https://vendor.test/api/v3/responses"
    )
    assert upstream_url("https://vendor.test/v1/", "messages") == "https://vendor.test/v1/messages"


def test_headers_remove_credentials_and_connection_named_headers():
    headers = forwarding_headers(
        {
            "authorization": "Bearer private",
            "x-api-key": "private",
            "connection": "x-secret",
            "x-secret": "hidden",
            "anthropic-beta": "feature",
        }
    )
    assert headers == {"anthropic-beta": "feature"}


def test_usage_sse_across_arbitrary_chunks():
    raw = (
        'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":0}}}\n\n'
        'data: {"type":"message_delta","usage":{"output_tokens":9}}\n\n'
    ).encode()
    capture = UsageCapture(stream=True, retain=False)
    for i in range(0, len(raw), 3):
        capture.feed(raw[i : i + 3])
    capture.finish()
    assert capture.usage == {"input_tokens": 12, "output_tokens": 9}
    assert capture.content is None


def test_missing_usage_is_unknown():
    capture = UsageCapture(stream=False, retain=True)
    capture.feed(b'{"error":{"message":"failed"}}')
    capture.finish()
    assert capture.usage is None


def test_responses_usage_and_unicode_content():
    capture = UsageCapture(stream=True, retain=True)
    raw = (
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {"usage": {"input_tokens": 8, "output_tokens": 4}},
            }
        )
        + "\n\n"
    ).encode()
    capture.feed(raw)
    capture.finish()
    assert capture.usage["output_tokens"] == 4
    assert capture.content == raw.decode()


def test_multiline_sse_and_oversized_event_recovery():
    from app.gateway.protocol import PARSE_LIMIT

    capture = UsageCapture(stream=True, retain=False)
    capture.feed(b'data: ' + b'x' * (PARSE_LIMIT + 1) + b'\n\n')
    raw = b'data: {"usage":\r\ndata: {"input_tokens":3,"output_tokens":2}}\r\n\r\n'
    for char in raw:
        capture.feed(bytes([char]))
    capture.finish()
    assert capture.usage == {"input_tokens": 3, "output_tokens": 2}


def test_content_is_bounded_and_anthropic_base_path():
    from app.gateway.protocol import CONTENT_LIMIT

    capture = UsageCapture(stream=False, retain=True)
    capture.feed(b"x" * (CONTENT_LIMIT + 10))
    assert capture.truncated and len(capture.content) == CONTENT_LIMIT
    assert upstream_url("https://vendor.test/anthropic", "messages/count_tokens") == "https://vendor.test/anthropic/v1/messages/count_tokens"


def test_error_event_is_detected_without_altering_stream():
    capture = UsageCapture(stream=True, retain=False)
    capture.feed(b'data: {"type":"error","error":{"type":"overloaded_error"}}\n\n')
    capture.finish()
    assert capture.failed
