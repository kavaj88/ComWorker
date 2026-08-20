from app.config import Settings


def test_settings_expose_dedicated_runtime_selector_fields():
    settings = Settings()

    assert settings.hermes_image == "comworker-hermes-agent:latest"
    assert settings.dedicated_runtime_backend == "hermes"
    assert settings.dedicated_hermes_url == ""
    assert settings.dedicated_runtime_container_name_prefix == "hermes-user"
    assert settings.dedicated_runtime_data_volume_prefix == "hermes-data"
    assert settings.hermes_connect_retries == 60
    assert settings.hermes_retry_delay_seconds == 0.5
    assert settings.hermes_reasoning_effort == "none"
    assert settings.hermes_service_tier == ""
    assert settings.minimax_m27_use_highspeed is True


def test_settings_expose_shared_runtime_selector_fields():
    settings = Settings()

    assert settings.shared_runtime_backend == "hermes"
    assert settings.shared_hermes_url == "http://shared-comworker:8080"
    assert settings.shared_hermes_api_key == "dev-hermes-bridge-key"


def test_settings_expose_disabled_training_trace_defaults():
    settings = Settings()

    assert settings.training_trace_enabled is False
    assert settings.training_trace_ingest_enabled is False
    assert settings.training_trace_ingest_token == ""
    assert settings.training_trace_dir == ".hermes/training_traces"
    assert settings.training_trace_hash_salt == ""
