from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Request, UploadFile

from app.db.models import User


@dataclass(slots=True)
class RuntimeContext:
    user: User
    scope: str


class RuntimeBackend(Protocol):
    async def prewarm(self, ctx: RuntimeContext) -> dict | list | str: ...

    async def get_agent_info(self, ctx: RuntimeContext) -> dict: ...

    async def list_skills(self, ctx: RuntimeContext) -> list[dict]: ...

    async def list_sessions(self, ctx: RuntimeContext) -> list[dict]: ...

    async def get_session(self, ctx: RuntimeContext, session_key: str) -> dict | list | str: ...

    async def send_message(self, ctx: RuntimeContext, session_key: str, message: str, model: str | None = None) -> dict: ...

    async def wait_run(self, ctx: RuntimeContext, run_id: str, timeout_ms: int) -> dict | list | str: ...

    async def respond_run_approval(
        self,
        ctx: RuntimeContext,
        run_id: str,
        choice: str,
        resolve_all: bool = False,
    ) -> dict | list | str: ...

    async def rename_session(self, ctx: RuntimeContext, session_key: str, title: str) -> dict | list | str: ...

    async def delete_session(self, ctx: RuntimeContext, session_key: str) -> dict | list | str: ...

    async def upload_file(
        self,
        ctx: RuntimeContext,
        file: UploadFile,
        target_dir: str | None = None,
    ) -> dict: ...

    async def stream_events(self, ctx: RuntimeContext, request: Request, token: str): ...

    async def stream_run_events(self, ctx: RuntimeContext, request: Request, token: str, run_id: str): ...
