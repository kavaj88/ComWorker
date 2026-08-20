import logging

from app.logging_setup import setup_logging


def test_setup_logging_silences_litellm_loggers():
    setup_logging()

    assert logging.getLogger("litellm").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("LiteLLM").getEffectiveLevel() == logging.WARNING
