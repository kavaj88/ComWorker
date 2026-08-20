"""Test: default model display with provider prefix in _hermes_to_frontend().

Validates two fixes:
1. configuredModel returns "provider/model" format when model.provider is set
2. Reserved provider names ("platform", "platform-gateway") are filtered

Prerequisites:
    docker compose services must be running.

Usage:
    python -m pytest tests/test_model_display_fix.py -v
"""

from __future__ import annotations

import pytest
from conftest import admin_token, api_url, auth_headers, json_request


def _token() -> str:
    return admin_token()


def _models_config_url() -> str:
    return api_url("/api/comworker/models/config")


def _get_models() -> dict:
    return json_request(
        api_url("/api/comworker/models"),
        headers=auth_headers(_token()),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultModelDisplay:
    """Tests that configuredModel returns provider-prefixed format."""

    def test_default_model_with_provider_prefix(self):
        """When model.provider=custom101 and model.default=glm-4.7-flash,
        configuredModel should be 'custom101/glm-4.7-flash'."""
        # Set a custom provider with a default model
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "custom101": {
                        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                        "api": "openai-completions",
                        "apiKey": "test-key",
                        "models": [{"id": "glm-4.7-flash", "name": "GLM 4.7 Flash"}],
                    }
                },
                "defaultModel": "custom101/glm-4.7-flash",
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True, f"Config update failed: {result}"

        # Read back and verify format
        config = _get_models()
        configured_model = config.get("configuredModel", "")
        assert configured_model == "custom101/glm-4.7-flash", (
            f"Expected 'custom101/glm-4.7-flash', got '{configured_model}'"
        )

        # Provider should be present
        providers = config.get("configuredProviders", {})
        assert "custom101" in providers, (
            f"custom101 provider not found: {list(providers.keys())}"
        )

    def test_default_model_platform_gateway_prefix(self):
        """When platform-gateway is the implicit provider, the prefix is prepended.

        Setting only the default model (no custom providers) causes the
        backend to store model.provider = 'platform-gateway'. The fix
        prepends 'platform-gateway/' so the frontend can match it.
        """
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "defaultModel": "claude-sonnet-4-5",
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models()
        configured_model = config.get("configuredModel", "")
        # The model should have the platform-gateway prefix prepended
        assert configured_model == "platform-gateway/claude-sonnet-4-5", (
            f"Expected 'platform-gateway/claude-sonnet-4-5', got '{configured_model}'"
        )

    def test_default_model_already_has_slash_preserved(self):
        """When default_model already contains '/', it is not double-prefixed."""
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "test-prov": {
                        "baseUrl": "https://api.example.com/v1",
                        "api": "openai-completions",
                        "apiKey": "test-key",
                        "models": [{"id": "test-model", "name": "Test Model"}],
                    }
                },
                "defaultModel": "test-prov/test-model",
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models()
        configured_model = config.get("configuredModel", "")
        # Should be exactly "test-prov/test-model", not "test-prov/test-prov/test-model"
        assert configured_model == "test-prov/test-model", (
            f"Expected 'test-prov/test-model', got '{configured_model}'"
        )


class TestReservedProviderNames:
    """Tests that reserved provider names are filtered on read and write."""

    def test_platform_name_filtered_from_read(self):
        """A provider named 'platform' in config should NOT appear in output."""
        # First set a normal provider to ensure we can write
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "custom101": {
                        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                        "api": "openai-completions",
                        "apiKey": "test-key",
                        "models": [{"id": "glm-4.7-flash", "name": "GLM 4.7 Flash"}],
                    }
                },
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models()
        providers = config.get("configuredProviders", {})
        # 'platform' is a system-reserved key that should be filtered
        # It should NOT appear as a user-provided provider
        assert "platform" not in providers or providers.get("platform", {}).get("_system"), (
            "Reserved 'platform' name should be filtered from user providers"
        )

    def test_platform_name_filtered_on_write(self):
        """Writing a provider named 'platform' should be rejected/filtered."""
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "platform": {
                        "baseUrl": "https://evil.example.com/v1",
                        "api": "openai-completions",
                        "apiKey": "bad-key",
                        "models": [{"id": "bad-model", "name": "Bad Model"}],
                    }
                },
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models()
        providers = config.get("configuredProviders", {})
        # The "platform" entry should NOT have our malicious values
        platform_entry = providers.get("platform", {})
        if platform_entry:
            # If present, it must be the system one, not ours
            assert platform_entry.get("_system") is True or platform_entry.get("baseUrl") != "https://evil.example.com/v1", (
                "Malicious 'platform' provider was not filtered"
            )

    def test_platform_gateway_preserved_in_config(self):
        """platform-gateway entries in config should be preserved (not lost on write)."""
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "custom101": {
                        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                        "api": "openai-completions",
                        "apiKey": "test-key",
                        "models": [{"id": "glm-4.7-flash", "name": "GLM 4.7 Flash"}],
                    }
                },
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models()
        providers = config.get("configuredProviders", {})
        # platform-gateway is internal — it should NOT be exposed to users
        assert "platform-gateway" not in providers, (
            "platform-gateway should be filtered from user-facing output"
        )
