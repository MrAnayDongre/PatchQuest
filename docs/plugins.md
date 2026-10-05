# Plugins

A plugin adds capabilities (today: workflow actions) without changing PatchQuest. Plugins are installed by an
operator; they cannot be uploaded through the API, and nothing runs until it is enabled on purpose.

## Trust levels

| Level | Where it runs | What PatchQuest enforces | What it does **not** stop |
|---|---|---|---|
| `trusted` | in the PatchQuest process, from an installed package's `patchquest.plugins` entry point | policy gate on every call, timeout, failure containment, quarantine, secret scrubbing of results | **everything else**: it is ordinary Python with the server's privileges. Install only code you would run as PatchQuest. A timed-out call leaves its thread running. |
| `external_process` | a fresh subprocess per call, found in `~/.patchquest/plugins/<name>/` (no code is imported to discover it) | all of the above, plus: own session killed as a group after every call, near-empty environment, working directory = its folder, CPU/memory/open-file/output-size limits (`ulimit`), command confined to its folder or `PATH` | reading files or opening sockets that the user account can reach. This is not a sandbox. |

`permissions` (`fs.read`, `fs.write`, `net.http`, `process.spawn`, `secrets.read`, `repo.read`, `repo.write`) are
**declarations**. Enabling grants exactly the declared set (`--grant` must match; more or less is refused) so an
operator reads and accepts them; they are not enforced by the operating system. `secrets.read` is the one with an
effect: a setting marked `secret` is stored as `env:NAME` (never the value), and the host passes the resolved value in
the request only to a plugin that declared the permission.

## Manifest

```yaml
name: ticket-lookup            # 2-41 chars: a-z 0-9 - _
version: 1.0.0
kind: tool                     # tool | connector | provider | context_source | evaluator | workflow_node
trust: external_process        # or trusted (entry-point plugins)
command: [python3, run.py]     # external_process only
runtime: ">=0.1"               # minimum PatchQuest version
permissions: [net.http, secrets.read]
config:
  token: {type: string, secret: true, required: true}
capabilities:
  find: {side_effect: NETWORK_READ}
  comment: {side_effect: EXTERNAL_WRITE, idempotent: false}
```

Every capability must declare its side effect (use `UNKNOWN` if unsure; policy treats it as dangerous).

An external plugin reads one JSON object from stdin - `{"op": "health"|"invoke", "capability", "args", "config"}` - and
writes one JSON line: `{"ok": true, "result": {...}}` or `{"ok": false, "error": "..."}`.

A trusted plugin is a class with a `manifest` dict and `initialize(config)`, `health()`, `invoke(capability, args)`,
`shutdown()`.

## Lifecycle and containment

`discover` (manifests only; a broken plugin is reported, never raised) -> `enable` (compatibility, exact grant, settings
validated, started, saved) -> `invoke` -> `disable`. After a restart `restore()` re-starts enabled plugins and quarantines
those whose installed version changed. Three consecutive failures quarantine a plugin until someone enables it again.
Every enable, call, denial and failure is an append-only row in `plugin_events` (argument *names* only, never values).
Failures surface as `PLUGIN_FAILURE` or `TOOL_TIMEOUT`.

## Policy

A call is `plugin.<name>.<capability>` with the capability's side effect, so the system floor asks for approval for
writes and workspace/organisation policy can allow, deny or limit it. When a workflow calls a tool plugin the action is
`plugin.<name>.<capability>` in the workflow and `action.plugin.<name>.<capability>` for policy (the workflow's scope
applies).

## Commands

```bash
patchquest plugins list
patchquest plugins enable ticket-lookup --grant net.http --grant secrets.read --set token=env:TICKETS_TOKEN
patchquest plugins health ticket-lookup
patchquest plugins invoke ticket-lookup find --args '{"id": 42}'
patchquest plugins disable ticket-lookup
```

## Status

Implemented and tested: manifests, grants, settings, trusted and external-process hosts, policy gate, quarantine,
restore, and tool plugins as workflow actions. Defined by the manifest but not yet wired to anything: the
`connector`, `provider`, `context_source`, `evaluator` and `workflow_node` kinds. There is no HTTP API for plugins yet.
