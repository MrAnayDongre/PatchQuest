"""Checkpoint storage: integrity, versioning and limits (resume behaviour lives in tests/e2e/recovery)."""

import pytest

from patchquest.database import get_db
from patchquest.persistence import checkpoints
from patchquest.persistence.checkpoints import (
    CheckpointCorrupt,
    CheckpointTooLarge,
    CheckpointTooNew,
    describe,
    get,
    latest_valid,
    save,
)
from tests.support.db import insert_run


def _save(conn, run_id="c1", phase="intake", state=None):
    return save(conn, run_id=run_id, phase=phase, state=state or {"ctx": {"task": "t"}}, fingerprint={"head": "h"},
                event_cursor=7, attempt=1, runtime_version="test")


@pytest.fixture(autouse=True)
def run():
    insert_run("c1")


def test_roundtrip_preserves_state_and_identity():
    with get_db() as conn:
        written = _save(conn, state={"ctx": {"task": "töst ✓", "n": [1, 2]}})
        read = get(conn, "c1", written.seq)
    assert (read.state, read.fingerprint, read.phase, read.event_cursor, read.attempt) == (
        {"ctx": {"task": "töst ✓", "n": [1, 2]}}, {"head": "h"}, "intake", 7, 1)


def test_sequence_numbers_are_per_run_and_gapless():
    insert_run("c2")
    with get_db() as conn:
        assert [_save(conn).seq for _ in range(3)] == [1, 2, 3]
        assert _save(conn, run_id="c2").seq == 1


def test_latest_valid_returns_the_newest_and_reports_nothing_when_all_is_well():
    with get_db() as conn:
        _save(conn, phase="a")
        _save(conn, phase="b")
        cp, problems = latest_valid(conn, "c1")
    assert cp is not None and cp.phase == "b" and problems == []


def test_no_checkpoints_is_not_an_error():
    with get_db() as conn:
        assert latest_valid(conn, "c1") == (None, [])


@pytest.mark.parametrize("column,value", [
    ("state_json", '{"ctx": {"task": "other"}}'),
    ("fingerprint_json", '{"head": "evil"}'),
    ("phase", "patching"),
    ("event_cursor", 99),
    ("attempt", 5),
    ("seq", 42),
    ("run_id", "c2"),
])
def test_any_tampered_field_fails_verification(column, value):
    insert_run("c2")
    with get_db() as conn:
        written = _save(conn)
        conn.execute(f"UPDATE checkpoints SET {column} = ? WHERE id = ?", (value, written.id))
        row = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (written.id,)).fetchone()
        with pytest.raises(CheckpointCorrupt):
            checkpoints._decode(row)


def test_truncated_json_is_corrupt_not_a_crash():
    with get_db() as conn:
        written = _save(conn)
        conn.execute("UPDATE checkpoints SET state_json = substr(state_json, 1, 5) WHERE id = ?", (written.id,))
        cp, problems = latest_valid(conn, "c1")
    assert cp is None and "checksum mismatch" in problems[0]


def test_describe_reports_integrity_per_checkpoint_without_raising():
    with get_db() as conn:
        _save(conn, phase="a")
        bad = _save(conn, phase="b")
        conn.execute("UPDATE checkpoints SET checksum = 'x' WHERE id = ?", (bad.id,))
        info = describe(conn, "c1")
    assert info[0]["status"] == "ok" and info[1]["status"].startswith("invalid")


def test_newer_schema_is_refused_not_misread():
    with get_db() as conn:
        written = _save(conn)
        # re-sign a row as written by a future release so only the version differs
        row = conn.execute("SELECT * FROM checkpoints WHERE id = ?", (written.id,)).fetchone()
        future = checkpoints.CHECKPOINT_SCHEMA_VERSION + 1
        digest = checkpoints._checksum("c1", row["seq"], future, row["phase"], row["event_cursor"], row["attempt"],
                                       row["state_json"], row["fingerprint_json"])
        conn.execute("UPDATE checkpoints SET schema_version = ?, checksum = ? WHERE id = ?", (future, digest, written.id))
        with pytest.raises(CheckpointTooNew):
            get(conn, "c1", written.seq)
        cp, problems = latest_valid(conn, "c1")
    assert cp is None and "newer PatchQuest" in problems[0]


def test_older_schema_is_upgraded_on_read(monkeypatch):
    with get_db() as conn:
        written = _save(conn)
        conn.execute("DELETE FROM checkpoints")
    # pretend the code moved on to v2 and the stored row is a v1 row
    monkeypatch.setattr(checkpoints, "CHECKPOINT_SCHEMA_VERSION", 2)
    monkeypatch.setitem(checkpoints.UPGRADERS, 1, lambda state: {**state, "upgraded": True})
    with get_db() as conn:
        digest = checkpoints._checksum("c1", 1, 1, "intake", 7, 1, '{"ctx":{"task":"t"}}', '{"head":"h"}')
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, 'c1', 1, 1, 'old', 'intake', 7, 1, ?, ?, ?, 'n')",
            (written.id, '{"ctx":{"task":"t"}}', '{"head":"h"}', digest))
        cp = get(conn, "c1", 1)
    assert cp.state["upgraded"] is True and cp.schema_version == 2


def test_missing_upgrade_path_is_corrupt(monkeypatch):
    with get_db() as conn:
        written = _save(conn)
    monkeypatch.setattr(checkpoints, "CHECKPOINT_SCHEMA_VERSION", 3)
    with get_db() as conn, pytest.raises(CheckpointCorrupt, match="no upgrade path"):
        get(conn, "c1", written.seq)


def test_oversized_state_is_rejected_before_writing(monkeypatch):
    monkeypatch.setattr(checkpoints, "MAX_CHECKPOINT_BYTES", 100)
    with get_db() as conn, pytest.raises(CheckpointTooLarge):
        _save(conn, state={"blob": "x" * 500})
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0


def test_get_unknown_checkpoint():
    with get_db() as conn, pytest.raises(LookupError):
        get(conn, "c1", 9)
