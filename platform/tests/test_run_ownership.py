from types import SimpleNamespace

import pytest
from app.runtime.run_ownership import ensure_runtime_run_owned, record_runtime_run
from fastapi import HTTPException


class FakeResult:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class FakeDb:
    def __init__(self, record=None):
        self.record = record
        self.added = []
        self.committed = False
        self.refreshed = []

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.record)

    def add(self, record):
        self.added.append(record)
        self.record = record

    async def commit(self):
        self.committed = True

    async def refresh(self, record):
        self.refreshed.append(record)


@pytest.mark.asyncio
async def test_record_runtime_run_creates_mapping():
    db = FakeDb()

    record = await record_runtime_run(
        db,
        run_id="run-123",
        user_id="user-1",
        session_key="agent:usr_1:session-1",
        runtime_mode="shared",
        backend="hermes",
    )

    assert record is db.record
    assert record.run_id == "run-123"
    assert record.user_id == "user-1"
    assert record.session_key == "agent:usr_1:session-1"
    assert record.runtime_mode == "shared"
    assert record.backend == "hermes"
    assert db.added == [record]
    assert db.committed is True
    assert db.refreshed == [record]


@pytest.mark.asyncio
async def test_record_runtime_run_ignores_empty_run_id():
    db = FakeDb()

    record = await record_runtime_run(
        db,
        run_id=" ",
        user_id="user-1",
        session_key="agent:usr_1:session-1",
        runtime_mode="shared",
        backend="hermes",
    )

    assert record is None
    assert db.added == []
    assert db.committed is False


@pytest.mark.asyncio
async def test_ensure_runtime_run_owned_accepts_matching_owner():
    existing = SimpleNamespace(
        run_id="run-123",
        user_id="user-1",
        session_key="agent:usr_1:session-1",
        runtime_mode="shared",
        backend="hermes",
    )
    db = FakeDb(existing)

    record = await ensure_runtime_run_owned(
        db,
        run_id="run-123",
        user_id="user-1",
        runtime_mode="shared",
        backend="hermes",
    )

    assert record is existing


@pytest.mark.asyncio
async def test_ensure_runtime_run_owned_rejects_missing_run():
    db = FakeDb()

    with pytest.raises(HTTPException) as exc:
        await ensure_runtime_run_owned(
            db,
            run_id="run-missing",
            user_id="user-1",
            runtime_mode="shared",
            backend="hermes",
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_ensure_runtime_run_owned_rejects_other_user():
    existing = SimpleNamespace(
        run_id="run-123",
        user_id="user-2",
        session_key="agent:usr_2:session-1",
        runtime_mode="shared",
        backend="hermes",
    )
    db = FakeDb(existing)

    with pytest.raises(HTTPException) as exc:
        await ensure_runtime_run_owned(
            db,
            run_id="run-123",
            user_id="user-1",
            runtime_mode="shared",
            backend="hermes",
        )

    assert exc.value.status_code == 403
