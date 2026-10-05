"""The durable queue: exclusive claims, leases, heartbeats, and recovery of a dead worker's run."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from patchquest.database import get_db
from patchquest.domain.runs import IllegalTransition
from patchquest.persistence import ledger
from patchquest.runtime import queue
from tests.support.db import insert_run

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def make_queued(n=1, prefix="q"):
    ids = []
    for i in range(n):
        run_id = f"{prefix}{i}"
        insert_run(run_id)
        with get_db() as conn:
            queue.enqueue(conn, run_id, actor="test")
            conn.execute("UPDATE runs SET queued_at = ? WHERE id = ?", (f"2026-10-02T11:00:{i:02d}+00:00", run_id))
        ids.append(run_id)
    return ids


def row(run_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


class TestEnqueueAndClaim:
    def test_a_claim_gives_the_oldest_queued_run_and_starts_it(self):
        a, b = make_queued(2)
        c = queue.claim("w1", 30, now=T0)
        assert (c.run_id, c.kind, c.epoch) == (a, "start", 1)
        r = row(a)
        assert (r["status"], r["lease_owner"], r["lease_epoch"]) == ("running", "w1", 1)
        assert r["lease_expires_at"] == (T0 + timedelta(seconds=30)).isoformat()
        assert row(b)["status"] == "queued"
        with get_db() as conn:
            event = next(e for e in ledger.read(conn, a) if e["type"] == "run_state_changed" and e["status"] == "running")
        assert event["actor"] == "worker:w1"  # who started it is on the record

    def test_nothing_queued_means_nothing_claimed(self):
        assert queue.claim("w1", 30, now=T0) is None
        insert_run("not-queued")  # a created run is not claimable
        assert queue.claim("w1", 30, now=T0) is None

    def test_only_a_created_or_interrupted_run_can_be_queued(self):
        insert_run("done", status="completed")
        with get_db() as conn, pytest.raises(IllegalTransition):
            queue.enqueue(conn, "done", actor="test")

    def test_many_workers_claiming_at_once_never_share_a_run(self):
        ids = make_queued(12)
        got: list[str] = []
        lock = threading.Lock()

        def worker(n):
            while True:
                c = queue.claim(f"w{n}", 30, now=T0)
                if c is None:
                    return
                with lock:
                    got.append(c.run_id)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(got) == sorted(ids) and len(set(got)) == len(got)  # every run exactly once

    def test_epoch_increases_across_claims_of_the_same_run(self):
        (a,) = make_queued(1)
        assert queue.claim("w1", 30, now=T0).epoch == 1
        with get_db() as conn:  # the worker finished its attempt without the run ending, e.g. an approval-style requeue
            conn.execute("UPDATE runs SET status = 'interrupted' WHERE id = ?", (a,))
            queue.enqueue(conn, a, actor="test")
        assert queue.claim("w2", 30, now=T0).epoch == 2


class TestLeases:
    def test_heartbeats_extend_the_lease_for_the_owner_only(self):
        (a,) = make_queued(1)
        c = queue.claim("w1", 30, now=T0)
        queue.heartbeat(a, "w1", c.epoch, 30, now=T0 + timedelta(seconds=20))
        assert row(a)["lease_expires_at"] == (T0 + timedelta(seconds=50)).isoformat()
        with pytest.raises(queue.LeaseLost):
            queue.heartbeat(a, "w2", c.epoch, 30, now=T0)  # not the owner
        with pytest.raises(queue.LeaseLost):
            queue.heartbeat(a, "w1", c.epoch + 1, 30, now=T0)  # a newer epoch owns it
        assert row(a)["lease_expires_at"] == (T0 + timedelta(seconds=50)).isoformat()  # refusals changed nothing

    def test_a_live_lease_is_never_recovered(self):
        make_queued(1)
        queue.claim("w1", 30, now=T0)
        assert queue.claim("w2", 30, now=T0 + timedelta(seconds=29)) is None

    def test_an_expired_lease_is_recovered_as_interrupted_and_the_old_owner_learns_it(self):
        (a,) = make_queued(1)
        c = queue.claim("w1", 30, now=T0)
        recovered = queue.claim("w2", 30, now=T0 + timedelta(seconds=31))
        assert (recovered.run_id, recovered.kind) == (a, "recover")
        r = row(a)
        assert (r["status"], r["lease_owner"], r["outcome"]) == ("interrupted", None, "interrupted")
        with get_db() as conn:
            types = [e["type"] for e in ledger.read(conn, a)]
        assert "run_interrupted" in types  # the event clients treat as "this run ended"
        with pytest.raises(queue.LeaseLost):
            queue.heartbeat(a, "w1", c.epoch, 30, now=T0 + timedelta(seconds=32))  # the stalled worker is told

    def test_a_dead_workers_pending_approvals_expire(self):
        (a,) = make_queued(1)
        queue.claim("w1", 30, now=T0)
        with get_db() as conn:
            conn.execute("INSERT INTO approvals (id, run_id, type, status, created_at) VALUES ('ap', ?, 'command', 'pending', 'n')", (a,))
        queue.claim("w2", 30, now=T0 + timedelta(seconds=60))
        with get_db() as conn:
            assert conn.execute("SELECT status FROM approvals WHERE id = 'ap'").fetchone()[0] == "expired"

    def test_a_cancel_requested_before_the_worker_died_is_honoured(self):
        (a,) = make_queued(1)
        queue.claim("w1", 30, now=T0)
        with get_db() as conn:
            conn.execute("UPDATE runs SET status = 'cancel_requested' WHERE id = ?", (a,))
        queue.claim("w2", 30, now=T0 + timedelta(seconds=60))
        assert row(a)["status"] == "cancelled"

    def test_release_clears_the_lease_for_the_owner_only(self):
        (a,) = make_queued(1)
        c = queue.claim("w1", 30, now=T0)
        queue.release(a, "w2", c.epoch)
        assert row(a)["lease_owner"] == "w1"
        queue.release(a, "w1", c.epoch)
        assert row(a)["lease_owner"] is None and row(a)["lease_expires_at"] is None

    def test_finished_runs_with_a_stale_lease_are_not_recovered(self):
        (a,) = make_queued(1)
        queue.claim("w1", 30, now=T0)
        with get_db() as conn:
            conn.execute("UPDATE runs SET status = 'completed' WHERE id = ?", (a,))
        assert queue.claim("w2", 30, now=T0 + timedelta(hours=1)) is None


def test_stats_report_depth_leases_and_wait():
    make_queued(3)
    queue.claim("w1", 30, now=T0)
    s = queue.stats(now=T0 + timedelta(seconds=10))
    assert (s["queued"], s["leased"], s["expired_leases"]) == (2, 1, 0) and s["oldest_wait_s"] > 0
    assert queue.stats(now=T0 + timedelta(minutes=5))["expired_leases"] == 1
