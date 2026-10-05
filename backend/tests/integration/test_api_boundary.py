"""API boundary over real HTTP (ASGI): authentication, Host validation, repo_path policy, run lifecycle, crash recovery.

Invariants: with a token configured every route except /api/health requires it; foreign Host headers are rejected;
a run against a system directory is refused before anything is stored; finished runs leave no active task behind;
in-flight runs left by a dead process become 'interrupted' and their approvals expire.
"""

import asyncio
import uuid

import httpx
import pytest

from patchquest.config import AppConfig, set_config
from patchquest.database import get_db, now_iso
from patchquest.recovery import recover_interrupted_runs
from patchquest.security import (
    RepoPathError,
    check_startup_policy,
    is_loopback,
    validate_repo_path,
)


def client(headers=None):
    from patchquest.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers or {})


class TestRepoPathPolicy:
    @pytest.mark.parametrize("bad", ["/", "/etc", "/etc/ssh", "/usr/lib", "/proc", "/nonexistent-dir-xyz", "", "~"])
    def test_rejected(self, bad):
        with pytest.raises(RepoPathError):
            validate_repo_path(bad)

    def test_file_is_not_a_repo(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(RepoPathError):
            validate_repo_path(str(f))

    def test_credentials_directory_rejected(self, tmp_path, monkeypatch):
        import os
        ssh = os.path.expanduser("~/.ssh")
        if os.path.isdir(ssh):
            with pytest.raises(RepoPathError):
                validate_repo_path(ssh)

    def test_symlink_to_system_dir_rejected(self, tmp_path):
        link = tmp_path / "innocent"
        link.symlink_to("/etc")
        with pytest.raises(RepoPathError):
            validate_repo_path(str(link))

    def test_allowed_roots_enforced(self, tmp_path):
        inside, outside = tmp_path / "work" / "p", tmp_path / "other"
        inside.mkdir(parents=True), outside.mkdir()
        cfg = AppConfig()
        cfg.safety.allowed_roots = [str(tmp_path / "work")]
        set_config(cfg)
        assert validate_repo_path(str(inside)) == str(inside.resolve())
        with pytest.raises(RepoPathError):
            validate_repo_path(str(outside))


class TestStartupPolicy:
    def test_loopback_detection(self):
        assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
        assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")

    def test_refuses_public_bind_without_token(self):
        with pytest.raises(RuntimeError, match="without authentication"):
            check_startup_policy("0.0.0.0")

    def test_public_bind_allowed_with_token(self, monkeypatch):
        monkeypatch.setenv("PATCHQUEST_API_TOKEN", "x" * 32)
        check_startup_policy("0.0.0.0")

    def test_default_config_binds_loopback(self):
        assert AppConfig().host == "127.0.0.1"


class TestHttpBoundary:
    @pytest.mark.asyncio
    async def test_run_against_system_directory_is_rejected(self):
        async with client() as c:
            r = await c.post("/api/runs", json={"repo_path": "/etc", "task": "inspect", "provider": "mock"})
        assert r.status_code == 400 and "system location" in r.json()["detail"]
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_token_required_when_configured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATCHQUEST_API_TOKEN", "s3cret-token-value")
        async with client() as c:
            assert (await c.get("/api/health")).status_code == 200  # liveness stays open
            assert (await c.get("/api/runs")).status_code == 401
            assert (await c.get("/api/runs", headers={"Authorization": "Bearer wrong"})).status_code == 401
            assert (await c.get("/api/runs", headers={"Authorization": "Bearer s3cret-token-value"})).status_code == 200
            r = await c.post("/api/runs", json={"repo_path": str(tmp_path), "task": "x"})
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_foreign_host_header_rejected(self):
        from patchquest.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evil.example.com") as c:
            r = await c.get("/api/health")
        assert r.status_code == 400  # DNS-rebinding defence

    @pytest.mark.asyncio
    async def test_run_lifecycle_cleans_up_and_supports_cancel(self, tmp_path):
        from patchquest.application import get_service

        service = get_service()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# r\n")
        async with client() as c:
            r = await c.post("/api/runs", json={"repo_path": str(repo), "task": "Summarize. Do not modify files.",
                                                "provider": "mock"})
            assert r.status_code == 200
            run_id = r.json()["id"]
            for _ in range(200):
                if not service.is_active(run_id):
                    break
                await asyncio.sleep(0.05)
            assert not service.is_active(run_id) and run_id not in service._tasks
            assert (await c.post(f"/api/runs/{run_id}/cancel")).status_code == 409  # finished: nothing to cancel


class TestRecovery:
    def test_in_flight_runs_are_marked_interrupted_and_approvals_expire(self):
        rid = str(uuid.uuid4())
        now = now_iso()
        with get_db() as conn:
            conn.execute("INSERT INTO runs (id, repo_path, task, status, current_phase, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                         (rid, "/x", "t", "running", "testing", now, now))
            conn.execute("INSERT INTO approvals (id, run_id, type, command, reason, status, created_at) VALUES (?,?,?,?,?,?,?)",
                         ("a1", rid, "command", "x", "r", "pending", now))
        assert recover_interrupted_runs() == 1
        with get_db() as conn:
            run = conn.execute("SELECT status, outcome FROM runs WHERE id = ?", (rid,)).fetchone()
            appr = conn.execute("SELECT status FROM approvals WHERE id = 'a1'").fetchone()
            ev = conn.execute("SELECT type, phase FROM run_events WHERE run_id = ?", (rid,)).fetchall()
        assert run["status"] == "interrupted" and appr["status"] == "expired"
        assert ("run_interrupted", "testing") in [(e["type"], e["phase"]) for e in ev]
        assert ev[0]["type"] == "run_state_changed"  # the typed transition is recorded first
        assert recover_interrupted_runs() == 0  # idempotent


class TestApprovalEndpoints:
    @staticmethod
    def pending(run_id="api-run", command="touch x.txt", effect=None):
        from patchquest.domain.effects import SideEffect
        from patchquest.persistence import approvals
        from tests.support.db import insert_run

        insert_run(run_id)
        with get_db() as conn:
            return approvals.request(conn, run_id, kind="command", reason="r", command=command,
                                     side_effect=effect or SideEffect.WORKSPACE_WRITE, timeout_s=60)

    @pytest.mark.asyncio
    async def test_list_then_decide(self):
        aid = self.pending()
        async with client() as c:
            listed = (await c.get("/api/runs/api-run/approvals")).json()
            assert [a["id"] for a in listed] == [aid] and listed[0]["side_effect"] == "WORKSPACE_WRITE"
            r = await c.post(f"/api/runs/api-run/approvals/{aid}", json={"decision": "APPROVE_FOR_RUN", "note": "ok"})
            assert r.status_code == 200 and r.json() == {"status": "approved", "decision": "APPROVE_FOR_RUN"}
            assert (await c.get("/api/runs/api-run/approvals")).json() == []

    @pytest.mark.asyncio
    async def test_errors_have_stable_codes_and_statuses(self):
        from patchquest.domain.effects import SideEffect

        aid = self.pending()
        async with client() as c:
            assert (await c.post(f"/api/runs/api-run/approvals/{aid}", json={"decision": "MAYBE"})).status_code == 422
            r = await c.post("/api/runs/api-run/approvals/nope", json={"decision": "DENY"})
            assert r.status_code == 404 and r.json()["detail"]["code"] == "approval_not_found"
            r = await c.post("/api/runs/missing/approvals/x", json={"decision": "DENY"})
            assert r.status_code == 404
            await c.post(f"/api/runs/api-run/approvals/{aid}", json={"decision": "DENY"})
            r = await c.post(f"/api/runs/api-run/approvals/{aid}", json={"decision": "APPROVE_ONCE"})
            assert r.status_code == 409 and r.json()["detail"]["code"] == "approval_already_decided"
            risky = self.pending(command="curl http://x", effect=SideEffect.EXTERNAL_WRITE)
            r = await c.post(f"/api/runs/api-run/approvals/{risky}", json={"decision": "APPROVE_FOR_RUN"})
            assert r.status_code == 422 and r.json()["detail"]["code"] == "decision_not_allowed"

    @pytest.mark.asyncio
    async def test_legacy_yes_no_endpoints_still_work(self):
        a, b = self.pending(), self.pending()
        async with client() as c:
            assert (await c.post("/api/runs/api-run/approve", json={"approval_id": a, "approved": True})).json()["status"] == "approved"
            assert (await c.post("/api/runs/api-run/reject", json={"approval_id": b, "approved": True})).json()["status"] == "denied"

    @pytest.mark.asyncio
    async def test_approvals_require_the_token_when_one_is_configured(self, monkeypatch):
        cfg = AppConfig()
        set_config(cfg)
        monkeypatch.setenv(cfg.api_token_env, "s3cret-token-value")
        aid = self.pending()
        async with client() as c:
            assert (await c.post(f"/api/runs/api-run/approvals/{aid}", json={"decision": "APPROVE_ONCE"})).status_code == 401
            ok = await c.post(f"/api/runs/api-run/approvals/{aid}", json={"decision": "DENY"},
                              headers={"Authorization": "Bearer s3cret-token-value"})
            assert ok.status_code == 200


class TestRunApiExtras:
    @pytest.mark.asyncio
    async def test_overrides_at_creation_are_validated_and_stored(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        async with client() as c:
            ok = await c.post("/api/runs", json={"repo_path": str(repo), "task": "read only: explain", "overrides": {"agent.max_model_calls": 7}})
            assert ok.status_code == 200
            bad = await c.post("/api/runs", json={"repo_path": str(repo), "task": "t", "overrides": {"safety.approval_timeout_seconds": 1}})
            assert bad.status_code == 400 and "cannot be overridden" in bad.json()["detail"]
        with get_db() as conn:
            assert conn.execute("SELECT overrides_json FROM runs WHERE id = ?", (ok.json()["id"],)).fetchone()[0] == '{"agent.max_model_calls": 7}'

    @pytest.mark.asyncio
    async def test_listing_pages_backwards_with_a_cursor(self):
        from tests.support.db import insert_run

        for i in range(5):
            insert_run(f"p{i}")
            with get_db() as conn:
                conn.execute("UPDATE runs SET created_at = ? WHERE id = ?", (f"2026-01-0{i + 1}T00:00:00+00:00", f"p{i}"))
        async with client() as c:
            first = (await c.get("/api/runs?limit=2")).json()
            assert [r["id"] for r in first] == ["p4", "p3"]
            second = (await c.get(f"/api/runs?limit=2&before={first[-1]['created_at']}")).json()
            assert [r["id"] for r in second] == ["p2", "p1"]
            assert (await c.get("/api/runs?limit=0")).status_code == 422 and (await c.get("/api/runs?limit=999")).status_code == 422

    @pytest.mark.asyncio
    async def test_pending_approvals_say_whether_they_can_be_remembered(self):
        from patchquest.domain.effects import SideEffect
        from patchquest.persistence import approvals
        from tests.support.db import insert_run

        insert_run("g1")
        with get_db() as conn:
            safe = approvals.request(conn, "g1", kind="command", reason="r", command="touch a", side_effect=SideEffect.WORKSPACE_WRITE, timeout_s=60)
            risky = approvals.request(conn, "g1", kind="command", reason="r", command="curl x", side_effect=SideEffect.EXTERNAL_WRITE, timeout_s=60)
            promote = approvals.request(conn, "g1", kind="promote_patch", reason="r", side_effect=SideEffect.WORKSPACE_WRITE, timeout_s=60)
        async with client() as c:
            grantable = {a["id"]: a["grantable"] for a in (await c.get("/api/runs/g1/approvals")).json()}
        assert grantable == {safe: True, risky: False, promote: False}
