"""Tests for GET /api/ping — platform health-check endpoint."""

from conftest import api_url, json_request


def test_ping_returns_pong():
    """GET /api/ping should return pong message without auth."""
    result = json_request(api_url("/api/ping"))
    assert result["message"] == "pong"
    assert result["service"] == "comworker-platform"


def test_ping_no_auth_required():
    """Ping endpoint should work without any auth headers."""
    result = json_request(api_url("/api/ping"))
    assert "message" in result
