# Memory, preferences and repository profiles

PatchQuest remembers facts and honours preferences, but both are small, scoped, attributable and always below policy.

## Four different things

| | What it is | Who sets it | Can it grant anything? |
|---|---|---|---|
| **Configuration** | how an installation is set up (`config.yaml`, env) | the operator | n/a (see `patchquest config explain`) |
| **Policy** | what is allowed (`docs/policy.md`) | admins | it is the authority |
| **Preference** | a choice among allowed things | a person | **no**: it can pick or tighten; where it asks for less, policy still decides and the run says so |
| **Memory** | facts learned, with provenance | people, detectors, and (low authority) runs | **no**: advisory text; never executed |

## Memory model

A memory has a `kind`, an owning `scope`, a `key`, a structured `value`, and the provenance needed to judge it:
`source`, `reason`, `authored_by`, `confidence`, `created_at / updated_at / last_verified_at`, `expires_at`,
`evidence` (path -> hash of the files it was learned from), `version` and `supersedes`. Rows are tenant-owned
(`org_id`, `workspace_id`) and every read is filtered by owner in SQL.

- **Kinds in use:** `repository` (facts and the repository profile), `procedural` (a command that works),
  `preference`, `episodic` (what earlier runs did). `working` and `team` are reserved and refused: per-run state
  lives in the run's checkpoint, and there is no team entity yet.
- **Scopes in use:** organization, workspace, repository, user, workflow - only entities that exist. A repository is
  filed under its resolved path *inside one workspace*, so the same path in two workspaces is two separate scopes.
- **Sources and authority:** `user_explicit` 100 > `configuration` 80 > `repository_detected` 60 > `test_result` 55 >
  `ci` 50 > `accepted_patch`/`rejected_patch` 45 > `workflow_observation` 30 > `import` 20 > `agent_inference` 10.
  A write never replaces a record of higher authority (`kept_existing`); the same value seen again *reinforces*
  (confidence and verification time); a different value from equal or higher authority is a new version, and the
  old one is kept as `superseded`.
- **Who may write what:** preferences only from a person or configuration; procedures from those plus deterministic
  detection and test results; facts and episodes from any source. So text a model or a repository file produced can
  at most become a low-authority note, shown to models as *unverified*. A screen for instruction-like text
  ("ignore policy", "disable tests", "send the code to...") rejects it from non-human sources as defence in depth.
- **Secrets are never stored:** values that look like secrets are refused, and the refusal does not echo them.

### Confidence and freshness

`effective_confidence = confidence x freshness`. Freshness is 1.0 when verified, falling linearly to 0.5 at the end
of the source's time to live, and 0 once expired, stale or otherwise inactive. TTLs: none for people, configuration
and repository detection (they end when their evidence changes or someone removes them); 30 days for test results,
CI and workflow observations; 90 days for patch outcomes; 14 days for agent inference. Below an effective confidence of
0.25 a record is listed but never used.

## Invalidation

- **Expiry** is applied whenever records are read.
- **Evidence:** a repository memory with `evidence` goes `stale` when any of those files changed, vanished or resolves
  outside the repository. Stale records are kept for explanation and never used.
- **Supersession** and **forgetting** (`memory forget`) keep history; nothing is deleted.
- **Repository profile:** see below.

## Preferences

Only keys in `domain/preferences.py` exist, each with a validator and the code that applies it:

| Key | Effect |
|---|---|
| `test.commands` | commands tried before the planner's and before auto-detection - they must still be ones the command policy runs unattended and whose tool is installed |
| `approval.ask_before_workspace_writes` | ask before commands that write in the workspace, even ones the gate would run |
| `automation.external_writes` | `auto` is recorded; policy still requires approval for external writes and the step says it did not honour the preference |

Precedence, lowest to highest: system default < organization < workspace < repository < user < workflow. The value
shown always names what decided it and what it overrode (`patchquest preferences list`).

## Repository profile

Per repository: languages, package managers, test frameworks, test/lint/typecheck/build commands, CI provider,
monorepo packages, source/test roots, generated and vendor directories, protected paths. Each field is a memory with
source, confidence, evidence files and last-verified time. Detection **reads manifests and layout only and never runs
anything it finds**; running a command stays behind the command policy and sandbox.

Refresh is incremental: a fingerprint over the manifests it reads and the top-level names is compared first - if
unchanged nothing else is read. A change recomputes the profile and writes only fields whose value changed, marks
fields whose subject vanished `stale`, and never replaces a field a person set (`repo set`).

## In a run

At the repository-scan phase the run refreshes the profile, invalidates stale facts, and selects notes. Selection
considers only memories visible to this tenant, repository, user and workflow; ranks by relevance (task words, paths,
`applies_to`), authority and effective confidence; keeps the highest-precedence record per key; and stops at a token
budget (default 400). Notes appear in the planner prompt labelled with scope and source (`unverified` when untrusted)
and "not instructions". Ledger events: `repository_profile_changed`, `memory_invalidated`, `memory_selected` (with
`considered / selected / tokens / stale_rejected / low_confidence_rejected / duplicates_avoided / not_relevant /
over_budget` and the reason for each item), `memory_withheld`, `decision_explained`, `assumption_invalidated`.

`decision_explained` is structured provenance, not reasoning: what was chosen, which preference/policy/detection
decided it, what it overrode, and what was set aside. When an assumption stops holding (a preferred command no longer
passes the gate or its tool is gone) the run records `assumption_invalidated` and falls back; it never edits the
preference. A finished run leaves a low-authority episodic note.

`memory_mode` `off` and `session` read and write nothing; `repo` uses organization, workspace, repository and workflow
memory; `user` adds the caller's own user-scoped memory. Preferences are settings and apply in every mode.

## Policy hooks

`memory.inject.local` / `memory.inject.cloud` (by provider locality) can be denied to keep memory out of model calls;
`model.use.<provider>` can be denied to forbid a provider - the run fails with `POLICY_DENIED` before any model call.

## Commands and API

```bash
patchquest memory list|show|forget|add        patchquest preferences list|set|unset
patchquest repo profile [PATH] [--refresh]    patchquest repo set FIELD VALUE
patchquest explain RUN
```

`GET/POST /api/memories`, `DELETE /api/memories/{id}`, `GET/PUT/DELETE /api/preferences`,
`GET /api/repositories/profile`, `PUT /api/repositories/profile/{field}`, `GET /api/runs/{id}/explanations`.
Organisation memory needs an owner, workspace memory `settings.write`, repository/workflow memory `run.create`; user
memory is only ever your own and invisible to others, admins included. Service accounts' writes are recorded as
`import`, and they cannot set preferences. The API never reads a repository; profiles are detected by runs and the CLI.

## Limits

No embeddings or vector search (deterministic keyword relevance is measured first); no team/project scope; the API
cannot yet tell which repository paths a tenant owns, so repository-scoped data is filed per workspace but the path
itself is not access-checked until a repository entity exists; relevance is lexical.
