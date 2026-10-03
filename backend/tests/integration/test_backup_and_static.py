"""Backups (create/verify/restore) and serving the built UI from the API."""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from patchquest import backup, cli
from patchquest.config import load_config
from patchquest.database import get_db, get_db_path
from tests.support import FIX, PLAN, make_calc_repo, run_scripted

REVIEW = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [],
          "recommendation": "approve"}


@pytest.fixture
async def populated(tmp_path):
    _, rid = await run_scripted(make_calc_repo(tmp_path / "r"), {"planner": [PLAN], "coder": [FIX], "reviewer": [REVIEW]})
    return rid


@pytest.mark.asyncio
@pytest.mark.sqlite_only
async def test_a_backup_is_a_consistent_verified_private_copy(populated, tmp_path):
    dest = tmp_path / "backups" / "pq.db"
    report = backup.create(get_db_path(), dest)
    assert report.ok and report.counts["runs"] == 1 and report.counts["checkpoints"] == 12 and report.counts["run_events"] > 20
    assert (dest.stat().st_mode & 0o777) == 0o600
    assert json.loads(dest.with_name("pq.db.manifest.json").read_text())["ok"] is True
    with pytest.raises(FileExistsError):
        backup.create(get_db_path(), dest)  # never silently overwrites a backup


@pytest.mark.asyncio
@pytest.mark.sqlite_only
async def test_verify_catches_damage(populated, tmp_path):
    dest = tmp_path / "pq.db"
    backup.create(get_db_path(), dest)
    conn = sqlite3.connect(dest)
    conn.execute("DROP TRIGGER run_events_no_delete")
    conn.execute("UPDATE checkpoints SET checksum = 'x' WHERE seq = 1")
    conn.commit()
    conn.close()
    report = backup.verify(dest)
    assert not report.ok
    assert any("missing guard: run_events_no_delete" in p for p in report.problems)
    assert any("checkpoint(s) fail their checksum" in p for p in report.problems)


@pytest.mark.sqlite_only
def test_verify_rejects_things_that_are_not_backups(tmp_path):
    assert "does not exist" in backup.verify(tmp_path / "nope.db").problems[0]
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    assert not backup.verify(junk).ok
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert "not a PatchQuest database" in backup.verify(empty).problems[0]


@pytest.mark.asyncio
@pytest.mark.sqlite_only
async def test_a_newer_schema_is_refused_and_an_older_one_is_noted_not_failed(populated, tmp_path):
    dest = tmp_path / "pq.db"
    backup.create(get_db_path(), dest)
    conn = sqlite3.connect(dest)
    conn.execute("INSERT INTO schema_migrations VALUES (999, 'future', 'n')")
    conn.commit()
    assert not backup.verify(dest).ok and "newer than this PatchQuest" in backup.verify(dest).problems[0]
    conn.execute("DELETE FROM schema_migrations WHERE version IN (999, (SELECT MAX(version) FROM schema_migrations WHERE version < 999))")
    conn.commit()
    conn.close()
    old = backup.verify(dest)
    assert old.ok and any("will be migrated" in p for p in old.problems)


@pytest.mark.asyncio
@pytest.mark.sqlite_only
async def test_restore_replaces_the_database_and_keeps_the_old_one(populated, tmp_path):
    dest = tmp_path / "pq.db"
    backup.create(get_db_path(), dest)
    with get_db() as conn:
        conn.execute("INSERT INTO runs (id, repo_path, task, created_at, updated_at) VALUES ('later', '/r', 'added after the backup', 'n', 'n')")
    kept = backup.restore(dest, get_db_path())
    assert kept.exists()
    with get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE id = 'later'").fetchone()[0] == 0  # back to the backup's state
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert sqlite3.connect(kept).execute("SELECT COUNT(*) FROM runs WHERE id = 'later'").fetchone()[0] == 1  # nothing was lost


@pytest.mark.asyncio
@pytest.mark.sqlite_only
async def test_restore_refuses_a_bad_backup_and_a_database_in_use(populated, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"x" * 5000)
    with pytest.raises(backup.RestoreRefused, match="does not verify"):
        backup.restore(bad, get_db_path())
    good = tmp_path / "good.db"
    backup.create(get_db_path(), good)
    holder = sqlite3.connect(get_db_path())
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(backup.RestoreRefused, match="in use"):
            backup.restore(good, get_db_path())
    finally:
        holder.execute("ROLLBACK")
        holder.close()


