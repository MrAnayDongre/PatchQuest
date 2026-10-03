# The demo

`patchquest demo` starts a self-contained PatchQuest with realistic data and nothing real in it. What is **real**: the pipeline (shadow
workspace, validation by the repository's own tests, repair rounds, approvals, checkpoints, the ledger), the workflow engine, the
policy engine, memory, metrics, webhook verification, encrypted-secret handling, the UI. What is **simulated**, and labelled as such
everywhere it appears: the model answers (scripted, deterministic) and the GitHub and Slack servers (in-process fakes behind the real
connectors). Nothing is sent to the internet.

```bash
pip install ./backend && (cd frontend && npm ci && npm run build)   # the UI is optional; the API works without it
patchquest demo                 # http://127.0.0.1:8765 ; data in ~/.patchquest/demo (never your real state)
patchquest demo reset           # delete the demo directory (it must carry the demo marker) and rebuild on the next start
patchquest demo trigger         # the live scenario below, from the terminal
patchquest demo transcript      # what the simulated GitHub and Slack actually received
```

## What is seeded

Two repositories (`payments-service`, Python; `web-checkout`, JavaScript), git-initialised, with real bugs and real tests, and six runs:
a fix, a fix that needed a repair round, a JavaScript fix, a read-only explanation, a patch whose tests still fail that a person **denied**, and
a second such patch **waiting for you** (open the run, read the diff, approve or deny). Plus a workspace policy (cloud models denied, ceilings on
model calls), a remembered fact and a preference for a repository, two integrations ("GitHub (simulator)", "Slack (simulator)") and the
workflow *issue-to-fix*.

## The live scenario (about three minutes)

1. **Command centre** (Home): active work, the run that needs you, outcomes, recent completions.
2. **Workflow** (Workflows -> issue-to-fix): trigger *GitHub issue labelled `agent-ready`* -> agent -> condition (validated?) -> **approval** -> comment
   on the issue -> Slack message. The definition is the same JSON the backend runs.
3. `patchquest demo trigger` - a signed webhook for issue #412 ("Prices like 19.99 are charged as 19.98") reaches `/hooks/<id>`; its signature is
   verified, it is deduplicated, and the workflow starts a real agent run.
4. **Run** (Runs): the plan, the context chosen and why, the model calls, the command that ran the tests, the patch moving *proposed -> validated*
   in an isolated workspace. The repository is untouched until the end.
5. **Approval**: the workflow waits. `patchquest demo transcript` shows GitHub and Slack have received nothing. Approve in the UI.
6. The comment appears on the simulated issue and the message in the simulated channel (`demo transcript`); the repository now contains the fix.
7. **Why** (run page): which preference or detection chose the test command, what memory was selected and what was rejected as stale.
8. **Metrics**: success, validation pass, first-pass, repair rounds, approval latency, tokens, failure classes, and the operations block (context
   precision, memory cost, policy denials, recoveries, per-action integration latency).
9. **Recovery** (terminal): kill a worker mid-run and watch another take the run over - `patchquest worker` twice against the same database, `kill -9` one;
   `docs/failure-recovery.md` and `tests/e2e/recovery` do the same under test. A paused worker that wakes up after losing its lease cannot write to the run.
10. **Replay / fork**: replay a completed run from recorded model output (no model call), fork it from a checkpoint with a different setting.
11. **Evaluation and trajectories**: `patchquest eval context`, `patchquest eval run`, `patchquest trajectory <run>` (a real run as a trajectory with its reward
    breakdown), `patchquest gym rollout`.
12. **Scale evidence**: `docs/benchmarks.md` (1,000 organisations, 10,000 runs, 100 concurrent runs; with the caveats printed beside the numbers).

## Honest limits of the demo

Model quality is not demonstrated: the scripted models always give the planned answer. The integrations are protocol simulators, never live services.
The timeline starts when the demo starts (history is not backdated, to keep the ledger honest). `patchquest demo trigger --label ui-built` fires a second workflow; each trigger restores the payments repository to its buggy state first. Run it on a machine you trust: `PATCHQUEST_DEMO=1`
adds endpoints that fabricate issues in the simulator.
