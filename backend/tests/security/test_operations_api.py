"""The operations report follows the caller's workspaces; install-wide figures are for organisation owners only."""

from __future__ import annotations

import pytest

from patchquest.database import get_db
from patchquest.persistence import ledger
from patchquest.persistence import plugins as plugin_store


@pytest.mark.asyncio
async def test_each_tenant_sees_only_its_own_runs_and_recoveries(world):
    with get_db() as conn:
        ledger.append(conn, world.runs["a"], "run_interrupted", actor="recovery")
        ledger.append(conn, world.runs["a"], "run_interrupted", actor="recovery")
        ledger.append(conn, world.runs["b"], "run_interrupted", actor="recovery")
    async with world.client("viewer_a") as c:
        a = (await c.get("/api/metrics/operations")).json()
    async with world.client("viewer_b") as c:
        b = (await c.get("/api/metrics/operations")).json()
    assert (a["runs"], a["workers"]["recoveries"]) == (1, 2) and (b["runs"], b["workers"]["recoveries"]) == (1, 1)
    assert a["workers"]["queue_now"] is None  # the shared queue is not a tenant's to see


@pytest.mark.asyncio
async def test_plugin_figures_are_only_for_owners(world):
    with get_db() as conn:
        plugin_store.event(conn, "tool", "invoked", capability="x", duration_ms=5)
    for who, expected in (("owner_a", True), ("admin_a", False), ("dev_a", False), ("viewer_a", False)):
        async with world.client(who) as c:
            body = (await c.get("/api/metrics/operations")).json()
        assert ("plugins" in body) is expected, who
    async with world.client() as c:
        assert (await c.get("/api/metrics/operations")).status_code == 401
    async with world.client("owner_a") as c:
        assert (await c.get("/api/metrics/operations", params={"window": "soon"})).status_code == 422
