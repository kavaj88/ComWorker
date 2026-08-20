"""Run identifier helpers for platform compatibility."""

from uuid import uuid4


RUN_PREFIX = "run_"


def normalize_platform_run_id(run_id: str | None) -> str:
    if run_id:
        return run_id
    return f"{RUN_PREFIX}{uuid4().hex}"
