# Connectors

A connector links PatchQuest to an outside system. **Triggers** (events coming in) and **actions**
(effects going out) are separate halves of one contract: `patchquest/connectors/base.py`.

## Status: what is verified

| Piece | Status |
|---|---|
| HMAC verification, replay window, dedup, size limit, SSRF guard, retry/dead-letter, grants | Unit/integration tested, no network needed |
| GitHub connector (`github.py`) | **MOCKED_PROTOCOL only.** Tested against `testing/mock_server.py`, our reading of GitHub's public docs. **Never run against live GitHub / GHE.** |
| Slack | Only the signature scheme and a delivery builder exist (no connector). Not verified live. |

Tests that rely on a simulator carry `pytestmark = pytest.mark.mocked_protocol` and say so in their docstring.

## Contract

- `ConnectorSpec`: name, version, trigger types, `ActionSpec`s (name, `SideEffect`, `requires_approval`), required scopes.
- `requires_approval` defaults to `True`; it can only be `False` for `PURE`/`READ_ONLY`/`NETWORK_READ`. Writes and `UNKNOWN` can never waive it.
- `Connector.perform(action, *, idempotency_key, grant)` is a template method: it checks the `ApprovalGrant`
  (matching action and key, approver set, tz-aware and unexpired), then calls `find_existing(key)` for
  side-effecting actions (crash reconciliation), then `_execute`. Connectors implement `_execute`, so they cannot skip the check.
- Actions are frozen, `extra=forbid` pydantic models, never free-form strings.
- `normalize(raw, headers) -> EventEnvelope` (frozen, JSON round-trip, payload <= 256 KiB). `verify` returns a `SignatureStatus`.
- Credentials are `SecretRef`s (an env var name) resolved at call time; values are never stored on an object.

## Trust boundaries

1. Network -> `WebhookReceiver`: size limit, then signature (constant-time), then normalisation, then dedup insert. Nothing is stored unless the delivery is authentic and well formed.
2. Envelope payload text is **untrusted data** (prompt-injection capable).
3. `ApprovalGrant` is a capability value minted by the approval engine. It is not unforgeable inside one Python process; it prevents accidents, not malicious connector code. It is not bound to the action parameters (only name + key): bind a params digest when the approval engine lands.
4. Outbound: every URL, and every redirect hop, passes `ssrf.check_url`.

## SSRF limits

`check_url` blocks non-http(s), userinfo, backslashes/control chars, `localhost`, and any address that is not globally routable
(RFC1918, loopback, link-local/metadata, CGNAT, multicast, reserved, IPv6 ULA, IPv4-mapped/compatible, NAT64, 6to4), including decimal/hex/octal/short IPv4 spellings.
DNS names are resolved and **every** answer must be public. An allowlist (`example.com`, `*.example.com`) only narrows; it never waives address checks.
Redirects are followed manually (cap 3), `Authorization`/`Cookie` are dropped on a host change, responses are capped at 1 MiB.

**Not closed: TOCTOU.** DNS can change between our check and the socket connect (rebinding). The guard narrows the window; it does not eliminate it.
Pinning the connection to the validated IP (with Host/SNI preserved) is *not implemented*. Pair this with network egress rules.

## Delivery semantics

- Inbound dedup key `(source, external_id)`; redelivery returns the original outcome. Ordering is not guaranteed: compare `envelope.timestamp`.
- Outbound uses `runtime.retry.decide`/`backoff` with `Origin.CONNECTOR`. `Retry-After` is honoured (capped at 120 s); 401/403 never retry;
  deterministic 4xx (400, 404, 410, 422...) go straight to `DEAD`; 408/409/425/429/5xx and timeouts retry until the policy cap, then `DEAD` (visible, never retried again).
  An attempt is counted when claimed, so a crash cannot cause unbounded retries. Receivers dedupe on `X-PatchQuest-Delivery`.
- GitHub search is eventually consistent: `find_existing` may miss an object created moments earlier.

## Add a connector in under 30 lines

```python
class Ping(Action):
    name = "ping"
    target: str

class Chat(Connector):
    spec = ConnectorSpec("chat", "1", ("message.created",), (ActionSpec("ping", SideEffect.EXTERNAL_WRITE),))

    def verify(self, headers, body):
        return verify_github_signature(self._secret(), headers, body)   # or Slack's, or NOT_APPLICABLE
    def normalize(self, raw, headers):
        ...  # parse; raise MalformedEvent / UnsupportedEvent; return EventEnvelope(...)
    def find_existing(self, idempotency_key):
        ...  # search the remote system for an object carrying the key, else None
    def _execute(self, action, idempotency_key):
        ...  # SafeHttp request; embed the key where the remote can search for it
```

Register with `ConnectorRegistry().register(Chat())`, route inbound HTTP through `WebhookReceiver.receive`.

## Database

`patchquest.connectors.migration.MIGRATION` (version 8) creates `connector_events` and `webhook_deliveries`; it is idempotent.
