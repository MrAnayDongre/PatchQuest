"""Who may do what: roles, permissions and the single authorisation function.

Every access decision in PatchQuest goes through ``Principal.can`` / ``authorize``; no route or service
checks a role by name. Roles are granted per workspace, and a resource belongs to exactly one workspace.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

LOCAL_ORG_ID = "org_local"
LOCAL_WORKSPACE_ID = "ws_local"


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    DEVELOPER = "DEVELOPER"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"
    SERVICE = "SERVICE"


class Permission(StrEnum):
    RUN_READ = "run.read"
    RUN_CREATE = "run.create"
    RUN_CONTROL = "run.control"  # cancel, resume, fork, replay
    APPROVAL_DECIDE = "approval.decide"
    SETTINGS_READ = "settings.read"
    SETTINGS_WRITE = "settings.write"
    MEMBERS_MANAGE = "members.manage"
    TOKENS_MANAGE = "tokens.manage"
    AUDIT_READ = "audit.read"


_ALL = frozenset(Permission)
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: _ALL,
    Role.ADMIN: _ALL,  # differs from OWNER only in who may grant OWNER (see ``may_grant``)
    Role.DEVELOPER: frozenset({Permission.RUN_READ, Permission.RUN_CREATE, Permission.RUN_CONTROL,
                               Permission.APPROVAL_DECIDE, Permission.SETTINGS_READ}),
    Role.OPERATOR: frozenset({Permission.RUN_READ, Permission.RUN_CONTROL, Permission.APPROVAL_DECIDE,
                              Permission.SETTINGS_READ, Permission.AUDIT_READ}),
    Role.VIEWER: frozenset({Permission.RUN_READ, Permission.SETTINGS_READ}),
    # Automation (CI, webhooks): may start and watch runs, but approvals stay with people.
    Role.SERVICE: frozenset({Permission.RUN_READ, Permission.RUN_CREATE}),
}


class Forbidden(PermissionError):
    def __init__(self, permission: Permission, workspace_id: str | None) -> None:
        super().__init__(f"{permission.value} is not permitted" + (f" in workspace {workspace_id}" if workspace_id else ""))
        self.permission, self.workspace_id = permission, workspace_id


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str  # 'user' | 'service' | 'local'
    name: str
    org_id: str
    roles: Mapping[str, Role] = field(default_factory=dict)  # workspace id -> role

    @property
    def actor(self) -> str:
        """How this principal appears in event and audit records."""
        return f"{self.kind}:{self.id}"

    def role_in(self, workspace_id: str) -> Role | None:
        return self.roles.get(workspace_id)

    def can(self, permission: Permission, workspace_id: str) -> bool:
        role = self.roles.get(workspace_id)
        return role is not None and permission in ROLE_PERMISSIONS[role]

    def workspaces_with(self, permission: Permission) -> list[str]:
        return sorted(ws for ws, role in self.roles.items() if permission in ROLE_PERMISSIONS[role])


def authorize(principal: Principal, permission: Permission, workspace_id: str) -> None:
    if not principal.can(permission, workspace_id):
        raise Forbidden(permission, workspace_id)


def may_grant(granter: Role, target: Role) -> bool:
    """Only an OWNER can create another OWNER or an ADMIN; an ADMIN manages everyone below."""
    if target in (Role.OWNER, Role.ADMIN):
        return granter is Role.OWNER
    return granter in (Role.OWNER, Role.ADMIN)
