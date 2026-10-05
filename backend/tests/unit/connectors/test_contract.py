from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from patchquest.connectors.base import (
    Action,
    ActionResult,
    ActionSpec,
    ApprovalGrant,
    ApprovalRequired,
    Connector,
    ConnectorRegistry,
    ConnectorSpec,
    SecretRef,
)
from patchquest.connectors.envelope import MAX_PAYLOAD_BYTES, EventEnvelope, SignatureStatus
from patchquest.domain.effects import SideEffect
from tests.unit.connectors.conftest import FakeClock


class Ping(Action):
    name = "ping"
    n: int


class Read(Action):
    name = "read"


class Fake(Connector):
    spec = ConnectorSpec("fake", "1", ("x.y",), (ActionSpec("ping", SideEffect.EXTERNAL_WRITE),
                                                 ActionSpec("read", SideEffect.NETWORK_READ, requires_approval=False)))

    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.executed: list[str] = []
        self.existing: ActionResult | None = None

    def normalize(self, raw, headers):
        raise NotImplementedError

    def verify(self, headers, body):
        return SignatureStatus.NOT_APPLICABLE

    def find_existing(self, idempotency_key):
        return self.existing

    def _execute(self, action, idempotency_key):
        self.executed.append(action.name)
        return ActionResult("1")


def _grant(clock, **kw):
    base = {"action": "ping", "idempotency_key": "k1", "decided_by": "alice", "expires_at": clock() + timedelta(minutes=5)}
    return ApprovalGrant(**{**base, **kw})


def test_write_requires_valid_unexpired_matching_grant():
    clock = FakeClock()
    c = Fake(clock)
    with pytest.raises(ApprovalRequired):
        c.perform(Ping(n=1), idempotency_key="k1", grant=None)
    for bad in (_grant(clock, action="other"), _grant(clock, idempotency_key="k2"), _grant(clock, decided_by=""),
                _grant(clock, expires_at=clock()), _grant(clock, expires_at=clock() - timedelta(seconds=1)),
                _grant(clock, expires_at=clock().replace(tzinfo=None))):
        with pytest.raises(ApprovalRequired):
            c.perform(Ping(n=1), idempotency_key="k1", grant=bad)
    assert c.executed == []
    assert c.perform(Ping(n=1), idempotency_key="k1", grant=_grant(clock)).created
    clock.advance(301)
    with pytest.raises(ApprovalRequired):  # the same grant has now expired
        c.perform(Ping(n=1), idempotency_key="k1", grant=_grant(clock, expires_at=clock() - timedelta(seconds=1)))


def test_read_action_needs_no_grant_and_skips_reconciliation():
    c = Fake(FakeClock())
    c.existing = ActionResult("old")
    assert c.perform(Read(), idempotency_key="k", grant=None).external_id == "1"


def test_reconciliation_returns_existing_without_performing():
    clock = FakeClock()
    c = Fake(clock)
    c.existing = ActionResult("old", "u")
    result = c.perform(Ping(n=1), idempotency_key="k1", grant=_grant(clock))
    assert (result.external_id, result.created) == ("old", False) and c.executed == []


def test_unknown_or_write_effect_cannot_waive_approval():
    assert ActionSpec("a", SideEffect.UNKNOWN).requires_approval is True
    assert ActionSpec("a", SideEffect.EXTERNAL_WRITE).requires_approval is True
    for effect in (SideEffect.UNKNOWN, SideEffect.EXTERNAL_WRITE, SideEffect.DESTRUCTIVE, SideEffect.REPOSITORY_WRITE,
                   SideEffect.HOST_MUTATION, SideEffect.WORKSPACE_WRITE):
        with pytest.raises(ValueError, match="cannot waive"):
            ActionSpec("a", effect, requires_approval=False)


def test_bad_inputs_and_unknown_action():
    clock = FakeClock()
    c = Fake(clock)
    for key in ("", "has space", "a-->b", "x" * 129):
        with pytest.raises(ValueError, match="idempotency_key"):
            c.perform(Ping(n=1), idempotency_key=key, grant=None)

    class Rogue(Action):
        name = "rogue"

    with pytest.raises(ValueError, match="no action"):
        c.perform(Rogue(), idempotency_key="k", grant=None)
    with pytest.raises(ValidationError):
        Ping(n="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Ping(n=1, extra=2)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="twice"):
        ConnectorSpec("dup", "1", (), (ActionSpec("a", SideEffect.PURE), ActionSpec("a", SideEffect.PURE)))


def test_registry_rejects_duplicates_and_looks_up():
    reg = ConnectorRegistry()
    c = Fake(FakeClock())
    reg.register(c)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(Fake(FakeClock()))
    assert reg.spec("fake") is c.spec and reg.names() == ["fake"]
    with pytest.raises(KeyError):
        reg.get("nope")


def test_secret_ref_never_holds_the_value():
    ref = SecretRef("MY_TOKEN")
    assert "s3cret" not in repr(ref)
    assert ref.resolve({"MY_TOKEN": "s3cret"}) == "s3cret"


def _env(**kw):
    base = {"source": "github", "type": "t", "external_id": "1", "timestamp": "2026-01-01T00:00:00+00:00",
            "workspace_id": "w", "payload": {"a": 1}, "signature_status": "VERIFIED"}
    return EventEnvelope(**{**base, **kw})


def test_envelope_roundtrip_frozen_and_capped():
    e = _env(actor="bob")
    assert EventEnvelope.from_json(e.to_json()) == e
    with pytest.raises(ValidationError):
        e.source = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="limit"):
        _env(payload={"x": "y" * MAX_PAYLOAD_BYTES})
    with pytest.raises(ValidationError):
        _env(timestamp="2026-01-01T00:00:00")  # naive
    with pytest.raises(ValidationError):
        _env(signature_status="MAYBE")
    with pytest.raises(ValidationError):
        _env(payload={"x": object()})
