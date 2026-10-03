from __future__ import annotations

import hashlib
import hmac
import random

import pytest

from patchquest.connectors.base import MissingCredential, SecretRef
from patchquest.connectors.outbound import OutboundWebhooks
from patchquest.connectors.ssrf import SafeHttp, SSRFBlocked
from patchquest.connectors.testing.mock_server import EXPIRED_CREDENTIAL, TIMEOUT, Fault, ScriptedEndpoint, rate_limited
from patchquest.runtime.retry import RetryPolicy, backoff
from tests.unit.connectors.conftest import FakeClock, resolver

POLICY = RetryPolicy(max_attempts=3, base_delay_s=1.0, max_delay_s=30.0)
URL = "https://hooks.example.com/in"
ENV = {"WH": "outbound-secret"}


def _setup(allowlist=None):
    ep, clock = ScriptedEndpoint(), FakeClock()
    http = SafeHttp(transport=ep.transport(), resolve=resolver(), allowlist=allowlist)
    return ep, clock, OutboundWebhooks(SecretRef("WH"), http=http, clock=clock, rng=random.Random(1), policy=POLICY, environ=ENV)  # noqa: S311


def test_delivers_with_verifiable_signature_and_stable_id():
    ep, _, hooks = _setup()
    did = hooks.enqueue(URL, "run.completed", {"run": "r1"})
    assert [d.status for d in hooks.deliver_due()] == ["DELIVERED"]
    req = ep.requests[0]
    stamp = req.headers["x-patchquest-timestamp"]
    expected = hmac.new(b"outbound-secret", stamp.encode() + b"." + req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-patchquest-signature"] == f"sha256={expected}"
    assert req.headers["x-patchquest-delivery"] == did
    assert hooks.deliver_due() == []  # delivered ones are never sent again


def test_retry_schedule_uses_central_backoff_then_dead_after_cap():
    ep, clock, hooks = _setup()
    ep.script.push(*[Fault(status=503)] * 5)
    did = hooks.enqueue(URL, "e", {})
    rng = random.Random(1)  # noqa: S311
    for attempt, expected_state in ((1, "PENDING"), (2, "PENDING"), (3, "DEAD")):
        assert hooks.get(did).status == "PENDING"
        out = hooks.deliver_due()
        assert out[0].status == expected_state and out[0].attempts == attempt
        if expected_state == "PENDING":
            assert hooks.deliver_due() == []  # not due yet: no early retry
            clock.advance(backoff(POLICY, attempt, rng) + 0.001)
    final = hooks.get(did)
    assert final.status == "DEAD" and "gave up after 3 attempts" in (final.last_error or "")
    clock.advance(10_000)
    assert hooks.deliver_due() == [] and len(ep.requests) == 3


def test_retry_after_is_honoured_exactly():
    ep, clock, hooks = _setup()
    ep.script.push(rate_limited("7"))
    did = hooks.enqueue(URL, "e", {})
    hooks.deliver_due()
    clock.advance(6.9)
    assert hooks.deliver_due() == []
    clock.advance(0.2)
    assert hooks.deliver_due()[0].status == "DELIVERED"
    assert hooks.get(did).attempts == 2


def test_timeout_is_retried():
    ep, clock, hooks = _setup()
    ep.script.push(TIMEOUT)
    hooks.enqueue(URL, "e", {})
    assert hooks.deliver_due()[0].status == "PENDING"
    clock.advance(31)
    assert hooks.deliver_due()[0].status == "DELIVERED"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_client_errors_other_than_429_are_dead_immediately(status):
    ep, _, hooks = _setup()
    ep.script.push(Fault(status=status))
    did = hooks.enqueue(URL, "e", {})
    assert hooks.deliver_due()[0].status == "DEAD"
    assert len(ep.requests) == 1 and hooks.get(did).attempts == 1


def test_expired_credential_is_not_retried():
    ep, _, hooks = _setup()
    ep.script.push(EXPIRED_CREDENTIAL)
    hooks.enqueue(URL, "e", {})
    out = hooks.deliver_due()[0]
    assert out.status == "DEAD" and "CONNECTOR_AUTH" in (out.last_error or "")


def test_ssrf_guard_at_enqueue_and_at_delivery():
    ep, clock, hooks = _setup()
    for bad in ("http://127.0.0.1/", "http://169.254.169.254/", "file:///x", "http://good@evil.example/"):
        with pytest.raises(SSRFBlocked):
            hooks.enqueue(bad, "e", {})
    # DNS flips to a private address after enqueue: delivery re-checks and dead-letters without connecting
    rebinding = SafeHttp(transport=ep.transport(), resolve=resolver({"hooks.example.com": ["10.0.0.1"]}))
    hooks2 = OutboundWebhooks(SecretRef("WH"), http=SafeHttp(transport=ep.transport(), resolve=resolver()), clock=clock, policy=POLICY, environ=ENV)
    did = hooks2.enqueue(URL, "e", {})
    hooks2._http = rebinding
    assert hooks2.deliver_due()[0].status == "DEAD" and ep.requests == []
    assert "blocked URL" in (hooks2.get(did).last_error or "")


def test_allowlist_enforced():
    _, _, hooks = _setup(allowlist=["other.example.com"])
    with pytest.raises(SSRFBlocked, match="allowlist"):
        hooks.enqueue(URL, "e", {})


def test_missing_secret_fails_fast_and_oversize_body_refused():
    ep, clock, _ = _setup()
    hooks = OutboundWebhooks(SecretRef("NOPE"), http=SafeHttp(transport=ep.transport(), resolve=resolver()), clock=clock, environ={})
    with pytest.raises(MissingCredential):
        hooks.enqueue(URL, "e", {})
    _, _, ok = _setup()
    with pytest.raises(ValueError, match="exceeds"):
        ok.enqueue(URL, "e", {"x": "y" * 300_000})


def test_claim_is_exclusive_across_workers():
    ep, clock, hooks = _setup()
    did = hooks.enqueue(URL, "e", {})
    assert hooks._attempt(did) is True
    assert hooks._attempt(did) is False  # already delivered / claimed
    assert len(ep.requests) == 1


def test_repr_hides_secret():
    _, _, hooks = _setup()
    assert "outbound-secret" not in repr(hooks)
