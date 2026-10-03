"""The actions a workflow may use, with their declared side effects, and the built-in local runner.

The catalogue is what validation checks against (unknown action names and unguarded external writes are
rejected when a workflow is saved). Connector-backed actions are *declared* here so workflows can be designed
and validated before an integration is connected; performing one without a configured connector fails the
step with a clear, typed reason (``CONNECTOR_UNAVAILABLE``) instead of pretending to succeed.
"""

from __future__ import annotations

from typing import Any

from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.workflows import ActionInfo

ACTIONS: dict[str, ActionInfo] = {
    "notify.log": ActionInfo(SideEffect.PURE),  # records a message in the workflow's own history
    "github.comment": ActionInfo(SideEffect.EXTERNAL_WRITE),
    "github.add_label": ActionInfo(SideEffect.EXTERNAL_WRITE),
    "github.create_pull_request": ActionInfo(SideEffect.EXTERNAL_WRITE),
    "slack.post_message": ActionInfo(SideEffect.EXTERNAL_WRITE),
    "webhook.post": ActionInfo(SideEffect.EXTERNAL_WRITE, idempotent=False),
}


class LocalActions:
    """Performs the built-in actions and delegates connector actions to a registered backend, if any."""

    def __init__(self, backends: dict[str, Any] | None = None, plugins: Any = None) -> None:
        self._backends = backends or {}  # connector name ("github") -> object with perform/find_existing
        self._plugins = plugins  # a PluginHost: enabled tool plugins add "plugin.<name>.<capability>" actions

    def _all(self) -> dict[str, ActionInfo]:
        return {**ACTIONS, **(self._plugins.tool_actions() if self._plugins is not None else {})}

    def names(self) -> list[str]:
        return list(self._all())

    def info(self, name: str) -> ActionInfo | None:
        return self._all().get(name)

    def _backend(self, name: str) -> Any:
        connector = name.split(".", 1)[0]
        backend = self._backends.get(connector)
        if backend is None:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, f"no {connector} connector is connected for '{name}'")
        return backend

    async def perform(self, name: str, params: dict[str, Any], *, idempotency_key: str, approved_by: str | None) -> dict[str, Any]:
        if name == "notify.log":
            return {"logged": str(params.get("message", ""))[:2000]}
        if name.startswith("plugin.") and self._plugins is not None:
            _, plugin, capability = name.split(".", 2)
            # The engine has already applied policy to action.plugin.* (with the workflow's scope) and required a
            # human approver where the side effect demands one; the host re-checks the system floor.
            return await self._plugins.invoke(plugin, capability, params, approved=approved_by is not None)
        return await self._backend(name).perform(name, params, idempotency_key=idempotency_key, approved_by=approved_by)

    async def find_existing(self, name: str, idempotency_key: str) -> dict[str, Any] | None:
        if name == "notify.log" or name.startswith("plugin."):
            return None  # a plugin call has no external record to reconcile: non-idempotent ones become 'uncertain'
        backend = self._backends.get(name.split(".", 1)[0])
        return None if backend is None else await backend.find_existing(name, idempotency_key)
