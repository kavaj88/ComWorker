"""Tests for the capability admin routes (bulk apply)."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

# Stub docker + asyncpg so platform modules import without a daemon.
if "docker" not in sys.modules:
    _docker = types.ModuleType("docker")
    _docker.DockerClient = object
    _docker.from_env = lambda: None
    _docker.models = types.SimpleNamespace(containers=types.SimpleNamespace(Container=object))
    _docker.types = types.SimpleNamespace(Mount=lambda *a, **k: None)
    sys.modules["docker"] = _docker
    _docker_errors = types.ModuleType("docker.errors")
    _docker_errors.APIError = RuntimeError
    _docker_errors.NotFound = RuntimeError
    sys.modules["docker.errors"] = _docker_errors
if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = types.ModuleType("asyncpg")

from fastapi import HTTPException  # noqa: E402

from app.routes.admin import (  # noqa: E402
    BulkCapabilityRequest,
    BulkCapabilityTarget,
    bulk_set_capability,
)


class _FakeResult:
    def __init__(self, ids):
        self._ids = ids

    def scalars(self):
        ids = self._ids

        class _S:
            def all(self):
                return ids

        return _S()


class FakeDB:
    def __init__(self, ids=()):
        self._ids = list(ids)
        self.executed = []
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._ids)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True


ADMIN = SimpleNamespace(id="admin-id", username="admin")


@pytest.mark.asyncio
async def test_bulk_user_ids_upserts_in_single_operation():
    db = FakeDB()
    req = BulkCapabilityRequest(
        capability="web_search",
        enabled=True,
        target=BulkCapabilityTarget(user_ids=["u1", "u2"]),
    )
    res = await bulk_set_capability(req, db=db, admin_user=ADMIN)
    assert res == {"ok": True, "affected": 2}
    # user_ids path performs no SELECT — only the single bulk upsert executes.
    assert len(db.executed) == 1
    assert db.committed is True


@pytest.mark.asyncio
async def test_bulk_all_resolves_via_select_then_single_upsert():
    db = FakeDB(ids=["u1", "u2", "u3"])
    req = BulkCapabilityRequest(
        capability="web_search",
        enabled=False,
        target=BulkCapabilityTarget(all=True),
    )
    res = await bulk_set_capability(req, db=db, admin_user=ADMIN)
    assert res["affected"] == 3
    # One SELECT to resolve ids + one bulk upsert.
    assert len(db.executed) == 2
    assert db.committed is True


@pytest.mark.asyncio
async def test_bulk_all_with_no_users_returns_zero_without_upsert():
    db = FakeDB(ids=[])
    req = BulkCapabilityRequest(
        capability="web_search",
        enabled=True,
        target=BulkCapabilityTarget(all=True),
    )
    res = await bulk_set_capability(req, db=db, admin_user=ADMIN)
    assert res == {"ok": True, "affected": 0}
    # SELECT ran, but no upsert and no commit (early return).
    assert len(db.executed) == 1
    assert db.committed is False


@pytest.mark.asyncio
async def test_bulk_unknown_capability_rejected():
    db = FakeDB()
    req = BulkCapabilityRequest(
        capability="nope",
        enabled=True,
        target=BulkCapabilityTarget(all=True),
    )
    with pytest.raises(HTTPException) as exc:
        await bulk_set_capability(req, db=db, admin_user=ADMIN)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_no_target_rejected():
    db = FakeDB()
    req = BulkCapabilityRequest(
        capability="web_search",
        enabled=True,
        target=BulkCapabilityTarget(),
    )
    with pytest.raises(HTTPException) as exc:
        await bulk_set_capability(req, db=db, admin_user=ADMIN)
    assert exc.value.status_code == 400
