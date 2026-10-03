"""Roles, permissions, tokens, memberships and the audit log."""

import sqlite3
import time

import pytest

from patchquest.database import get_db
from patchquest.domain.identity import (
    LOCAL_WORKSPACE_ID,
    ROLE_PERMISSIONS,
    Forbidden,
    Permission,
    Principal,
    Role,
    authorize,
    may_grant,
)
from patchquest.persistence import identity as ids


class TestRoles:
    def test_every_role_is_defined_and_viewers_cannot_write(self):
        assert set(ROLE_PERMISSIONS) == set(Role)
        viewer = ROLE_PERMISSIONS[Role.VIEWER]
        assert Permission.RUN_READ in viewer and not viewer & {Permission.RUN_CREATE, Permission.RUN_CONTROL,
                                                                Permission.APPROVAL_DECIDE, Permission.SETTINGS_WRITE}

    def test_service_accounts_can_start_runs_but_never_approve_them(self):
        service = ROLE_PERMISSIONS[Role.SERVICE]
        assert Permission.RUN_CREATE in service and Permission.APPROVAL_DECIDE not in service
        assert Permission.TOKENS_MANAGE not in service

    def test_operators_control_and_approve_but_do_not_create(self):
        op = ROLE_PERMISSIONS[Role.OPERATOR]
        assert {Permission.RUN_CONTROL, Permission.APPROVAL_DECIDE} <= op and Permission.RUN_CREATE not in op

    def test_only_owners_and_admins_manage_people_and_tokens(self):
        for role in Role:
            manages = Permission.MEMBERS_MANAGE in ROLE_PERMISSIONS[role]
            assert manages == (role in (Role.OWNER, Role.ADMIN))

    def test_who_may_grant_what(self):
        assert may_grant(Role.OWNER, Role.OWNER) and may_grant(Role.OWNER, Role.ADMIN)
        assert not may_grant(Role.ADMIN, Role.OWNER) and not may_grant(Role.ADMIN, Role.ADMIN)
        assert may_grant(Role.ADMIN, Role.DEVELOPER) and not may_grant(Role.DEVELOPER, Role.VIEWER)

    def test_authorisation_is_per_workspace(self):
        p = Principal("p", "user", "ana", "org", {"ws_a": Role.DEVELOPER, "ws_b": Role.VIEWER})
        assert p.can(Permission.RUN_CREATE, "ws_a") and not p.can(Permission.RUN_CREATE, "ws_b")
        assert not p.can(Permission.RUN_READ, "ws_c")  # no membership, no access
        assert p.workspaces_with(Permission.RUN_CREATE) == ["ws_a"] and p.workspaces_with(Permission.RUN_READ) == ["ws_a", "ws_b"]
        authorize(p, Permission.RUN_READ, "ws_b")
        with pytest.raises(Forbidden, match=r"run\.create"):
            authorize(p, Permission.RUN_CREATE, "ws_b")

    def test_actor_names_identify_the_kind(self):
        assert Principal("p1", "service", "ci", "o").actor == "service:p1"


@pytest.fixture
def org():
    with get_db() as conn:
        o = ids.create_org(conn, "Acme")
        a, b = ids.create_workspace(conn, o, "a"), ids.create_workspace(conn, o, "b")
    return o, a, b