@pytest.mark.asyncio
@pytest.mark.sqlite_only
async def test_cli_backup_commands(populated, tmp_path, capsys):
    dest = tmp_path / "cli.db"
    assert cli.main(["backup", "create", str(dest), "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["backup", "verify", str(dest)]) == cli.EXIT_OK
    assert cli.main(["backup", "create", str(dest)]) == cli.EXIT_FAILED  # exists
    assert cli.main(["backup", "restore", str(dest)]) == cli.EXIT_NEEDS_CONFIRMATION  # needs --yes
    assert cli.main(["backup", "verify", str(tmp_path / "missing.db")]) == cli.EXIT_FAILED


class TestServingTheUi:
    @pytest.fixture
    def ui(self, tmp_path, monkeypatch):
        root = tmp_path / "ui"
        (root / "assets").mkdir(parents=True)
        (root / "index.html").write_text("<html>app shell</html>")
        (root / "assets" / "app.js").write_text("console.log(1)")
        (root.parent / "secret.txt").write_text("outside the static root")
        monkeypatch.setenv("PATCHQUEST_STATIC_DIR", str(root))
        from fastapi import FastAPI

        from patchquest.main import _mount_ui

        app = FastAPI()

        @app.get("/api/ping")
        async def ping():
            return {"ok": True}

        _mount_ui(app)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")

    @pytest.mark.asyncio
    async def test_files_shell_fallback_and_api_404(self, ui):
        async with ui as c:
            assert "app shell" in (await c.get("/")).text
            assert "app shell" in (await c.get("/runs/abc")).text  # a client-side route survives a refresh
            assert (await c.get("/assets/app.js")).text == "console.log(1)"
            assert (await c.get("/api/ping")).json() == {"ok": True}
            assert (await c.get("/api/does-not-exist")).status_code == 404  # not turned into the app shell

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt", "//etc/passwd", "/..%2fsecret.txt"])
    async def test_nothing_outside_the_static_root_is_served(self, ui, path):
        async with ui as c:
            r = await c.get(path)
        assert "outside the static root" not in r.text and "root:" not in r.text

    @pytest.mark.asyncio
    async def test_the_ui_is_public_but_the_api_still_needs_a_token(self, tmp_path, monkeypatch):
        from patchquest.domain.identity import Role
        from patchquest.persistence import identity as ids

        root = tmp_path / "ui"
        root.mkdir()
        (root / "index.html").write_text("<html>shell</html>")
        monkeypatch.setenv("PATCHQUEST_STATIC_DIR", str(root))
        with get_db() as conn:
            org = ids.create_org(conn, "A")
            ws = ids.create_workspace(conn, org, "w")
            p = ids.create_principal(conn, org, "ana")
            ids.set_role(conn, p, ws, Role.OWNER)
            ids.issue_token(conn, p)
        from fastapi import Depends, FastAPI

        from patchquest.api.auth import authenticate_request
        from patchquest.main import _mount_ui
        from patchquest.main import app as real

        app = FastAPI(dependencies=[Depends(authenticate_request)])
        for route in real.routes:
            if getattr(route, "path", "").startswith("/api/runs") and route.path == "/api/runs":
                app.router.routes.append(route)
        _mount_ui(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            assert "shell" in (await c.get("/")).text and "shell" in (await c.get("/runs/x")).text
            assert (await c.get("/api/runs")).status_code == 401


def test_environment_variables_configure_a_container(monkeypatch, tmp_path):
    monkeypatch.setenv("PATCHQUEST_QUEUE_MODE", "true")
    monkeypatch.setenv("PATCHQUEST_HOST", "0.0.0.0")
    monkeypatch.setenv("PATCHQUEST_PORT", "9000")
    monkeypatch.setenv("PATCHQUEST_WORKER_LEASE_SECONDS", "45")
    cfg = load_config(str(tmp_path / "none.yaml"))
    assert (cfg.queue_mode, cfg.host, cfg.port, cfg.worker_lease_seconds) == (True, "0.0.0.0", 9000, 45.0)
    monkeypatch.setenv("PATCHQUEST_QUEUE_MODE", "off")
    assert load_config(str(tmp_path / "none.yaml")).queue_mode is False
    monkeypatch.setenv("PATCHQUEST_PORT", "eighty")
    with pytest.raises(ValueError, match="PATCHQUEST_PORT"):
        load_config(str(tmp_path / "none.yaml"))
