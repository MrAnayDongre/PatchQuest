"""Doctor's checks of the new surfaces, and the support bundle's promise to leave your data out."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from patchquest import cli, doctor, support_bundle
from patchquest.database import get_db
from tests.support import FIX, PLAN, make_calc_repo, run_scripted

SECRET = "sk-" + "a1b2c3d4e5" * 4
REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


def by_name(checks):
    return {c.name: c for c in checks}


class TestDoctor:
    def test_schema_and_git_hardening_and_queue_report_ok_on_a_healthy_install(self):
        checks = by_name(doctor.run_checks())
        assert checks["schema"].status == doctor.OK and checks["git-hardening"].status == doctor.OK
        assert checks["queue"].status == doctor.OK

    def test_a_newer_database_is_a_failure_with_an_explanation(self):
        with get_db() as conn:
            conn.execute("INSERT INTO schema_migrations VALUES (999, 'future', 'n')")
        c = doctor.check_schema()
        assert c.status == doctor.FAIL and "v999" in c.detail and c.impact

    def test_expired_leases_and_long_waits_are_called_out_with_a_fix(self):
        from datetime import UTC, datetime, timedelta

        from patchquest.runtime import queue
        from tests.support.db import insert_run

        insert_run("lost")
        with get_db() as conn:
            queue.enqueue(conn, "lost", actor="t")
        queue.claim("dead-worker", 30, now=datetime.now(UTC) - timedelta(hours=1))
        c = next(x for x in doctor.check_queue_and_runs() if x.name == "queue")
        assert c.status == doctor.WARN and "lost their worker" in c.detail and "patchquest worker" in c.fix

    def test_loose_state_directory_permissions_are_flagged(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".patchquest").mkdir(parents=True)
        (home / ".patchquest").chmod(0o775)
        monkeypatch.setattr("pathlib.Path.home", lambda: home)
        c = doctor.check_state_permissions()
        assert c.status == doctor.WARN and "chmod 700" in c.fix
        (home / ".patchquest").chmod(0o700)
        assert doctor.check_state_permissions().status == doctor.OK

    def test_git_hardening_check_fails_loudly_if_the_wrapper_stops_working(self, monkeypatch):
        from patchquest.runtime import fingerprint

        real = fingerprint._git

        def unhardened(repo, *args):
            import subprocess

            subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=10, check=False)  # no hardening flags at all
            return real(repo, *args)

        monkeypatch.setattr(fingerprint, "_git", unhardened)
        c = doctor.check_git_hardening()
        assert c.status == doctor.FAIL and "executed code" in c.detail

    def test_every_warning_and_failure_says_what_breaks(self):
        for c in doctor.run_checks():
            if c.status in (doctor.WARN, doctor.FAIL):
                assert c.impact, f"{c.name} has no impact statement"

    def test_cli_prints_impact_and_exit_code(self, capsys):
        with get_db() as conn:
            conn.execute("INSERT INTO schema_migrations VALUES (999, 'future', 'n')")
        assert cli.main(["doctor"]) == cli.EXIT_FAILED
        out = capsys.readouterr().out
        assert "impact:" in out and "fix:" in out


@pytest.mark.asyncio
async def test_the_bundle_contains_diagnostics_and_none_of_your_data(tmp_path, monkeypatch):
    repo = make_calc_repo(tmp_path / "r")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b  # UNIQUE-SOURCE-MARKER\n")
    sm, ok_run = await run_scripted(repo, {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]},
                                    task="Fix add() in calc.py so it returns the sum UNIQUE-TASK-MARKER")
    sm2, bad_run = await run_scripted(make_calc_repo(tmp_path / "r2"), {"planner": [PLAN], "coder": []})
    from patchquest.persistence import ledger
    from tests.support.db import insert_run

    insert_run("leaky", status="failed")  # a failure whose message leaked a key, a home path and a URL with credentials
    with get_db() as conn:
        ledger.append(conn, "leaky", "run_failed", message=f"auth failed with {SECRET} in {Path.home()}/proj",
                      payload={"failure": {"kind": "MODEL_AUTH", "detail": "http://user:pw@host/v1"}})
        conn.execute("UPDATE runs SET failure_kind = 'MODEL_AUTH' WHERE id = 'leaky'")
    dest = tmp_path / "out" / "bundle.zip"
    names = support_bundle.build(dest, run_id=ok_run)
    assert dest.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(dest) as z:
        assert set(z.namelist()) == set(names) and {"README.txt", "manifest.json", "doctor.json", "config.json", "database.json",
                                                    "failures.json", "endpoint_health.json"} <= set(names)
        text = "\n".join(z.read(n).decode() for n in z.namelist())
    for forbidden in (SECRET, "UNIQUE-SOURCE-MARKER", "UNIQUE-TASK-MARKER", str(Path.home()), "user:pw@", "calc.py"):
        assert forbidden not in text, forbidden
    failures = json.loads(zipfile.ZipFile(dest).read("failures.json"))
    leaky = next(f for f in failures if f["id"] == "leaky")
    assert leaky["failure_kind"] == "MODEL_AUTH" and "~/proj" in leaky["message"]
    db = json.loads(zipfile.ZipFile(dest).read("database.json"))
    assert db["counts"]["runs"] == 3 and db["schema_version"] == db["expected_schema_version"]
    history = json.loads(zipfile.ZipFile(dest).read(f"run-{ok_run[:12]}.json"))
    assert {"phase_started", "run_completed"} <= {e["type"] for e in history["events"]}


def test_bundle_refuses_to_overwrite_and_works_on_an_empty_install(tmp_path):
    dest = tmp_path / "b.zip"
    support_bundle.build(dest)
    with pytest.raises(FileExistsError):
        support_bundle.build(dest)
    assert cli.main(["doctor", "--bundle", str(tmp_path / "c.zip")]) == cli.EXIT_OK
    assert cli.main(["doctor", "--bundle", str(dest)]) == cli.EXIT_FAILED


def test_scrub_hides_keys_homes_and_url_credentials():
    out = support_bundle.scrub({"a": [f"key {SECRET}", f"{Path.home()}/x", "https://bob:hunter2@example.com/p"], "n": 3})
    assert SECRET not in json.dumps(out) and "~/x" in json.dumps(out) and "hunter2" not in json.dumps(out) and out["n"] == 3
