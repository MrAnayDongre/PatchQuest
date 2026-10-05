"""Approval decisions and states, shared by the UI, API, CLI and runtime."""

from __future__ import annotations

from enum import StrEnum


class Decision(StrEnum):
    APPROVE_ONCE = "APPROVE_ONCE"
    APPROVE_FOR_RUN = "APPROVE_FOR_RUN"  # remember this exact command for the rest of the run (sandbox-only effects)
    DENY = "DENY"
    MODIFY = "MODIFY"  # approve, but run the command as the approver edited it
    CANCEL_RUN = "CANCEL_RUN"  # deny and stop the run


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


STATUS_FOR: dict[Decision, ApprovalStatus] = {
    Decision.APPROVE_ONCE: ApprovalStatus.APPROVED,
    Decision.APPROVE_FOR_RUN: ApprovalStatus.APPROVED,
    Decision.MODIFY: ApprovalStatus.APPROVED,
    Decision.DENY: ApprovalStatus.DENIED,
    Decision.CANCEL_RUN: ApprovalStatus.CANCELLED,
}
APPROVING = frozenset({Decision.APPROVE_ONCE, Decision.APPROVE_FOR_RUN, Decision.MODIFY})


class ApprovalError(RuntimeError):
    """A decision could not be recorded; the message says why and is safe to show."""

    code = "approval_error"


class ApprovalNotFound(ApprovalError):
    code = "approval_not_found"


class AlreadyDecided(ApprovalError):
    code = "approval_already_decided"


class DecisionNotAllowed(ApprovalError):
    code = "decision_not_allowed"
