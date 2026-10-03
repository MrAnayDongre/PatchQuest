# Workflows

A workflow is a small, closed, versioned graph that starts from a trigger, runs agents and actions, stops for people,
waits for the world, and survives restarts. It is validated before it is saved and again before it runs.

```yaml
name: issue-to-proposal
trigger: {type: github.issues.labeled, filter: {payload.label: agent-ready}}
variables: {repo: {default: /work/app}}
nodes:
  - {id: fix, type: agent, config: {task: "Resolve: {{trigger.payload.title}}", repo: "{{vars.repo}}", provider: sglang}}
  - {id: ok, type: condition, config: {if: {left: "{{nodes.fix.output.verdict}}", op: eq, right: passed}}}
  - {id: review, type: approval, config: {message: "Post it?", timeout_s: 86400}}
  - {id: post, type: action, config: {action: github.comment, params: {issue_number: "{{trigger.payload.number}}", body: "..."}}}
  - {id: done, type: end}
edges: [{from: fix, to: ok}, {from: ok, to: review, when: "true"}, {from: ok, to: done, when: "false"},
        {from: review, to: post, when: approved}, {from: review, to: done, when: denied}, {from: post, to: done}]
```

## Nodes

| Node | Does | Waits durably? |
|---|---|---|
| `agent` | runs a full PatchQuest run (same pipeline, checkpoints, approvals) | yes, for the child run |
| `action` | performs a catalogued connector action | no (retried centrally on transient errors) |
| `condition` | branches on a structured comparison | no |
| `approval` | go/no-go from a person, optional `timeout_s` (means denied) | yes |
| `wait_event` | waits for an external event, optional filter and timeout | yes |
| `timer` | waits a duration | yes |
| `end` | finishes (`result: success|failure`), cancelling anything still in flight | - |

Edges carry an optional label: `true/false` (condition), `approved/denied/timeout` (approval), `failed` (a node with
`on_failure: continue`). There is no expression language: templates are `{{dotted.paths}}`, comparisons are
`eq ne gt ge lt le in contains exists` plus `all/any/not`, and trigger filters are path -> value or `{in: [...]}`.

## Validation (all before anything runs)

Structure (unknown fields, duplicate/dangling ids, no entry, unreachable nodes), conditions need both branches,
**loops must be bounded** (`max_visits`), references must name something that exists *and runs earlier*, no literal
secrets anywhere, event-triggered workflows cannot require variables an event cannot supply, and: **any action that writes
outside the sandbox must sit behind an approval on every path to it.** Unknown actions are rejected. The same checks run
again when a run starts, and the engine refuses an external write that has no recorded approver, so editing a stored
definition cannot bypass the gate.

## Durability

Every step is a database row. `advance(run)` can be called again after any crash and continues exactly where it was.
Waiting holds no worker, task or thread; `tick()` wakes due timers and timeouts and polls child runs; `deliver_event()`
wakes waiting runs and starts matching workflows.

- A node runs at most `max_visits` times; a loop that runs out of visits fails the run loudly.
- Each action carries an idempotency key (`run:node:visit`) recorded **before** it is performed. After a crash the engine
  asks the connector whether it already happened (`find_existing`) before repeating it; a non-idempotent action it
  cannot reconcile stops for a person (`uncertain`, resolved with `happened | retry | failed`).
- One external event never starts the same workflow twice (unique trigger key per workflow).
- A run stays on the workflow version it started with; saving an edit creates a new version.
- If an event cannot start one workflow (e.g. it no longer validates), the failure is audited and the event still
  reaches the others.

## Templates (`patchquest workflows templates`)

`issue-to-proposal`, `failed-ci-repair`, `dependency-upgrade`, `oncall-investigation`. Each validates against the
action catalogue and runs end to end in the tests (with fake or mocked connectors). Honest limits: opening a pull
request needs a branch-push action PatchQuest does not have, so `issue-to-proposal` posts the proposal as a comment; a CI
event carries no PR number, so `failed-ci-repair` records the proposed fix in the workflow history. `oncall-investigation`
needs a Slack connector, which does not exist yet (only signature verification does): without it the reply step fails
with a clear "no slack connector" error. `workflows templates` shows which connectors each needs. Bind a template with
`patchquest workflows save --template NAME --var repo=/path`.

## Interfaces

```
patchquest workflows templates | list | runs | validate FILE | save FILE|--template NAME | start ID | show RUN
patchquest workflows decide RUN NODE approve|deny | cancel RUN
GET/POST /api/workflows ... /api/workflows/runs/{id}/steps/{node}/decision   (tenant-scoped; see security.md)
```
Roles: `WORKFLOW_MANAGE` (developer and up) defines workflows; `APPROVAL_DECIDE` answers gates; service accounts can start but not approve.

## Not built

Parallel branches (a node with several outgoing edges runs them one after another), a drag-and-drop builder UI, scheduled
triggers, importing OpenAPI operations as actions, a Slack/Jira/Linear/Notion connector, a branch-push action.