class TestTokens:
    def test_a_token_resolves_to_its_principal_with_workspace_roles(self, org):
        o, a, b = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            ids.set_role(conn, p, a, Role.DEVELOPER)
            ids.set_role(conn, p, b, Role.VIEWER)
            _, secret = ids.issue_token(conn, p, "laptop")
        with get_db() as conn:
            principal = ids.authenticate(conn, secret)
        assert principal.id == p and principal.roles == {a: Role.DEVELOPER, b: Role.VIEWER} and principal.org_id == o

    def test_the_secret_is_never_stored(self, org):
        o, a, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            _, secret = ids.issue_token(conn, p)
            dumped = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM api_tokens"))
        assert secret not in dumped and secret.startswith("pq_")
        assert ids.hash_token(secret) in dumped

    def test_tokens_are_long_unique_and_unguessable_in_shape(self, org):
        o, _, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            secrets_ = {ids.issue_token(conn, p)[1] for _ in range(20)}
        assert len(secrets_) == 20 and all(len(s) >= 40 for s in secrets_)

    @pytest.mark.parametrize("bad", ["", "pq_", "pq_wrong", "Bearer x", "' OR 1=1 --"])
    def test_unknown_tokens_are_rejected(self, bad):
        with get_db() as conn, pytest.raises(ids.UnknownToken):
            ids.authenticate(conn, bad)

    def test_revoked_tokens_stop_working_immediately(self, org):
        o, a, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            tid, secret = ids.issue_token(conn, p)
            ids.authenticate(conn, secret)
            assert ids.revoke_token(conn, tid) is True and ids.revoke_token(conn, tid) is False
            with pytest.raises(ids.UnknownToken):
                ids.authenticate(conn, secret)

    def test_expired_tokens_stop_working(self, org):
        o, _, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            _, secret = ids.issue_token(conn, p, expires_in_days=0.000001)
        time.sleep(0.2)
        with get_db() as conn, pytest.raises(ids.UnknownToken):
            ids.authenticate(conn, secret)

    def test_disabling_a_principal_disables_its_tokens(self, org):
        o, _, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            _, secret = ids.issue_token(conn, p)
            conn.execute("UPDATE principals SET disabled = 1 WHERE id = ?", (p,))
            with pytest.raises(ids.UnknownToken):
                ids.authenticate(conn, secret)

    def test_use_is_recorded_and_listing_never_exposes_secrets(self, org):
        o, _, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            _, secret = ids.issue_token(conn, p, "ci")
            ids.authenticate(conn, secret)
            listing = ids.list_tokens(conn)
        assert listing[0]["last_used_at"] and listing[0]["label"] == "ci" and secret not in str(listing)
        assert "token_hash" not in listing[0]


class TestMemberships:
    def test_a_principal_cannot_join_another_organisations_workspace(self, org):
        o, a, _ = org
        with get_db() as conn:
            other = ids.create_org(conn, "Other")
            outsider = ids.create_principal(conn, other, "eve")
            with pytest.raises(ValueError, match="own organisation"):
                ids.set_role(conn, outsider, a, Role.VIEWER)

    def test_changing_a_role_replaces_it(self, org):
        o, a, _ = org
        with get_db() as conn:
            p = ids.create_principal(conn, o, "ana")
            ids.set_role(conn, p, a, Role.VIEWER)
            ids.set_role(conn, p, a, Role.ADMIN)
            assert ids.load_principal(conn, p).roles == {a: Role.ADMIN}

    def test_unknown_principal_or_kind(self, org):
        o, a, _ = org
        with get_db() as conn:
            with pytest.raises(LookupError):
                ids.set_role(conn, "nobody", a, Role.VIEWER)
            with pytest.raises(ValueError):
                ids.create_principal(conn, o, "x", kind="robot")

    def test_the_local_workspace_exists_after_migration_and_owns_old_runs(self):
        with get_db() as conn:
            assert conn.execute("SELECT 1 FROM workspaces WHERE id = ?", (LOCAL_WORKSPACE_ID,)).fetchone()
            conn.execute("INSERT INTO runs (id, repo_path, task, created_at, updated_at) VALUES ('old','/r','t','n','n')")
            assert conn.execute("SELECT workspace_id FROM runs WHERE id = 'old'").fetchone()[0] == LOCAL_WORKSPACE_ID


class TestAudit:
    def test_entries_are_scoped_to_the_workspaces_the_reader_may_see(self, org):
        o, a, b = org
        with get_db() as conn:
            ids.audit(conn, "run.create", actor="user:1", workspace_id=a, target="r1")
            ids.audit(conn, "run.create", actor="user:2", workspace_id=b, target="r2")
            ids.audit(conn, "auth.failed", actor="anonymous", outcome="denied")
            assert [e["target"] for e in ids.read_audit(conn, [a])] == ["r1"]
            assert {e["action"] for e in ids.read_audit(conn, None)} == {"run.create", "auth.failed"}
            assert ids.read_audit(conn, []) == []  # a reader with no workspaces sees nothing

    def test_the_audit_log_is_append_only(self, org):
        o, a, _ = org
        with get_db() as conn:
            ids.audit(conn, "token.create", actor="user:1", workspace_id=a, detail={"label": "ci"})
        for sql in ("UPDATE audit_log SET outcome = 'ok'", "DELETE FROM audit_log"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"), get_db() as conn:
                conn.execute(sql)

    def test_cursor_and_detail_round_trip(self, org):
        o, a, _ = org
        with get_db() as conn:
            for i in range(3):
                ids.audit(conn, f"a{i}", actor="u", workspace_id=a, detail={"i": i})
            rows = ids.read_audit(conn, [a])
            assert [r["detail"]["i"] for r in rows] == [0, 1, 2]
            assert [r["action"] for r in ids.read_audit(conn, [a], after=rows[0]["id"])] == ["a1", "a2"]
