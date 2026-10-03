"""Export a run as a bundle, and import one: reproducibility without sharing more than you mean to.

A bundle is a zip of JSON files: ``manifest.json``, ``run.json``, ``events.jsonl`` and ``approvals.jsonl``; with
``include_model_io`` also ``model_calls.jsonl`` (prompts and answers); with ``include_code`` also ``checkpoints.jsonl``
(which hold the files the run touched). Everything textual is secret-redacted and your home directory shows as ``~``.
Without checkpoints a bundle can be *inspected and state-replayed*; with model I/O it can also be *model-replayed*;
with code it can be *forked and resumed*.

Import treats the bundle as untrusted input: format and schema version are checked, sizes are capped, only the known
files are read (names are never used as paths; nothing is extracted to disk), every field is type-checked, and the run
gets fresh ids. An imported run is a record: its status is terminal (an unfinished one becomes ``interrupted``) and it
is marked ``lineage_kind = 'import'``.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchquest import __version__
from patchquest.database import get_db
from patchquest.domain.runs import TERMINAL, RunStatus
from patchquest.persistence import checkpoints, ledger
from patchquest.persistence.migrations import current_version
from patchquest.persistence.schema import MIGRATIONS
from patchquest.runtime import egress
from patchquest.runtime import policy as policy_runtime
from patchquest.support_bundle import scrub

FORMAT = 1
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_FILES = 16
KNOWN = {"manifest.json", "run.json", "events.jsonl", "approvals.jsonl", "model_calls.jsonl", "checkpoints.jsonl", "report.json"}
RUN_COLUMNS = ("task", "repo_path", "status", "provider", "model", "runtime_mode", "outcome", "verdict", "failure_kind", "attempt",
               "created_at", "updated_at", "completed_at", "dry_run", "memory_mode", "overrides_json")


class ImportRefused(ValueError):
    """The bundle is not acceptable; the message says why and is safe to show."""


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, default=str) + "\n" for r in rows)


def export_run(run_id: str, dest: Path, *, include_model_io: bool = False, include_code: bool = False) -> list[str]:
    """Write the bundle to ``dest`` (a .zip, never overwritten). Returns the names of the files in it."""
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; choose a new name")
    with get_db() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise LookupError(run_id)
        classes = ["metadata", "logs", *(["model_io"] if include_model_io else []), *(["diff", "source_code"] if include_code else [])]
        egress.enforce(egress.decide_disclosure(policy_runtime.chain_for(workspace_id=run["workspace_id"]), classes, "portable_bundle"),
                       "exporting this run")
        events = ledger.read(conn, run_id, limit=1_000_000)
        approvals = [{k: r[k] for k in r.keys()} for r in conn.execute("SELECT * FROM approvals WHERE run_id = ?", (run_id,))]
        calls = [{k: r[k] for k in r.keys()} for r in conn.execute("SELECT * FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,))]
        cps = [{"seq": cp.seq, "schema_version": cp.schema_version, "runtime_version": cp.runtime_version, "phase": cp.phase,
                "event_cursor": cp.event_cursor, "attempt": cp.attempt, "created_at": cp.created_at, "state": cp.state,
                "fingerprint": cp.fingerprint}
               for cp in (checkpoints.get(conn, run_id, row["seq"]) for row in conn.execute(
                   "SELECT seq FROM checkpoints WHERE run_id = ? ORDER BY seq", (run_id,))) ] if include_code else []
        rep = conn.execute("SELECT report_md, diff_patch, commands_log, created_at FROM reports WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                           (run_id,)).fetchone()
        schema = current_version(conn)
    if not include_model_io:
        calls = [{k: v for k, v in c.items() if k not in ("request_json", "response_text")} for c in calls]
    files: dict[str, str] = {
        "manifest.json": json.dumps({"format": FORMAT, "patchquest": __version__, "schema_version": schema, "run_id": run_id,
                                     "exported_at": datetime.now(UTC).isoformat(),
                                     "includes": {"model_io": include_model_io, "code": include_code}}, indent=2),
        "run.json": json.dumps(scrub({k: run[k] for k in run.keys() if k in RUN_COLUMNS}), indent=2, default=str),
        "events.jsonl": _jsonl(scrub([{k: e[k] for k in ("id", "event_uid", "type", "phase", "status", "message", "payload", "created_at",
                                                         "actor", "attempt", "correlation_id", "causation_id")} for e in events])),
        "approvals.jsonl": _jsonl(scrub(approvals)),
        "model_calls.jsonl": _jsonl(scrub(calls)),
    }
    if rep is not None and include_code:  # the diff is source code, so it follows the same tier as checkpoints
        files["report.json"] = json.dumps(scrub({k: rep[k] for k in rep.keys()}))
    if include_code:
        files["checkpoints.jsonl"] = _jsonl(scrub(cps))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    dest.chmod(0o600)
    return list(files)


def _read(z: zipfile.ZipFile) -> dict[str, bytes]:
    infos = z.infolist()
    if len(infos) > MAX_FILES:
        raise ImportRefused("the bundle has too many files")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise ImportRefused("the bundle is too large when unpacked")
    out: dict[str, bytes] = {}
    for info in infos:
        if info.filename in KNOWN:  # anything else, including odd paths, is ignored: names are never used as paths
            out[info.filename] = z.read(info)
    return out


def _lines(raw: bytes | None, what: str) -> list[dict[str, Any]]:
    rows = []
    for n, line in enumerate((raw or b"").decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise ImportRefused(f"{what} line {n} is not valid JSON") from None
        if not isinstance(row, dict):
            raise ImportRefused(f"{what} line {n} is not an object")
        rows.append(row)
    return rows


def import_run(source: Path | bytes, *, workspace_id: str = "ws_local", actor: str = "import") -> str:
    """Import a bundle as a new read-only run. Returns the new run id."""
    try:
        with zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source) as z:
            files = _read(z)
    except zipfile.BadZipFile:
        raise ImportRefused("that is not a PatchQuest bundle (not a zip file)") from None
    try:
        manifest = json.loads(files.get("manifest.json", b""))
        run = json.loads(files.get("run.json", b""))
    except ValueError:
        raise ImportRefused("the bundle's manifest or run record is missing or unreadable") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ImportRefused(f"unsupported bundle format {manifest.get('format') if isinstance(manifest, dict) else None!r}")
    latest = max(m.version for m in MIGRATIONS)
    if not isinstance(manifest.get("schema_version"), int) or manifest["schema_version"] > latest:
        raise ImportRefused("the bundle comes from a newer PatchQuest; upgrade before importing it")
    if not isinstance(run, dict) or not isinstance(run.get("task"), str) or not isinstance(run.get("repo_path"), str):
        raise ImportRefused("the run record is malformed")
    events, approvals = _lines(files.get("events.jsonl"), "events"), _lines(files.get("approvals.jsonl"), "approvals")
    calls, cps = _lines(files.get("model_calls.jsonl"), "model calls"), _lines(files.get("checkpoints.jsonl"), "checkpoints")

    try:
        report = json.loads(files["report.json"]) if "report.json" in files else None
    except ValueError:
        raise ImportRefused("the bundle's report is unreadable") from None
    new_id = str(uuid.uuid4())
    status = run.get("status") if run.get("status") in {s.value for s in TERMINAL} else RunStatus.INTERRUPTED.value
    now = datetime.now(UTC).isoformat()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO runs (id, repo_path, task, status, provider, model, runtime_mode, outcome, verdict, failure_kind, attempt,
                   created_at, updated_at, completed_at, dry_run, memory_mode, overrides_json, workspace_id, created_by, lineage_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'import')""",
            (new_id, run["repo_path"], run["task"], status, run.get("provider"), run.get("model"), run.get("runtime_mode") or "local",
             run.get("outcome"), run.get("verdict"), run.get("failure_kind"), int(run.get("attempt") or 1),
             str(run.get("created_at") or now), now, run.get("completed_at"), int(bool(run.get("dry_run"))),
             run.get("memory_mode") or "repo", run.get("overrides_json"), workspace_id, actor))
        uid_map: dict[str, str] = {}
        for e in events:
            new_uid = uuid.uuid4().hex
            if isinstance(e.get("event_uid"), str):
                uid_map[e["event_uid"]] = new_uid
            conn.execute(
                "INSERT INTO run_events (run_id, type, phase, status, message, payload_json, created_at, event_uid, schema_version, actor, "
                "attempt, correlation_id, causation_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (new_id, str(e.get("type") or "unknown")[:80], e.get("phase"), e.get("status"), e.get("message"),
                 json.dumps(e["payload"]) if e.get("payload") else None, str(e.get("created_at") or now), new_uid,
                 str(e.get("actor") or "import")[:80], int(e.get("attempt") or 1), e.get("correlation_id"),
                 uid_map.get(e.get("causation_id") or "")))
        ledger.append(conn, new_id, "run_imported", actor=actor, message=f"Imported from run {str(manifest.get('run_id'))[:8]}",
                      payload={"original_run_id": manifest.get("run_id"), "bundle": manifest.get("includes"),
                               "exported_with": manifest.get("patchquest")})
        for a in approvals:
            conn.execute("INSERT INTO approvals (id, run_id, type, command, reason, status, note, created_at, resolved_at, side_effect, risk, phase) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex, new_id, a.get("type") or "command", a.get("command"), a.get("reason"),
                          a.get("status") if a.get("status") != "pending" else "expired", a.get("note"), str(a.get("created_at") or now),
                          a.get("resolved_at"), a.get("side_effect"), a.get("risk"), a.get("phase")))
        for c in calls:
            conn.execute("INSERT INTO model_calls (run_id, role, provider, model, started_at, duration_ms, prompt_tokens, completion_tokens, "
                         "attempts, status, degraded, request_json, response_text, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (new_id, c.get("role") or "unknown", c.get("provider"), c.get("model"), str(c.get("started_at") or now),
                          c.get("duration_ms"), c.get("prompt_tokens"), c.get("completion_tokens"), c.get("attempts") or 1,
                          c.get("status") or "ok", c.get("degraded"), c.get("request_json"), c.get("response_text"), c.get("error")))
        if isinstance(report, dict):
            conn.execute("INSERT INTO reports (run_id, report_md, diff_patch, commands_log, created_at) VALUES (?, ?, ?, ?, ?)",
                         (new_id, report.get("report_md"), report.get("diff_patch"), report.get("commands_log"),
                          str(report.get("created_at") or now)))
        cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM run_events WHERE run_id = ?", (new_id,)).fetchone()[0]
        for cp in cps:
            if not isinstance(cp.get("state"), dict) or not isinstance(cp.get("fingerprint"), dict) or not isinstance(cp.get("phase"), str):
                raise ImportRefused("a checkpoint in the bundle is malformed")
            checkpoints.save(conn, run_id=new_id, phase=cp["phase"], state=cp["state"], fingerprint=cp["fingerprint"],
                             event_cursor=min(int(cp.get("event_cursor") or 0), cursor), attempt=int(cp.get("attempt") or 1),
                             runtime_version=str(cp.get("runtime_version") or "imported"))
    return new_id
