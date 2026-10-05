# Policy and configuration explain

Policy decides what a run may do. It is evaluated by deterministic code at the point of action; model output
cannot create, edit or bypass a policy.

## Model

- A **policy** is a named, versioned document attached to one scope: `organization`, `workspace`, `repository`,
  `workflow` or `user`. Teams and projects are not modelled yet, so they have no scope.
- An **action** is an id such as `command.run` or `action.github.create_pull_request`; rules match it with a glob
  and may filter by side-effect class.
- Every applicable policy is consulted and the **strictest** answer wins:
  `DENY` > `REQUIRE_APPROVAL` > `ALLOW_WITH_LIMITS` > `ALLOW`. A narrower scope can tighten, never loosen.
  Ties are attributed to the widest scope.
- A built-in **system floor** asks for approval for `REPOSITORY_WRITE`, `EXTERNAL_WRITE`, `HOST_MUTATION`,
  `DESTRUCTIVE` and `UNKNOWN` effects. Stored policies cannot relax it (an `ALLOW` is just an explicit opinion).
- `limits` set **ceilings** on `agent.*` numeric settings (for example `agent.max_model_calls`). The smallest ceiling
  wins. They are applied when a run is created, forked or replayed, and are recorded in the run's overrides, so a
  resume keeps them. A ceiling applies to "unlimited" (0) settings too.
- A stored policy that cannot be parsed is replaced by a deny-all (`POLICY_MALFORMED`); it never silently disappears.
- History is kept: saving creates the next version, disabling only deactivates, and deleting is blocked by a trigger.
  Saves are written to the audit log with the policy digest.

```yaml
name: release-freeze
scope: workspace
rules:
  - {action: "action.github.*", result: DENY, reason: frozen for the release}
  - {action: "command.run", effects: [WORKSPACE_WRITE], result: REQUIRE_APPROVAL, reason: ask before running anything}
limits: {agent.max_model_calls: 20, agent.max_commands: 30}
```

## Where it is enforced

| Point | Action id | Effect of the decision |
|---|---|---|
| a command about to run in a run | `command.run` | `DENY` blocks it (like the command gate's own block); `REQUIRE_APPROVAL` asks even if the command gate would run it |
| a workflow action about to be performed | `action.<name>` | `DENY` fails the step; `REQUIRE_APPROVAL` fails the step unless a human approval sits upstream |
| run creation, fork, replay | `agent.*` ceilings | overrides and defaults are clamped |
| a run starting | `model.use.<provider>` | `DENY` fails the run with `POLICY_DENIED` before any model call |
| memory about to be shown to a model | `memory.inject.local` / `memory.inject.cloud` | `DENY` keeps remembered notes out of that call |
| a web search about to contact a provider | `network.read.domain:<host>` | anything but `ALLOW` refuses the search before the request |
| a workflow action that sends data out | `artifact.disclose:<class>:<destination>` (and, for a read, `network.read.domain:<host>`) | fails the step before the connector or plugin is called |
| a hosted model provider about to receive the task's source | `artifact.disclose:source_code:model_provider` | fails the run with `POLICY_DENIED`; local providers are exempt |
| `patchquest export` | `artifact.disclose:<class>:portable_bundle` | refuses before any file is written |

A policy change applies to the next run (or resume); a running run keeps the chain it loaded.

## Network reads and artifact disclosure

These are ordinary policy actions, matched by the same patterns and combined the same way (strictest wins, narrower scopes can
only tighten), not a second system.

* **Network reads** are `network.read.domain:<host>` with effect `NETWORK_READ` (`*.pypi.org` patterns work). Writing to an
  external system stays `action.<connector.action>` with `EXTERNAL_WRITE`.
* **Disclosure** is `artifact.disclose:<class>:<destination>`. Classes: `metadata`, `logs`, `diff`, `source_code`, `test_output`,
  `model_io`, `trajectory`, `artifact`, `secret`. A destination is a kind (`model_provider`, `connector`, `portable_bundle`,
  `external_api`) or a specific name (`github`, `slack`, a plugin); both are evaluated and the stricter answer applies.
* `secret` is denied by the system floor and no stored policy can change that. An unknown class or destination is refused.
* With no policy, ordinary reads and disclosures are allowed (as for `model.use`); restrict by policy. An allowlist is one policy
  with `ALLOW` rules followed by a catch-all `DENY` (the first matching rule within a policy speaks):

```yaml
name: docs-only
scope: workspace
rules:
  - {action: "network.read.domain:docs.github.com", result: ALLOW}
  - {action: "network.read.domain:*", result: DENY, reason: destination is not permitted by effective policy}
  - {action: "artifact.disclose:source_code:model_provider", result: DENY, reason: source stays on local models}
```

`patchquest policy explain network.read.domain:unknown.example --effect NETWORK_READ` prints the action, decision, reason and source.
These points cannot ask a person, so `REQUIRE_APPROVAL` there is treated as a refusal. Which classes a workflow action discloses is
declared in the action catalog (`workflows/catalog.py`); the declarations are coarse (a comment is `metadata`, a pull request is `diff`).
Not covered: reads performed by commands inside the sandbox (that is the sandbox's network setting), and connector reads other than
`notion.read_page`.

## Commands

```bash
patchquest policy put release-freeze.yaml --ref ws_local   # validate, store as next version, audit
patchquest policy list
patchquest policy explain action.github.comment --effect EXTERNAL_WRITE
patchquest policy disable release-freeze --scope workspace --ref ws_local
patchquest config explain [--key max_model_calls] [--all] [--json]
```

`config explain` shows, per setting, the effective value, the layer it came from (default, file, env, run override,
policy ceiling) and the values it displaced. The CLI talks to the database directly, so whoever can run it owns the
install.

## API

`GET /api/policies`, `PUT /api/policies`, `DELETE /api/policies/{scope}/{name}`, `POST /api/policies/explain`.
Writing needs `settings.write` (owner/admin); organisation-wide policy needs an owner. Foreign workspaces are 404.
The API sets organization, workspace and workflow policy only: a repository path belongs to no tenant, so
repository- and user-scoped policy is CLI-only.
