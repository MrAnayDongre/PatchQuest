"""Migrations, the immutable event ledger and validated run transitions."""

import sqlite3
from itertools import product

import pytest

from patchquest.database import SCHEMA, get_db, get_db_path, init_db, set_db_path
from patchquest.domain.runs import (
    ACTIVE,
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    RunStatus,
    StaleTransition,
    can_transition,
)
from patchquest.persistence import ledger
from patchquest.persistence.migrations import Migration, SchemaTooNew, migrate, statements
from patchquest.persistence.runs import transition
from patchquest.persistence.schema import MIGRATIONS
from tests.support.db import insert_run

LATEST = max(m.version for m in MIGRATIONS)


class TestTransitionTable:
    def test_every_status_is_described(self):
        assert set(TRANSITIONS) == set(RunStatus)

    def test_terminal_states_have_no_exit(self):
        assert TERMINAL == {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
        for state, target in product(TERMINAL, RunStatus):
            assert not can_transition(state, target)

    def test_nothing_moves_back_to_created_and_self_loops_are_illegal(self):
        for state in RunStatus:
            assert not can_transition(state, RunStatus.CREATED)
            assert not can_transition(state, state)

    def test_every_non_terminal_state_can_reach_a_terminal_one(self):
        for start in set(RunStatus) - TERMINAL:
            seen, frontier = {start}, [start]
            while frontier:
                frontier = [n for s in frontier for n in TRANSITIONS[s] if n not in seen]
                seen.update(frontier)
            assert seen & TERMINAL, start

    def test_interrupted_is_the_only_way_back_into_running_from_a_crash(self):
        assert can_transition(RunStatus.INTERRUPTED, RunStatus.RUNNING)
        assert ACTIVE.isdisjoint(TERMINAL)


class TestTransition:
    def test_records_actor_reason_and_both_states(self):
        insert_run("r1")
        with get_db() as conn:
            done = transition(conn, "r1", RunStatus.RUNNING, actor="worker:1", reason="go", attempt=2, correlation_id="c")
            row = conn.execute("SELECT * FROM run_events WHERE id = ?", (done.event_id,)).fetchone()
        assert done.previous is RunStatus.CREATED
        assert (row["type"], row["actor"], row["attempt"], row["correlation_id"], row["message"]) == (
            "run_state_changed", "worker:1", 2, "c", "go")
        assert '"from": "created"' in row["payload_json"] and row["event_uid"] == done.event_uid

    def test_illegal_transition_changes_nothing(self):
        insert_run("r2", status="completed")
        with pytest.raises(IllegalTransition), get_db() as conn:
            transition(conn, "r2", RunStatus.RUNNING, actor="x", reason="x")
        with get_db() as conn:
            assert conn.execute("SELECT status FROM runs WHERE id='r2'").fetchone()[0] == "completed"
            assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id='r2'").fetchone()[0] == 0

    def test_status_and_event_commit_or_roll_back_together(self):
        insert_run("r3")
        with pytest.raises(RuntimeError), get_db() as conn:
            transition(conn, "r3", RunStatus.RUNNING, actor="x", reason="x")
            raise RuntimeError("crash before commit")
        with get_db() as conn:
            assert conn.execute("SELECT status FROM runs WHERE id='r3'").fetchone()[0] == "created"
            assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id='r3'").fetchone()[0] == 0

    def test_lost_race_is_reported_not_overwritten(self):
        insert_run("r4", status="running")

        class RacingConnection:
            """Another writer finishes the run between our read of the status and our compare-and-set."""

            def __init__(self, conn):
                self._conn, self._raced = conn, False

            def execute(self, sql, params=()):
                if sql.startswith("UPDATE runs SET") and not self._raced:
                    self._raced = True
                    with get_db() as rival:
                        rival.execute("UPDATE runs SET status = 'completed' WHERE id = 'r4'")
                return self._conn.execute(sql, params)

        with pytest.raises(StaleTransition), get_db() as conn:
            transition(RacingConnection(conn), "r4", RunStatus.CANCEL_REQUESTED, actor="u", reason="stop")  # type: ignore[arg-type]
        with get_db() as conn:
            assert conn.execute("SELECT status FROM runs WHERE id='r4'").fetchone()[0] == "completed"
            assert conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id='r4'").fetchone()[0] == 0

    def test_terminal_and_interrupted_stamp_completed_at_resume_clears_it(self):
        insert_run("r5", status="running")
        with get_db() as conn:
            transition(conn, "r5", RunStatus.INTERRUPTED, actor="x", reason="x")
            assert conn.execute("SELECT completed_at FROM runs WHERE id='r5'").fetchone()[0] is not None
            transition(conn, "r5", RunStatus.RUNNING, actor="x", reason="resume")
            assert conn.execute("SELECT completed_at FROM runs WHERE id='r5'").fetchone()[0] is None

    def test_unknown_run(self):
        with pytest.raises(LookupError), get_db() as conn:
            transition(conn, "nope", RunStatus.RUNNING, actor="x", reason="x")

    def test_only_whitelisted_columns_can_ride_along(self):
        insert_run("r6")
        with get_db() as conn:
            transition(conn, "r6", RunStatus.RUNNING, actor="x", reason="x",
                       fields={"outcome": "ok", "task": "hijacked; --"})
            row = conn.execute("SELECT outcome, task FROM runs WHERE id='r6'").fetchone()
        assert row["outcome"] == "ok" and row["task"] == "task"


class TestLedger:
    def _event(self):
        insert_run("L")
        with get_db() as conn:
            return ledger.append(conn, "L", "model_called", payload={"k": "secret-ish"}, message="m",
                                 actor="agent:coder", attempt=3, correlation_id="c", causation_id="p")

    def test_events_are_attributed_versioned_and_unique(self):
        event_id, uid = self._event()
        with get_db() as conn:
            _, uid2 = ledger.append(conn, "L", "x")
            row = ledger.read(conn, "L")[0]
        assert uid != uid2 and row["id"] == event_id
        assert (row["schema_version"], row["actor"], row["attempt"], row["causation_id"]) == (
            ledger.EVENT_SCHEMA_VERSION, "agent:coder", 3, "p")
        assert row["payload"] == {"k": "secret-ish"}

    def test_cursor_reads_resume_after_an_id(self):
        insert_run("C")
        with get_db() as conn:
            ids = [ledger.append(conn, "C", f"e{i}")[0] for i in range(5)]
            assert [e["id"] for e in ledger.read(conn, "C", after=ids[1])] == ids[2:]

    @pytest.mark.parametrize("sql", [
        "UPDATE run_events SET type = 'forged'",
        "UPDATE run_events SET payload_json = '{}'",
        "UPDATE run_events SET message = 'x'",
        "UPDATE run_events SET run_id = 'other'",
        "UPDATE run_events SET event_uid = 'y'",
        "UPDATE run_events SET redacted = 1",  # flag alone, content kept
        "DELETE FROM run_events",
    ])
    def test_history_cannot_be_rewritten(self, sql):
        self._event()
        with pytest.raises(sqlite3.DatabaseError, match="append-only"), get_db() as conn:
            conn.execute(sql)

    def test_redaction_blanks_content_and_keeps_the_event(self):
        event_id, uid = self._event()
        with get_db() as conn:
            ledger.redact(conn, event_id)
            row = ledger.read(conn, "L")[0]
        assert row["payload"] is None and row["message"] is None and row["redacted"] == 1
        assert row["type"] == "model_called" and row["event_uid"] == uid

    def test_redacted_event_cannot_be_edited_further(self):
        event_id, _ = self._event()
        with get_db() as conn:
            ledger.redact(conn, event_id)
        with pytest.raises(sqlite3.DatabaseError), get_db() as conn:
            conn.execute("UPDATE run_events SET type = 'x' WHERE id = ?", (event_id,))


class TestMigrations:
    def _legacy_db(self, tmp_path):
        """A database as an older release left it: tables, data, no schema_migrations."""
        path = tmp_path / "old.db"
        raw = sqlite3.connect(path)
        raw.executescript(SCHEMA)
        raw.execute("INSERT INTO runs (id, repo_path, task, status, created_at, updated_at) "
                    "VALUES ('old', '/r', 't', 'completed', 'n', 'n')")
        raw.execute("INSERT INTO run_events (run_id, type, created_at) VALUES ('old', 'run_created', 'n')")
        raw.commit()
        raw.close()
        return path

    def test_legacy_database_upgrades_in_place_keeping_history_and_gets_a_backup(self, tmp_path):
        path = self._legacy_db(tmp_path)
        set_db_path(path)
        init_db()
        with get_db() as conn:
            assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == LATEST
            old = ledger.read(conn, "old")[0]
            assert old["type"] == "run_created" and old["event_uid"] is None and old["schema_version"] == 1
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM run_events")
        backup = path.with_name("old.db.pre-v1.bak")
        assert backup.exists()
        assert sqlite3.connect(backup).execute("SELECT COUNT(*) FROM run_events").fetchone()[0] == 1
        cols = {r[1] for r in sqlite3.connect(backup).execute("PRAGMA table_info(run_events)")}
        assert "event_uid" not in cols  # the backup is the pre-upgrade state

    def test_rerunning_is_a_no_op(self):
        init_db()
        init_db()
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(MIGRATIONS)

    def test_fresh_database_has_no_backup(self):
        assert not list(get_db_path().parent.glob("*.bak"))

    def test_database_from_a_newer_release_is_refused(self):
        with get_db() as conn:
            conn.execute("INSERT INTO schema_migrations VALUES (999, 'future', 'n')")
        with pytest.raises(SchemaTooNew, match="v999"):
            init_db()

    def test_failed_migration_rolls_back_completely(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "m.db")
        conn.row_factory = sqlite3.Row

        def good(c):
            c.execute("CREATE TABLE a (x)")

        def bad(c):
            c.execute("CREATE TABLE b (x)")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            migrate(conn, [Migration(1, "a", good), Migration(2, "b", bad)])
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "a" in tables and "b" not in tables  # v1 committed, v2 left no trace
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1

    def test_statement_splitter_keeps_trigger_bodies_whole(self):
        script = "CREATE TABLE t (x);\nCREATE TRIGGER g BEFORE DELETE ON t\nBEGIN SELECT 1; SELECT 2; END;\nSELECT 3;"
        assert len(list(statements(script))) == 3


def test_default_state_directory_is_private_and_a_custom_one_is_left_alone(tmp_path, monkeypatch):
    import stat

    from patchquest import database

    home = tmp_path / "home"
    (home / ".patchquest").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    default_db = home / ".patchquest" / "patchquest.db"
    default_db.write_bytes(b"")
    database._make_private(default_db)
    assert stat.S_IMODE((home / ".patchquest").stat().st_mode) == 0o700
    assert stat.S_IMODE(default_db.stat().st_mode) == 0o600

    custom = tmp_path / "shared"
    custom.mkdir()
    custom.chmod(0o755)
    database._make_private(custom / "x.db")
    assert stat.S_IMODE(custom.stat().st_mode) == 0o755  # not ours to change


def test_the_state_directory_is_always_created_owner_only(tmp_path, monkeypatch):
    import stat

    from patchquest import database

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    created = database.ensure_state_dir()
    assert stat.S_IMODE(created.stat().st_mode) == 0o700
    created.chmod(0o775)  # something else loosened it
    database.ensure_state_dir()
    assert stat.S_IMODE(created.stat().st_mode) == 0o700
