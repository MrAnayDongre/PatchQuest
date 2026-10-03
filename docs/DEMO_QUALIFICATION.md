# Demo qualification

Run on one machine, 2026-10-03. Everything below was produced by `scripts/demo_qualify.sh` (a clean reset, then the whole path against a real
`patchquest demo` server and a real headless Chromium) plus the checks listed under *Other checks*. The models, GitHub and Slack in the demo
are **simulated and labelled as such** (a banner on every page says so); the pipeline, ledger, workflow engine, policy, metrics and UI are the real ones.

```bash
scripts/demo_qualify.sh /tmp/pq-qualify        # reset -> start -> build a workflow in the browser -> trigger -> approve -> replay -> fork -> crash
patchquest demo reset && patchquest demo       # the reset and start commands it uses (demo directory: ~/.patchquest/demo, or --dir)
patchquest demo trigger [--label ui-built]     # a signed webhook; the repository is restored to its buggy state first, so it can be repeated
patchquest demo crash                          # SIGKILL a worker mid-run
```

## Gates

| Gate | Status | Evidence |
|---|---|---|
| DEMO_RESET | PASS | `demo reset` deletes only a directory carrying the demo marker; `demo start` rebuilds it: 6 seeded runs (5 finished, 1 waiting), 1 workflow, 2 simulator integrations (stages 01-03) |
| DEMO_END_TO_END | PASS | 16 stages, all PASS: reset, start, seed, build workflow in browser, signed webhook, live run, **nothing sent to GitHub/Slack before approval** (0/0), approval in the UI, 1 comment + 1 message per workflow after approval (2/2 for both), metrics, replay, fork, UI tour, trajectory, context evaluation, crash recovery |
| WORKFLOW_BUILDER_DEMO | PASS (with a limit) | In Chromium: dragged 7 steps from the palette, arranged them by dragging, drew 9 connections port-to-port, configured every step in the side panel, saw a bad name refused with a reason, saved (v1), **reloaded** (steps, connections and layout identical), changed a step and saved v2, the versions dialog lists both; then a signed webhook **started the workflow the browser built** and it ran through the real engine to completion. Limit: the flow is issue -> agent (which runs the tests) -> validated? -> approval -> GitHub comment -> Slack. There is **no pull-request step**: `github.create_pull_request` exists as an action but PatchQuest has no branch-push action yet |
| CONNECTOR_DEMO | PASS as MOCKED_PROTOCOL | Real connector code against in-process simulators of GitHub and Slack; the simulators' transcript is the evidence. **No live service was contacted** |
| FAULT_RECOVERY_DEMO | PASS | See below |
| REPLAY_DEMO | PASS | See below |
| FORK_DEMO | PASS | See below |
| METRICS_DEMO | PASS | See below |
| LOAD_EVIDENCE_REVIEW | PASS | See below |
| UI_POLISH | PASS (limits listed) | Copy pass over every page's text; defects fixed (see *Changes made by this qualification*) |
| VISUAL_QA | PASS (limits listed) | 12 pages x 3 viewports (1440, 1100, 390 wide) x light/dark in headless Chromium; no console errors, no failed requests other than aborted navigations, no horizontal overflow; keyboard focus: 119 focused controls over 7 pages all show a visible indicator |
| PRIVACY_REVIEW | PASS for the demo source and the assets captured so far | Demo data lives under `/tmp/pq-demo` (no user name in any path; sandboxes now live there too); git identity in demo repos is `Demo <demo@example.test>`; scans of the demo database, evidence logs, UI text and repositories for names, e-mail addresses, home paths, tokens, private IPs found nothing. **Video assets must be reviewed again when they exist** |
| SECRET_REVIEW | PASS | Pattern scan of the whole tracked tree: only the fake keys in security tests; no environment variables are shown in the UI except *references* (`{env: PATCHQUEST_DEMO_GITHUB_TOKEN}`), never values |
| VC_DEMO_GATE | PASS | All of the above |

## Crash recovery (FAULT_RECOVERY_DEMO)

`patchquest demo crash`: a worker process is started on a queued run, the run is planned and checkpointed, then the worker's model call hangs and the process is
killed with SIGKILL.

```
worker-1 (pid 1343049) is gone; its last checkpoint is #6 after 'analysis' (6 saved)
the database still says the run is 'running' owned by worker-1: nobody has noticed yet
worker-2: the lease expired 1.9s after the kill; run recovered as 'queued'
ledger: run_interrupted -> promotion_started -> promotion_completed -> patch_applied -> run_completed
result: completed/applied/passed; patch applied 1 time(s); repository fixed: True
evidence: planner model calls 1 (the plan was kept, not redone); duplicate side effects 0; finished by worker-2; resumed at phase 'patching'
```

* Recovered: yes. State preserved: yes (the planner ran once, in worker-1; worker-2 resumed at `patching`). Unsafe side effect duplicated: no (`DUPLICATE_SIDE_EFFECTS=0`, one `patch_applied`).
  Manual reconstruction: none; worker-2 found the run on its own after the lease expired.
* This runs in a throwaway database, not inside the UI session. Separately, restarting the demo server while a run waited for approval left it `interrupted`; its resume plan said
  `SAFE_RESUME ... Continue from phase 'final_report'` and resuming returned it to *waiting for approval* (observed by hand, not automated).
* Under test (not in this script): `backend/tests/e2e/recovery` (real SIGKILLs, SQLite and PostgreSQL), a paused worker that wakes after losing its lease cannot write, a container run with a worker killed mid-batch (once, by hand).

