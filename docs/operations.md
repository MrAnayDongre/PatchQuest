# Operating PatchQuest

## Is it healthy?
```bash
patchquest doctor                   # installation, config, safety boundaries
patchquest engines                  # local model engines: running, model, context limit, latency
patchquest queue                    # queued runs, live leases, expired leases, oldest wait
patchquest metrics --window 24h --by model
curl localhost:8000/ready           # probe
```
Expired leases with no worker alive mean runs are waiting to be recovered by the next worker that polls.

## A run is stuck or failed
```bash
patchquest status RUN               # status, outcome, failure kind, budget
patchquest inspect RUN              # the event timeline        patchquest events RUN --json
patchquest checkpoints RUN          # what can be resumed from, and whether each verifies
patchquest resume RUN --plan        # what resuming would do, and what it cannot be sure of
patchquest resume RUN               # ... then do it (--accept-drift / --rollback when a person must decide)
patchquest fork RUN --from 6 --model other --set agent.max_model_calls=80
patchquest replay RUN --mode state  # verify the history is consistent (no side effects)
```
Exit code `4` means *a person must decide first*. A worker that finds it cannot safely resume a dead worker's run
leaves it `interrupted` and writes `worker.recovery_blocked` to the audit log (`patchquest admin audit`).

## Traces
`patchquest trace RUN` prints an OTLP/JSON trace (run -> phases -> model calls, commands) derived from the ledger;
`--endpoint http://collector:4318/v1/traces` posts it. Re-exporting gives the same trace ids.

## Access
`patchquest admin init|token create|token revoke|token list|audit`. Disabling a principal disables its tokens at once.

## Metrics definitions
Success = a completed run whose outcome is `applied`, `read_only` or `no_changes`. Validation pass = verdict `passed` or
`no_tests` among runs with a verdict. First-pass = a success with no repair round, patch retry or resume. Resume success =
completed among runs resumed at least once. Cost appears only for models listed in `pricing`; local engines report tokens
and compute seconds. Empty denominators are `null`, never zero.

## Operational metrics
`patchquest metrics --operations` (and `GET /api/metrics/operations?window=7d`) reports on the machinery rather than the agents:

| Block | What it tells you | Derived from |
|---|---|---|
| `context` | files and tokens selected per run; **precision** = selected files that were also patched / files selected (a falling trend means noisier context) | `context_selected`, `patch_applied` events |
| `memory` | runs using memory, items considered/selected, tokens injected, rejected as stale / low confidence, duplicates avoided, withheld by policy, profile changes, facts invalidated | `memory_selected` and related events |
| `policy` | commands denied by policy, model use denied, commands blocked by the command gate, approvals requested | `command_blocked`, `model_denied`, `approval_requested` |
| `workers` | runs another worker recovered after a lease expired; current queue depth (install-wide) | `run_interrupted` by `recovery`, `queue.stats` |
| `workflows` | per action (`slack.post_message`, ...): steps, failures, latency of finished steps; inbound deliveries by source and outcome | workflow steps/events, `connector_events` |
| `plugins` | per plugin: calls, failures, denials, quarantines, latency | `plugin_events` |

The report follows the caller's workspaces. Plugin figures and the shared queue are installation-wide, so the API shows
them to organisation owners only. As elsewhere, nothing is invented: an empty denominator is `null`.
