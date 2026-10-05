# Approvals

The policy gate (`tools/command_risk.py`) decides whether a command is blocked, automatic, or needs a
person. Needing a person creates a durable approval request.

## Request

`operation`, command, reason, **risk**, **side-effect class**, phase, requester, expiry. Side-effect
classes: `PURE`, `READ_ONLY`, `NETWORK_READ`, `WORKSPACE_WRITE`, `REPOSITORY_WRITE`, `EXTERNAL_WRITE`,
`HOST_MUTATION`, `DESTRUCTIVE`, `UNKNOWN`. They are derived from the parsed argv (never from free text);
anything unrecognised, and any shell syntax, is `UNKNOWN`.

## Decisions

| Decision | Effect |
|---|---|
| `APPROVE_ONCE` | run it this time |
| `APPROVE_FOR_RUN` | also remember this exact command (parsed argv) for the rest of **this run** - only for `PURE`, `READ_ONLY`, `WORKSPACE_WRITE` |
| `DENY` | do not run it |
| `MODIFY` | run the approver's edited command instead; it must still pass the policy gate (a blocked command stays blocked) |
| `CANCEL_RUN` | deny and stop the run |

No answer within `safety.approval_timeout_seconds` is a denial.

## Guarantees

The first decision wins (compare-and-set on a pending, unexpired request); a late, duplicate, unknown or
cross-run decision changes nothing. The decision and its `approval_decided` event, attributed to the actor,
commit together. A grant never crosses runs and is never offered for effects that leave the sandbox.

## Surfaces

```
patchquest run ...                          # interactive prompt: [y]es once, [a]lways this run, [m]odify, [n]o, [c]ancel run
patchquest approve <run> <approval> --decision MODIFY --command "..."   # against a running server
GET  /api/runs/<run>/approvals              # pending requests
POST /api/runs/<run>/approvals/<id>         {"decision": "...", "note": "...", "modified_command": "..."}
```

Errors carry stable codes: `approval_not_found` (404), `approval_already_decided` (409),
`decision_not_allowed` (422).
