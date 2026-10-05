"""PostgreSQL (team / server mode): the properties that matter when more than one process shares the database.

Run with ``PATCHQUEST_TEST_PG=postgresql://user@host:port/db``; skipped otherwise. The whole suite also runs on
PostgreSQL under that variable - these tests are the ones that only make sense there.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from patchquest import database
from patchquest.database import get_db
from patchquest.domain.failures import FailureKind, classify
from patchquest.domain.runs import RunStatus
from patchquest.persistence import ledger
from patchquest.persistence.runs import transition
from patchquest.runtime import queue
from tests.support.db import insert_run

pytestmark = pytest.mark.skipif(not os.environ.get("PATCHQUEST_TEST_PG"), reason="needs PATCHQUEST_TEST_PG")


def enqueue(n: int) -> list[str]:
    ids = []
    for i in range(n):
        rid = f"run-{i:03d}-{uuid.uuid4().hex[:6]}"
        insert_run(rid, status="created")
        with get_db() as conn:
            queue.enqueue(conn, rid, actor="test")
        ids.append(rid)
    return ids


def test_the_backend_is_postgres_and_dialect_is_visible():
    assert database.is_postgres()
    with get_db() as conn:
        assert conn.dialect == "postgresql"
        assert conn.execute("SELECT current_schema()").fetchone()[0].startswith("t_")


def test_concurrent_workers_never_receive_the_same_run():
    run_ids = enqueue(40)
    claimed: list[str] = []
    guard = threading.Lock()
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            while (c := queue.claim(name, 30.0)) is not None:
                assert c.kind == "start"
                with guard:
                    claimed.append(c.run_id)
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert not errors, errors
    assert sorted(claimed) == sorted(run_ids)  # every run claimed, none twice
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'running' AND lease_owner IS NOT NULL").fetchone()[0] == 40
        assert conn.execute("SELECT MAX(lease_epoch) FROM runs").fetchone()[0] == 1


def test_one_dead_workers_run_is_recovered_by_exactly_one_other_worker():
    [rid] = enqueue(1)
    first = queue.claim("dead-worker", 1.0, now=datetime.now(UTC) - timedelta(minutes=5))  # its lease is long expired
    assert first is not None and first.run_id == rid
    results: list[queue.Claim | None] = []
    guard = threading.Lock()

    def recover(name: str) -> None:
        c = queue.claim(name, 30.0)
        with guard:
            results.append(c)

    threads = [threading.Thread(target=recover, args=(f"rescuer-{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    recovered = [c for c in results if c is not None]
    assert [c.kind for c in recovered] == ["recover"] and recovered[0].run_id == rid
    with get_db() as conn:
        assert conn.execute("SELECT status FROM runs WHERE id = ?", (rid,)).fetchone()[0] == "interrupted"
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ? AND type = 'run_interrupted'", (rid,)).fetchone()[0] == 1


def test_a_heartbeat_from_a_worker_that_lost_its_lease_is_refused():
    [rid] = enqueue(1)
    old = queue.claim("slow", 1.0, now=datetime.now(UTC) - timedelta(minutes=5))
    assert old is not None
    assert queue.claim("other", 30.0).kind == "recover"
    with pytest.raises(queue.LeaseLost):
        queue.heartbeat(rid, "slow", old.epoch, 30.0)


def test_events_appended_concurrently_to_one_run_are_never_skipped_by_a_reader_following_the_cursor():
    """PostgreSQL assigns ids before commit; without per-run turn-taking a reader could pass an id that commits later."""
    insert_run("busy")
    stop = threading.Event()
    seen: list[int] = []

    def reader() -> None:
        cursor = 0
        while not stop.is_set() or True:
            with get_db() as conn:
                rows = ledger.read(conn, "busy", after=cursor, limit=500)
            for r in rows:
                seen.append(r["id"])
                cursor = r["id"]
            if stop.is_set() and not rows:
                return
            time.sleep(0.001)

    def writer(n: int) -> None:
        for i in range(40):
            with get_db() as conn:
                ledger.append(conn, "busy", "tick", message=f"{n}:{i}")
                time.sleep(0.002)  # keep the transaction open so commits overlap

    rt = threading.Thread(target=reader)
    rt.start()
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    [w.start() for w in writers]
    [w.join(60) for w in writers]
    stop.set()
    rt.join(30)
    with get_db() as conn:
        everything = [r["id"] for r in ledger.read(conn, "busy", limit=1000)]
    assert len(everything) == 240
    assert seen == everything  # the follower saw every event, in order, exactly once


def test_a_failed_transaction_leaves_nothing_behind():
    insert_run("atomic")
    with pytest.raises(RuntimeError):
        with get_db() as conn:
            ledger.append(conn, "atomic", "first")
            transition(conn, "atomic", RunStatus.RUNNING, actor="t", reason="r")
            raise RuntimeError("boom")
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id = 'atomic'").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM runs WHERE id = 'atomic'").fetchone()[0] == "created"


def test_foreign_keys_are_enforced():
    with pytest.raises(sqlite3.IntegrityError):
        with get_db() as conn:
            ledger.append(conn, "no-such-run", "orphan")
    with pytest.raises(sqlite3.IntegrityError):
        with get_db() as conn:
            conn.execute("INSERT INTO memberships (principal_id, workspace_id, role) VALUES ('nobody', 'ws_local', 'OWNER')")


def test_unique_constraints_and_append_only_triggers_raise_integrity_errors():
    insert_run("dup")
    with get_db() as conn:
        a = ledger.append(conn, "dup", "x")[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with get_db() as conn:
            conn.execute("UPDATE run_events SET type = 'y' WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with get_db() as conn:
            conn.execute("DELETE FROM run_events WHERE id = ?", (a,))
    with get_db() as conn:
        ledger.redact(conn, a)  # the one sanctioned change
    with pytest.raises(sqlite3.IntegrityError):
        with get_db() as conn:
            conn.execute("INSERT INTO organizations (id, name, created_at) VALUES ('org_local', 'again', 'x')")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM run_events WHERE run_id = 'r' AND id > 5 ORDER BY id LIMIT 10",
    "SELECT id FROM runs WHERE status = 'queued' ORDER BY queued_at, id LIMIT 1",
    "SELECT * FROM runs WHERE workspace_id = 'w' ORDER BY created_at DESC LIMIT 50",
    "SELECT * FROM memories WHERE org_id = 'o' AND workspace_id = 'w' AND scope = 'repository' AND scope_id = 's' AND status = 'active'",
    "SELECT * FROM checkpoints WHERE run_id = 'r' ORDER BY seq DESC LIMIT 1",
])
def test_the_hot_queries_have_an_index_to_use(sql):
    with get_db() as conn:
        conn.execute("SET LOCAL enable_seqscan = off")  # tables are tiny here; prove an index exists, not that the planner prefers it
        plan = "\n".join(r[0] for r in conn.execute("EXPLAIN " + sql))
    assert "Index" in plan and "Seq Scan" not in plan, plan


def test_installs_in_different_schemas_do_not_see_each_other():
    insert_run("mine")
    other = "t_other_" + uuid.uuid4().hex[:6]
    import psycopg
    with psycopg.connect(os.environ["PATCHQUEST_TEST_PG"], autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {other}")
    mine = (database._PG_DSN, database._PG_SCHEMA)
    try:
        database.use_postgres(mine[0], other)
        database.init_db()
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        database.use_postgres(*mine)
        with psycopg.connect(os.environ["PATCHQUEST_TEST_PG"], autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {other} CASCADE")
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE id = 'mine'").fetchone()[0] == 1


def test_data_survives_closing_and_reopening_the_pool():
    insert_run("durable")
    from patchquest import dbpg
    dbpg.close_pool(database._PG_DSN, database._PG_SCHEMA)
    with get_db() as conn:
        assert conn.execute("SELECT id FROM runs WHERE id = 'durable'").fetchone()[0] == "durable"


def test_a_lock_timeout_surfaces_as_a_database_failure_not_a_crash():
    insert_run("locked")
    import psycopg
    holder = psycopg.connect(os.environ["PATCHQUEST_TEST_PG"], autocommit=False, options=f"-c search_path={database._PG_SCHEMA}")
    try:
        holder.execute("UPDATE runs SET task = 'held' WHERE id = 'locked'")
        with pytest.raises(sqlite3.OperationalError) as exc:
            with get_db() as conn:
                conn.execute("SET LOCAL lock_timeout = '150ms'")
                conn.execute("UPDATE runs SET task = 'mine' WHERE id = 'locked'")
        assert classify(exc.value).kind is FailureKind.DATABASE_FAILURE
    finally:
        holder.rollback()
        holder.close()
    with get_db() as conn:
        conn.execute("UPDATE runs SET task = 'free' WHERE id = 'locked'")  # and the row is usable once the holder is gone


def test_aggregates_come_back_as_numbers_like_sqlite_returns_them():
    insert_run("agg")
    with get_db() as conn:
        for tokens in (10, 20, 30):
            conn.execute("INSERT INTO model_calls (run_id, role, started_at, status, prompt_tokens) VALUES ('agg', 'planner', 'x', 'ok', ?)", (tokens,))
        total = conn.execute("SELECT SUM(prompt_tokens) FROM model_calls").fetchone()[0]
        mean = conn.execute("SELECT AVG(prompt_tokens) FROM model_calls").fetchone()[0]
    assert total == 60 and isinstance(total, int) and mean == 20 and isinstance(mean, int | float)


def test_processes_starting_together_migrate_exactly_once():
    import psycopg

    from patchquest.persistence.migrations import current_version, migrate
    from patchquest.persistence.schema import MIGRATIONS

    fresh = "t_boot_" + uuid.uuid4().hex[:6]
    with psycopg.connect(os.environ["PATCHQUEST_TEST_PG"], autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {fresh}")
    mine = (database._PG_DSN, database._PG_SCHEMA)
    outcomes: list[object] = []
    guard = threading.Lock()
    try:
        database.use_postgres(mine[0], fresh)

        def boot() -> None:
            try:
                with get_db() as conn:
                    applied = migrate(conn, MIGRATIONS, None)
                with guard:
                    outcomes.append(len(applied))
            except BaseException as exc:
                with guard:
                    outcomes.append(exc)

        threads = [threading.Thread(target=boot) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        assert all(isinstance(o, int) for o in outcomes), outcomes
        assert sorted(outcomes)[-1] == len(MIGRATIONS) and sum(outcomes) == len(MIGRATIONS)  # one booter did all the work
        with get_db() as conn:
            assert current_version(conn) == max(m.version for m in MIGRATIONS)
            assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(MIGRATIONS)
    finally:
        database.use_postgres(*mine)
        with psycopg.connect(os.environ["PATCHQUEST_TEST_PG"], autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {fresh} CASCADE")
