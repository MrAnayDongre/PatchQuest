"""Adapter: a synchronous ``Connector`` as a backend the workflow engine can call.

The engine has already enforced "a human approved this" (validation plus a runtime check) and passes the
approver's identity. Here that becomes the connector's own ``ApprovalGrant`` - minted only when an approver
exists, scoped to this action and idempotency key, and short-lived. Connector calls are blocking HTTP, so
they run in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from patchquest.connectors.base import Action, ApprovalGrant, Connector
from patchquest.connectors.clock import utc_now
from patchquest.domain.failures import FailureKind, PatchQuestError

GRANT_TTL = timedelta(minutes=5)


class ConnectorBackend:
    def __init__(self, connector: Connector, actions: Mapping[str, type[Action]]) -> None:
        self._connector = connector
        self._actions = dict(actions)  # short action name ("comment") -> its typed model

    def _short(self, name: str) -> str:
        short = name.split(".", 1)[1]
        if short not in self._actions:
            raise PatchQuestError(FailureKind.TOOL_FAILURE, f"the {self._connector.spec.name} connector has no action '{short}'")
        return short

    async def perform(self, name: str, params: dict[str, Any], *, idempotency_key: str, approved_by: str | None) -> dict[str, Any]:
        short = self._short(name)
        try:
            action = self._actions[short](**params)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise PatchQuestError(FailureKind.TOOL_FAILURE,
                                  f"invalid parameters for {name}: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from None
        grant = ApprovalGrant(short, idempotency_key, approved_by, utc_now() + GRANT_TTL) if approved_by else None
        result = await asyncio.to_thread(self._connector.perform, action, idempotency_key=idempotency_key, grant=grant)
        return {"id": result.external_id, "url": result.url, "created": result.created}

    async def find_existing(self, name: str, idempotency_key: str) -> dict[str, Any] | None:
        self._short(name)
        found = await asyncio.to_thread(self._connector.find_existing, idempotency_key)
        return None if found is None else {"id": found.external_id, "url": found.url, "created": False}
