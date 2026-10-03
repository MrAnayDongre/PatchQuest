"""Who owns what: organisations contain workspaces; workspaces own repositories and projects; teams group people.

* A **workspace** is the unit of isolation: runs, workflows, policies, memory and repositories belong to exactly one.
* A **repository** is a codebase PatchQuest may act on. A filesystem path belongs to exactly one workspace at a time
  (and nested or enclosing paths cannot belong to a different one), so one tenant cannot point runs at another's code.
  The implicit local workspace is the exception: single-user mode needs no registration.
* A **project** groups a workspace's repositories and may be owned by a **team**; creating runs on a team-owned
  project's repositories is limited to that team's members and workspace admins. Reading stays workspace-wide.
* A **team** is a set of people in one organisation. Granting a team a role in a workspace gives every member that
  role there (the strongest of their direct and team roles applies).

Everything else - what a role may do - is in ``domain.identity``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from patchquest.domain.identity import Role

IMPLICIT_WORKSPACE = "ws_local"
# Strongest first. Roles are not a strict hierarchy (an operator can read audit logs, a developer cannot), so when
# a person holds several, the first in this list wins; grant the specific role you mean.
ROLE_STRENGTH = (Role.OWNER, Role.ADMIN, Role.DEVELOPER, Role.OPERATOR, Role.VIEWER, Role.SERVICE)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


class TenancyError(ValueError):
    """A request about teams, projects or repositories was refused. The message is safe to show."""


class RepositoryNotRegistered(PermissionError):
    def __init__(self, path: str, workspace_id: str) -> None:
        super().__init__(f"{path} is not a registered repository of workspace {workspace_id}; an admin can register it")
        self.path, self.workspace_id = path, workspace_id


class RepositoryClaimed(TenancyError):
    """The path (or a path inside or around it) already belongs to another workspace."""


class ProjectAccessDenied(PermissionError):
    def __init__(self, project: str) -> None:
        super().__init__(f"project '{project}' belongs to a team you are not in")
        self.project = project


def clean_name(raw: str, what: str) -> str:
    name = (raw or "").strip()
    if not _NAME.match(name):
        raise TenancyError(f"{what} name must be 1-64 characters: letters, digits, space, '.', '_' or '-'")
    return name


def strongest(roles: Iterable[Role]) -> Role | None:
    found = set(roles)
    return next((r for r in ROLE_STRENGTH if r in found), None)