## Replay (REPLAY_DEMO)

Original run and its replay (`replay_run` differs, same task): 4 model calls in the original, 4 in the replay, **`LIVE_MODEL_CALLS_DURING_REPLAY=0`** (every replayed call came from the `recorded`
provider), comparison `matched: true`, the original's 62 events unchanged afterwards. State replay of the ledger also verified all phases. A replay never promotes to the repository.

## Fork (FORK_DEMO)

The denied tax run (tests failed, a person declined the patch) was forked from checkpoint 6 (after `analysis`) with **two controlled changes**: model `demo-tax-fixed` and `agent.promote_policy=never`.
The fork's patch validated (`verdict: passed`) where the parent's had not (`unresolved`); lineage records parent, checkpoint and kind `fork`; the parent's status, outcome and 70 events are unchanged;
`payments/tax.py` is byte-identical (nothing was promoted). The "stronger model" is a script written for this demonstration.

## Metrics (METRICS_DEMO)

What each number on the Metrics and Home pages is, in the demo:

| Shown | Category |
|---|---|
| runs, outcomes, task success, checks passed, first-pass, approvals and wait for approval, failure classes, workflow action counts, policy and memory counters, worker recoveries, context size and precision | **Real runtime data**, computed from the demo's own ledger and tables |
| time to finish (hundreds of ms), model response time (0 ms), tokens (0, "No token counts reported"), cost (none) | **Fixture-driven**: the model is scripted, so these say nothing about a real model. The banner states this |
| GitHub comments and Slack messages | **Simulated** services (the integrations are badged *Simulated*) |
| 1,000 organisations / 10,000 runs / 100 concurrent workers | **Synthetic load-test data**; these appear only in `docs/benchmarks.md`, never in the UI |

## Load evidence (LOAD_EVIDENCE_REVIEW)

From `docs/benchmarks.md` (read, not re-run). The strongest supported statement:

> PatchQuest's control plane was tested on one 20-core laptop (Intel Core Ultra 7 255HX, 30.8 GB RAM) with PostgreSQL 16 on the same machine running `fsync=off` and `synchronous_commit=off`:
> 1,000 simulated organisations, 5,000 users, 10,000 historical runs and 400,000 events, then 1,000 real lightweight runs (mock model, read-only task, tiny repository) drained by 100 concurrent
> in-process workers with 0 failures and 0 duplicate claims, 20.6 runs/s, queued to completed p50 24.5 s / p95 48.5 s; after a lease expired the run was recovered in p50 9.6 ms.

Not supported: durable-commit PostgreSQL numbers, more than one host, a network or TLS between workers and the database, real model or test workloads, "thousands of production teams".

## Live model quality

Qwen3-0.6B solved 0 of 14 corpus tasks in the latest live run. No GPU was used for this qualification. PatchQuest measures and attributes model quality separately from harness reliability; the weak result is
evidence that the evaluation does not manufacture success. It is a documented limitation, not a demo claim.

## Egress policy (closed in this pass)

Network reads and artifact disclosure are now scoped policy actions (`network.read.domain:<host>`, `artifact.disclose:<class>:<destination>`), enforced before data leaves: web search, workflow actions and plugins,
hosted model providers (source code) and `patchquest export`. `secret` is never disclosable. See [policy](policy.md). Tests: `tests/integration/test_egress_policy.py` (defaults, scope inheritance, lower-scope weakening,
cross-tenant, secrets, allowlists, explanation, and that the outbound call is never made) and a workflow-engine test that the connector is not called.

## Other checks

| Check | Result |
|---|---|
| ruff / mypy (241 files) | clean / clean |
| Backend, SQLite | 1821 passed, 18 skipped; coverage 86% of 18,515 statements |
| Backend, PostgreSQL 16 | 1831 passed, 8 skipped (the full run found a PostgreSQL-only bug in the new search-cache code path; fixed) |
| Frontend | typecheck clean, 329 tests, production build, browser smoke (`npm run smoke`) OK |

## Changes made by this qualification

Demo banner on every page (simulated parts are stated), sandboxes kept in the demo directory (a traceback in test output had shown the user's home path), workflow layout left-to-right (an arrow ran backwards),
repeatable trigger, fork-demo model, crash demo reports its own evidence, plain-language names (`repository write risk`, `recorded answers`, `this machine`, `GitHub`, `test commands`), a focus ring that framed the whole page,
`%` in SQL literals and `INSERT OR REPLACE` on PostgreSQL, a games card layout.

## Known limitations, stated as they are

| Item | Status |
|---|---|
| SSO/OIDC | designed, not implemented |
| Per-tenant quotas | not implemented |
| Object storage | artifacts live in the database/filesystem; no external backend |
| Kubernetes manifests | not provided (a Dockerfile and a compose file are) |
| Live connector validation | protocol and simulator tested only; no credentials were supplied |
| Multi-host | workers are separate processes sharing a database (tested on one host, including containers); never tested across machines |
| Accessibility | keyboard focus checked automatically; no assistive-technology audit; Chromium only |
| Workflow canvas | at laptop and phone widths the whole workflow fits at about a third of full size; use the List view or zoom |
| Pull requests | no branch-push action, so no automatic pull request |
| Egress policy | does not cover network use by commands inside a local-mode shadow workspace, nor connector reads other than `notion.read_page` |
| Model quality | weak on the live 0.6B model |
