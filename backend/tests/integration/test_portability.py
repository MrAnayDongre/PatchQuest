"""Run bundles: what survives an export and import, what each level of detail enables, and refusal of hostile input."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from patchquest import cli, portability
from patchquest.application import TaskService
from patchquest.database import get_db
from patchquest.persistence import checkpoints
from patchquest.runtime.replay import ReplayMode, compare_runs, replay_state
from tests.support import FIX, PLAN, make_calc_repo, run_row, run_scripted

SECRET = "sk-" + "f9e8d7c6b5" * 4
REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


@pytest.fixture
async def finished(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    _, rid = await run_scripted(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # restore the bug so replays have something to do
    return rid


def bundle_with(tmp_path, **files):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return path


MANIFEST = json.dumps({"format": 1, "schema_version": 1, "run_id": "x", "includes": {}})
RUN = json.dumps({"task": "t", "repo_path": "/r", "status": "completed"})


@pytest.mark.asyncio
async def test_a_light_bundle_imports_as_an_inspectable_record_and_cannot_be_resumed(finished, tmp_path):
    dest = tmp_path / "light.zip"
    names = portability.export_run(finished, dest)
    assert "checkpoints.jsonl" not in names
    with zipfile.ZipFile(dest) as z:
        assert not any(json.loads(line).get("request_json") for line in z.read("model_calls.jsonl").decode().splitlines())
    new = portability.import_run(dest)
    assert new != finished
    row = run_row(new)
    assert (row["status"], row["outcome"], row["verdict"], row["lineage_kind"]) == ("completed", "applied", "passed", "import")
    report = replay_state(new)
    assert report.ok, report.findings  # the imported history is as consistent as the original
    assert report.status_trail == replay_state(finished).status_trail
    with get_db() as conn:
        assert not conn.execute("SELECT 1 FROM checkpoints WHERE run_id = ?", (new,)).fetchone()
    with pytest.raises(Exception, match=r"(?i)checkpoint|fork|resume"):
        TaskService().fork(new)


@pytest.mark.asyncio
async def test_a_full_bundle_can_be_model_replayed_and_forked_elsewhere(finished, tmp_path):
    dest = tmp_path / "full.zip"
    portability.export_run(finished, dest, include_model_io=True, include_code=True)
    new = portability.import_run(dest)
    with get_db() as conn:
        assert len(checkpoints.describe(conn, new)) == 12 and all(c["status"] == "ok" for c in checkpoints.describe(conn, new))
    svc = TaskService()
    child = svc.replay(new, ReplayMode.MODEL)  # the recorded answers travelled with the bundle
    await svc._tasks[child["id"]]
    assert compare_runs(new, child["id"]).matched


@pytest.mark.asyncio
async def test_import_is_repeatable_and_assigns_fresh_ids(finished, tmp_path):
    dest = tmp_path / "b.zip"
    portability.export_run(finished, dest)
    a, b = portability.import_run(dest), portability.import_run(dest)
    assert len({finished, a, b}) == 3
    with get_db() as conn:
        uids = [r[0] for r in conn.execute("SELECT event_uid FROM run_events WHERE event_uid IS NOT NULL")]
    assert len(uids) == len(set(uids))


@pytest.mark.asyncio
async def test_nothing_sensitive_leaves_in_a_bundle(tmp_path):
    repo = make_calc_repo(tmp_path / "r")
    _, rid = await run_scripted(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    with get_db() as conn:
        conn.execute("UPDATE model_calls SET response_text = ?, request_json = ? WHERE run_id = ?",
                     (f"the key is {SECRET}", f"[{{\"content\": \"home {Path.home()}/proj\"}}]", rid))
    dest = tmp_path / "s.zip"
    portability.export_run(rid, dest, include_model_io=True, include_code=True)
    with zipfile.ZipFile(dest) as z:
        text = "\n".join(z.read(n).decode() for n in z.namelist())
    assert SECRET not in text and str(Path.home()) not in text
    assert dest.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_an_unfinished_run_is_imported_as_interrupted_not_running(finished, tmp_path):
    with get_db() as conn:
        conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (finished,))
    dest = tmp_path / "u.zip"
    portability.export_run(finished, dest)
    assert run_row(portability.import_run(dest))["status"] == "interrupted"


class TestHostileBundles:
    @pytest.mark.parametrize("make,message", [
        (lambda p: p.write_bytes(b"not a zip at all"), "not a zip"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": "{{{", "run.json": RUN}), "unreadable"),
        (lambda p: bundle_with(p.parent, **{"run.json": RUN}), "missing or unreadable"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": json.dumps({"format": 99}), "run.json": RUN}), "unsupported bundle format"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": json.dumps({"format": 1, "schema_version": 9999}), "run.json": RUN}), "newer PatchQuest"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": MANIFEST, "run.json": json.dumps({"task": 5})}), "malformed"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": MANIFEST, "run.json": RUN, "events.jsonl": "not json\n"}), "not valid JSON"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": MANIFEST, "run.json": RUN, "events.jsonl": "[1, 2]\n"}), "not an object"),
        (lambda p: bundle_with(p.parent, **{"manifest.json": MANIFEST, "run.json": RUN, "checkpoints.jsonl": json.dumps({"phase": 1}) + "\n"}),
         "checkpoint"),
    ])
    def test_refused_with_a_reason(self, tmp_path, make, message):
        path = tmp_path / "x.zip"
        made = make(path)
        target = made if isinstance(made, Path) else path
        with get_db() as conn:
            before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        with pytest.raises(portability.ImportRefused, match=message):
            portability.import_run(target)
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before  # a refused import stores nothing

    def test_too_many_files_and_oversized_content_are_refused(self, tmp_path):
        many = bundle_with(tmp_path, **{f"f{i}.txt": "x" for i in range(40)})
        with pytest.raises(portability.ImportRefused, match="too many files"):
            portability.import_run(many)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", MANIFEST)
            z.writestr("run.json", RUN)
            z.writestr("events.jsonl", "0" * (portability.MAX_UNCOMPRESSED + 1))  # compresses to almost nothing
        with pytest.raises(portability.ImportRefused, match="too large"):
            portability.import_run(buf.getvalue())

    def test_unknown_files_and_path_tricks_are_ignored_and_nothing_is_written_to_disk(self, tmp_path):
        path = bundle_with(tmp_path, **{"manifest.json": MANIFEST, "run.json": RUN, "../../evil.txt": "pwned", "/abs/evil.txt": "pwned",
                                        "events.jsonl": ""})
        new = portability.import_run(path)
        assert run_row(new)["lineage_kind"] == "import"
        assert not (tmp_path.parent / "evil.txt").exists() and not Path("/abs/evil.txt").exists()

    def test_a_pending_approval_in_a_bundle_cannot_be_acted_on(self, tmp_path):
        approvals = json.dumps({"type": "command", "command": "rm -rf x", "status": "pending", "created_at": "n"}) + "\n"
        new = portability.import_run(bundle_with(tmp_path, **{"manifest.json": MANIFEST, "run.json": RUN, "approvals.jsonl": approvals}))
        assert TaskService().pending_approvals(new) == []  # imported approvals are history, never open questions
        with get_db() as conn:
            assert conn.execute("SELECT status FROM approvals WHERE run_id = ?", (new,)).fetchone()[0] == "expired"


@pytest.mark.asyncio
async def test_cli_export_and_import(finished, tmp_path, capsys):
    dest = tmp_path / "cli.zip"
    assert cli.main(["export", finished, str(dest), "--model-io", "--code"]) == cli.EXIT_OK
    assert cli.main(["export", finished, str(dest)]) == cli.EXIT_FAILED  # exists
    assert cli.main(["export", "nope", str(tmp_path / "n.zip")]) == cli.EXIT_USAGE
    capsys.readouterr()
    assert cli.main(["import", str(dest), "--json"]) == cli.EXIT_OK
    assert run_row(json.loads(capsys.readouterr().out)["run_id"])["lineage_kind"] == "import"
    assert cli.main(["import", str(tmp_path / "missing.zip")]) == cli.EXIT_FAILED
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"junk")
    assert cli.main(["import", str(junk)]) == cli.EXIT_USAGE
