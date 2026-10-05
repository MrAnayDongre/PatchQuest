"""Consistent backups of the database, and verification that a backup (or the live database) is sound.

Create uses SQLite's online backup API, so it is safe while PatchQuest is running: the copy is a consistent
snapshot, never a half-written file. Verify checks what restoring would depend on: the file is a database,
SQLite's own integrity check passes, its schema is one this release understands, the append-only guards are in
place, and every checkpoint still matches its checksum.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchquest.persistence import checkpoints
from patchquest.persistence.migrations import current_version
from patchquest.persistence.schema import MIGRATIONS

REQUIRED_TRIGGERS = ("run_events_no_update", "run_events_no_delete", "audit_log_no_update", "audit_log_no_delete")


@dataclass
class VerifyReport:
    path: str
    ok: bool
    schema_version: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "ok": self.ok, "schema_version": self.schema_version, "counts": self.counts, "problems": self.problems}


def create(source_path: Path, dest: Path) -> VerifyReport:
    """Snapshot ``source_path`` into ``dest`` (refuses to overwrite) and verify the result."""
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; choose a new name")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    dest.chmod(0o600)  # run history holds task text, command output and file contents
    report = verify(dest)
    (dest.with_name(dest.name + ".manifest.json")).write_text(json.dumps(report.to_dict(), indent=2) + "\n")
    return report


def verify(path: Path) -> VerifyReport:
    report = VerifyReport(str(path), ok=False)
    if not path.is_file():
        report.problems.append("file does not exist")
        return report
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        report.problems.append(f"cannot open: {exc}")
        return report
    try:
        try:
            integrity = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        except sqlite3.DatabaseError as exc:
            report.problems.append(f"not a readable database: {exc}")
            return report
        if integrity != ["ok"]:
            report.problems += [f"integrity: {m}" for m in integrity[:5]]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_migrations'").fetchone() is None:
            report.problems.append("no schema_migrations table: not a PatchQuest database (or from before migrations)")
            return report
        report.schema_version = current_version(conn)
        latest = max(m.version for m in MIGRATIONS)
        if report.schema_version > latest:
            report.problems.append(f"schema v{report.schema_version} is newer than this PatchQuest understands (v{latest})")
        elif report.schema_version < latest:
            report.problems.append(f"schema v{report.schema_version} is older than v{latest}: it will be migrated on first start (not an error)")
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        report.problems += [f"missing guard: {t}" for t in REQUIRED_TRIGGERS if t not in present and report.schema_version >= 7]
        for table in ("runs", "run_events", "checkpoints", "approvals", "audit_log", "workflows", "workflow_runs"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
                report.counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if report.counts.get("checkpoints"):
            bad = 0
            for run in conn.execute("SELECT DISTINCT run_id FROM checkpoints"):
                bad += sum(1 for cp in checkpoints.describe(conn, run[0]) if cp["status"] != "ok")
            if bad:
                report.problems.append(f"{bad} checkpoint(s) fail their checksum")
    finally:
        conn.close()
    report.ok = not [p for p in report.problems if "will be migrated" not in p]
    return report


class RestoreRefused(RuntimeError):
    pass


def restore(backup: Path, target: Path) -> Path:
    """Replace ``target`` with ``backup`` after verifying it. The database being replaced is kept beside it
    (``.pre-restore-<timestamp>``), never deleted. Refuses while another process holds the database open, and
    refuses a backup that does not verify. Returns where the old database went."""
    import shutil
    import time

    report = verify(backup)
    if not report.ok:
        raise RestoreRefused("the backup does not verify: " + "; ".join(p for p in report.problems if "will be migrated" not in p))
    kept = target.with_name(f"{target.name}.pre-restore-{time.strftime('%Y%m%dT%H%M%S')}")
    if target.exists():
        probe = sqlite3.connect(str(target), timeout=1.0)
        try:
            probe.execute("BEGIN EXCLUSIVE")  # fails if a server or worker is still using the database
            probe.execute("ROLLBACK")
        except sqlite3.OperationalError:
            raise RestoreRefused("the database is in use; stop PatchQuest (API and workers) before restoring") from None
        finally:
            probe.close()
        shutil.move(str(target), str(kept))
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)
    target.chmod(0o600)
    return kept
