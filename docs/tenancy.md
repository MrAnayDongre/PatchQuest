# Tenancy, ownership and authorization

## Ownership

```
organization ── teams ── members (people)
     │             └── role grants: team -> workspace
     └── workspace ── runs, workflows, policies, memory, preferences
              ├── repositories  (a filesystem path belongs to exactly one workspace)
              └── projects      (group repositories; optionally owned by a team)
```

- The **workspace** is the unit of isolation. Every run, workflow, policy, memory and repository belongs to one, and
  anything you cannot read is reported as *not found* (never *forbidden*), so ids cannot be probed across tenants.
- **Repositories.** A named workspace may act only on repositories it has registered. Registering claims a canonical
  path: it is refused when another workspace has the same path, a path inside it, or one around it. Runs (from the
  API, CLI, or a workflow's agent node) on an unregistered or foreign path fail with `repository_not_allowed`. The
  implicit local workspace (`ws_local`, single-user mode) needs no registration. Unregistering releases the path.
- **Projects** group a workspace's repositories. If a project has a team, only that team's members and workspace
  admins/owners can start runs on its repositories; everyone with read access still sees the runs. Work started by the
  system with no person behind it (a trigger with no creator) is governed by who may manage the workflow instead.
- **Teams** belong to an organisation and are managed by its owners. A team can be granted a role in a workspace of its
  organisation by someone who may grant that role there; every member then holds it. A person's effective role in a
  workspace is the strongest of their direct and team roles, ordered owner > admin > developer > operator > viewer >
  service. Roles are not a strict hierarchy (an operator can read audit logs, a developer cannot), so grant the specific
  role you mean. Removing a member or revoking the grant takes effect on the next request.

## Authorization

Every decision is `principal.can(permission, workspace)` over one table in `domain/identity.py`; no route compares a
role name. Permissions: `run.read`, `run.create`, `run.control`, `approval.decide`, `workflow.manage`,
`settings.read`, `settings.write`, `members.manage`, `tokens.manage`, `audit.read`, `repository.manage`, `org.manage`.

| Role | Gets |
|---|---|
| owner | everything, including `org.manage` (organisation-wide policy/memory, teams) |
| admin | everything inside a workspace except `org.manage` |
| developer | read, create runs, control runs, decide approvals, manage workflows |
| operator | read, control runs, decide approvals, read audit |
| viewer | read |
| service | read and create runs (never approvals, never preferences) |

Who may grant roles is bounded by `may_grant`: only owners create owners or admins.

## Where it is enforced

API routes check the permission and the object's workspace; policy, memory, preference, repository, project and team
queries are additionally filtered by organisation/workspace in SQL, so a missing check in a route cannot widen access.
Run creation checks repository ownership in the application service, so the API, CLI and workflows share it. The test
matrix (`tests/security/`) drives forged and foreign ids through every route family and asserts nothing changes.

## Not yet

No SSO/OIDC; plugins are installed per host, not per tenant (no API); connectors' per-tenant credentials arrive with
the connector framework; the team scope for policy/memory/preferences is reserved (its ordering relative to existing
stored scopes needs a data migration) - teams currently carry roles and project ownership only.
