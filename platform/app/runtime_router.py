from __future__ import annotations

from app.config import Settings, settings
from app.runtime_backend import RuntimeBackend

_dedicated_backends: dict[str, RuntimeBackend] = {}


def _load_backend(backend_name: str) -> RuntimeBackend:
    normalized = (backend_name or "hermes").strip().lower()

    if normalized not in _dedicated_backends:
        if normalized == "hermes":
            from app.runtime_backends.dedicated_hermes import DedicatedHermesBackend
            _dedicated_backends[normalized] = DedicatedHermesBackend()
        else:
            raise ValueError(f"Unsupported runtime backend: {backend_name}")
    return _dedicated_backends[normalized]


def get_runtime_backend(runtime_settings: Settings | None = None) -> RuntimeBackend:
    runtime_settings = runtime_settings or settings
    return _load_backend(runtime_settings.dedicated_runtime_backend)


async def close_runtime_backends() -> None:
    for backend in _dedicated_backends.values():
        close = getattr(backend, "aclose", None)
        if close is not None:
            await close()
    _dedicated_backends.clear()
