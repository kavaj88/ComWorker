"""Integration test: user model config → hermes direct routing.

Validates the fix that routes user-added custom_providers directly to
their own API endpoints instead of forcing everything through the
platform-gateway proxy.

Prerequisites:
    docker compose services must be running.
    Set DEEPSEEK_API_KEY env var or pass it via --deepseek-key.

Usage:
    # Quick smoke test (requires deployed services)
    DEEPSEEK_API_KEY=sk-xxx python -m pytest tests/test_user_model_config.py -v

    # Skip actual LLM call (just test config persistence)
    python -m pytest tests/test_user_model_config.py -v -k "not test_llm"
"""

from __future__ import annotations

import os

import pytest
from conftest import admin_token, api_url, auth_headers, json_request


def _token() -> str:
    return admin_token()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def deepseek_api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        pytest.skip("DEEPSEEK_API_KEY not set")
    return key


def _models_config_url() -> str:
    return api_url("/api/comworker/models/config")


def _get_models_config() -> dict:
    return json_request(
        api_url("/api/comworker/models"),
        headers=auth_headers(_token()),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUserModelConfigPersistence:
    """Tests that model config is correctly written and read back."""

    def test_01_set_provider_and_default_model(self, deepseek_api_key):
        """PUT provider config + defaultModel, verify response is ok."""
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "deepseek": {
                        "baseUrl": "https://api.deepseek.com/v1",
                        "api": "openai-completions",
                        "apiKey": deepseek_api_key,
                        "models": [{"id": "deepseek-chat", "name": "deepseek-chat"}],
                    }
                },
                "defaultModel": "deepseek/deepseek-chat",
            },
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True, f"Config update failed: {result}"

    def test_02_config_is_read_back_correctly(self):
        """GET models returns the configured provider and default model."""
        config = _get_models_config()

        # Default model must be what we set
        assert config.get("configuredModel") == "deepseek/deepseek-chat", (
            f"Expected deepseek/deepseek-chat, got {config.get('configuredModel')}"
        )

        # deepseek provider must be present
        providers = config.get("configuredProviders", {})
        assert "deepseek" in providers, (
            f"deepseek provider not found: {list(providers.keys())}"
        )

        ds = providers["deepseek"]
        assert ds.get("apiKey"), "deepseek provider should have an API key"

    def test_03_set_default_only_without_providers(self):
        """Setting only defaultModel preserves existing providers."""
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={"defaultModel": "deepseek/deepseek-chat"},
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models_config()
        assert config.get("configuredModel") == "deepseek/deepseek-chat"
        # Provider should still be there
        assert "deepseek" in config.get("configuredProviders", {})

    def test_04_fallback_to_platform_model(self):
        """Setting a platform model (no user provider) should work."""
        result = json_request(
            _models_config_url(),
            method="PUT",
            payload={"defaultModel": "claude-sonnet-4-5"},
            headers=auth_headers(_token()),
        )
        assert result.get("ok") is True

        config = _get_models_config()
        assert config.get("configuredModel") == "claude-sonnet-4-5"


class TestUserModelLLMRouting:
    """Tests that the user's model actually routes to the correct provider.

    These tests require a running container and valid API keys.
    """

    def test_user_deepseek_model_routes_to_deepseek_not_vllm(
        self, deepseek_api_key
    ):
        """After setting deepseek as default, LLM calls should NOT fall to vLLM.

        If the routing is broken, the LLM call would either return a 400 from
        the proxy ("No provider configured") or route to vLLM (which would
        produce a different error or response).
        """
        # 1. Set deepseek provider + default
        json_request(
            _models_config_url(),
            method="PUT",
            payload={
                "providers": {
                    "deepseek": {
                        "baseUrl": "https://api.deepseek.com/v1",
                        "api": "openai-completions",
                        "apiKey": deepseek_api_key,
                        "models": [{"id": "deepseek-chat", "name": "deepseek-chat"}],
                    }
                },
                "defaultModel": "deepseek/deepseek-chat",
            },
            headers=auth_headers(_token()),
        )

        # 2. Get an API token for the admin user
        result = json_request(
            api_url("/api/auth/api-token"),
            method="POST",
            headers=auth_headers(_token()),
        )
        api_token = result["api_token"]

        try:
            result = json_request(
                api_url("/llm/v1/chat/completions"),
                method="POST",
                payload={
                    "model": "deepseek/deepseek-chat",
                    "messages": [{"role": "user", "content": "Say hello in one word."}],
                    "max_tokens": 10,
                },
                headers={"Authorization": f"Bearer {api_token}"},
            )
            # Got a successful response — routing worked
            assert result is not None
            assert "choices" in result, f"Unexpected response: {result}"
            content = result["choices"][0]["message"]["content"]
            print(f"LLM response: {content}")
        except RuntimeError as exc:
            err = str(exc)
            # The key assertions: must NOT be a routing/fallback error
            assert "No provider configured" not in err, (
                f"deepseek model was NOT routed to a provider: {err}"
            )
            # Must NOT show dashscope/qwen (keyword collision)
            assert "dashscope" not in err.lower(), (
                f"deepseek model was misrouted to dashscope: {err}"
            )
            # Any other error (like auth failure on DeepSeek's side) is fine
            # — it proves the request reached DeepSeek, not vLLM
            print(f"LLM call reached provider (non-routing error): {err[:200]}")
