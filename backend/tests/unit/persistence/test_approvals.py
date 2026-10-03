"""The approval engine: decisions, compare-and-set, expiry, run-scoped grants."""

import time

import pytest

from patchquest.database import get_db
from patchquest.domain.approvals import AlreadyDecided, ApprovalNotFound, Decision, DecisionNotAllowed
from patchquest.domain.effects import GRANTABLE, SideEffect
from patchquest.persistence import approvals, ledger
from tests.support.db import insert_run


@pytest.fixture(autouse=True)
def runs():
    insert_run("r1")
    insert_run("r2")


def ask(run="r1", command="touch notes.txt", effect=SideEffect.WORKSPACE_WRITE, kind="command", timeout=60.0):
    with get_db() as conn:
        return approvals.request(conn, run, kind=kind, reason="needs a person", command=command, side_effect=effect,
                                 risk="risky_ask", phase="testing", timeout_s=timeout)


def decide(approval_id, decision, run="r1", **kw):
    with get_db() as conn:
        return approvals.decide(conn, run, approval_id, decision, actor="user:ana", **kw)


def row(approval_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()


class TestRequest:
    def test_request_carries_what_a_reviewer_needs(self):
        a = ask(effect=SideEffect.HOST_MUTATION, command="pip install flask")
        r = row(a)
        assert (r["status"], r["side_effect"], r["risk"], r["phase"], r["requested_by"]) == (
            "pending", "HOST_MUTATION", "risky_ask", "testing", "runtime")
        assert r["expires_at"] > r["created_at"]

    def test_pending_lists_only_this_runs_open_requests(self):
        a, b = ask(), ask()
        ask(run="r2")
        decide(a, Decision.DENY)
        with get_db() as conn:
            assert [p["id"] for p in approvals.pending(conn, "r1")] == [b]


class TestDecisions:
    @pytest.mark.parametrize("decision,status,approved", [
        (Decision.APPROVE_ONCE, "approved", True), (Decision.APPROVE_FOR_RUN, "approved", True),
        (Decision.DENY, "denied", False), (Decision.CANCEL_RUN, "cancelled", False)])
    def test_each_decision_sets_the_right_status(self, decision, status, approved):
        a = ask()
        outcome = decide(a, decision, note="because")
        assert outcome.status.value == status and outcome.approved is approved
        r = row(a)
        assert (r["status"], r["decision"], r["decided_by"], r["note"]) == (status, decision.value, "user:ana", "because")
        assert r["resolved_at"]

    def test_decision_is_recorded_in_the_ledger_with_who_and_what(self):
        a = ask()
        decide(a, Decision.APPROVE_ONCE)
        with get_db() as conn:
            event = next(e for e in ledger.read(conn, "r1") if e["type"] == "approval_decided")
        assert event["actor"] == "user:ana" and event["payload"]["decision"] == "APPROVE_ONCE"
        assert event["payload"]["approval_id"] == a and event["phase"] == "testing"

    def test_first_decision_wins(self):
        a = ask()
        decide(a, Decision.DENY)
        with pytest.raises(AlreadyDecided, match="denied"):
            decide(a, Decision.APPROVE_ONCE)
        assert row(a)["status"] == "denied"

    def test_unknown_and_foreign_ids_change_nothing(self):
        a = ask(run="r2")
        with pytest.raises(ApprovalNotFound):
            decide("nonexistent", Decision.APPROVE_ONCE)
        with pytest.raises(ApprovalNotFound):  # real id, wrong run: a forged cross-run approval
            decide(a, Decision.APPROVE_ONCE, run="r1")
        assert row(a)["status"] == "pending"

    def test_an_expired_request_cannot_be_approved_late(self):
        a = ask(timeout=0.01)
        time.sleep(0.05)
        with pytest.raises(AlreadyDecided, match="expired"):
            decide(a, Decision.APPROVE_ONCE)
        with get_db() as conn:
            assert not [e for e in ledger.read(conn, "r1") if e["type"] == "approval_decided"]
            assert approvals.pending(conn, "r1") == []  # nothing a person could still act on
            assert approvals.expire(conn, a) is True  # the waiting run is who settles it

    def test_expire_does_not_override_a_decision(self):
        a = ask()
        decide(a, Decision.APPROVE_ONCE)
        with get_db() as conn:
            assert approvals.expire(conn, a) is False
        assert row(a)["status"] == "approved"

    def test_decision_event_and_status_commit_together(self):
        a = ask()
        with pytest.raises(RuntimeError), get_db() as conn:
            approvals.decide(conn, "r1", a, Decision.APPROVE_ONCE, actor="u")
            raise RuntimeError("crash before commit")
        assert row(a)["status"] == "pending"
        with get_db() as conn:
            assert not [e for e in ledger.read(conn, "r1") if e["type"] == "approval_decided"]


class TestGrants:
    def test_approve_for_run_remembers_that_exact_command_for_that_run_only(self):
        a = ask(command="touch  notes.txt")
        decide(a, Decision.APPROVE_FOR_RUN)
        with get_db() as conn:
            assert approvals.has_grant(conn, "r1", "touch notes.txt")  # spacing is not identity
            assert approvals.has_grant(conn, "r1", "touch 'notes.txt'")  # nor is quoting
            assert not approvals.has_grant(conn, "r1", "touch other.txt")
            assert not approvals.has_grant(conn, "r2", "touch notes.txt")

    @pytest.mark.parametrize("effect", sorted(set(SideEffect) - GRANTABLE, key=str))
    def test_dangerous_effects_can_never_be_remembered(self, effect):
        a = ask(effect=effect)
        with pytest.raises(DecisionNotAllowed, match="approve it once"):
            decide(a, Decision.APPROVE_FOR_RUN)
        assert row(a)["status"] == "pending"
        with get_db() as conn:
            assert not approvals.has_grant(conn, "r1", "touch notes.txt")

    def test_only_commands_with_text_can_be_granted(self):
        a = ask(kind="promote_patch", command=None, effect=SideEffect.WORKSPACE_WRITE)
        with pytest.raises(DecisionNotAllowed):
            decide(a, Decision.APPROVE_FOR_RUN)

    def test_a_denial_grants_nothing(self):
        a = ask()
        decide(a, Decision.DENY)
        with get_db() as conn:
            assert not approvals.has_grant(conn, "r1", "touch notes.txt")


class TestModify:
    def test_modify_returns_the_approvers_command(self):
        a = ask(command="make deploy")
        outcome = decide(a, Decision.MODIFY, modified_command="make test")
        assert outcome.approved and outcome.command == "make test"
        assert row(a)["modified_command"] == "make test"

    def test_modify_needs_a_command_and_a_command_approval(self):
        with pytest.raises(DecisionNotAllowed):
            decide(ask(), Decision.MODIFY)
        with pytest.raises(DecisionNotAllowed):
            decide(ask(), Decision.MODIFY, modified_command="   ")
        with pytest.raises(DecisionNotAllowed):
            decide(ask(kind="promote_patch", command=None), Decision.MODIFY, modified_command="x")

    def test_original_command_is_kept_for_the_audit_trail(self):
        a = ask(command="make deploy")
        decide(a, Decision.MODIFY, modified_command="make test")
        assert row(a)["command"] == "make deploy"
