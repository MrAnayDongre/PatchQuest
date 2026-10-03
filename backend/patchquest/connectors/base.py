"""The connector contract: triggers (inbound events) are kept apart from actions (outbound effects).

Why a template ``perform``: the approval check and the crash-reconciliation step live in the base class,
so an individual connector cannot forget them or authorise itself. Actions are pydantic objects, never
free-form strings, so what was approved is exactly what runs.

The grant is a capability *value*: only the approval engine is meant to construct it. Python cannot
make that unforgeable in-process; the guard here stops accidents and confused deputies, not a
malicious connector author (connectors are trusted code, plugins are not).
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from patchquest.connectors.clock import Clock, utc_now
from patchquest.connectors.envelope import EventEnvelope, SignatureStatus
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError

SAFE_EFFECTS = frozenset({SideEffect.PURE, SideEffect.READ_ONLY, SideEffect.NETWORK_READ})
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")  # also embedded in markers: no whitespace, no `-->`
_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class MalformedEvent(ValueError):
    """The delivery is not a well-formed event of this source."""


class UnsupportedEvent(ValueError):
    """Well-formed, but not a trigger this connector offers (or not for its bound resource)."""


class ApprovalRequired(PatchQuestError):
    """No valid grant for a side-effecting action. Never retried: only a person can fix it."""

    def __init__(self, detail: str) -> None:
        super().__init__(FailureKind.COMMAND_DENIED, detail)


class MissingCredential(PatchQuestError):
    def __init__(self, ref: SecretRef) -> None:
        super().__init__(FailureKind.ENVIRONMENT_FAILURE, f"credential {ref.name!r} is not set")


@dataclass(frozen=True)
class SecretRef:
    """A *reference* to a secret (an environment variable name), resolved at call time.

    The value is never stored on any object, so it cannot leak through a repr, a dump or an exception.
    """

    name: str

    def resolve(self, environ: Mapping[str, str] | None = None) -> str:
        value = (os.environ if environ is None else environ).get(self.name, "")
        if not value:
            raise MissingCredential(self)
        return value


class Action(BaseModel):
    """Base for typed, validated, immutable actions. Subclasses set ``name``."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ClassVar[str]


@dataclass(frozen=True)
class ActionSpec:
    name: str
    side_effect: SideEffect
    requires_approval: bool = True  # every write and UNKNOWN must keep this True

    def __post_init__(self) -> None:
        if not self.requires_approval and self.side_effect not in SAFE_EFFECTS:
            raise ValueError(f"action {self.name!r} ({self.side_effect}) cannot waive approval")


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    version: str
    triggers: tuple[str, ...]
    actions: tuple[ActionSpec, ...]
    required_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ValueError(f"invalid connector name {self.name!r}")
        names = [a.name for a in self.actions]
        if len(set(names)) != len(names):
            raise ValueError(f"connector {self.name!r} declares an action twice")

    def action(self, name: str) -> ActionSpec:
        for spec in self.actions:
            if spec.name == name:
                return spec
        raise ValueError(f"connector {self.name!r} has no action {name!r}")


@dataclass(frozen=True)
class ApprovalGrant:
    action: str
    idempotency_key: str
    decided_by: str
    expires_at: datetime


@dataclass(frozen=True)
class ActionResult:
    external_id: str
    url: str | None = None
    created: bool = True  # False: reconciliation found the object from an earlier attempt


def check_grant(spec: ActionSpec, key: str, grant: ApprovalGrant | None, now: datetime) -> None:
    if grant is None:
        raise ApprovalRequired(f"{spec.name} needs an approval grant")
    if grant.action != spec.name or grant.idempotency_key != key:
        raise ApprovalRequired(f"grant is not for {spec.name} with this idempotency key")
    if not grant.decided_by:
        raise ApprovalRequired("grant has no approver")
    if grant.expires_at.tzinfo is None or grant.expires_at <= now:
        raise ApprovalRequired("grant is expired or has no timezone")


class Connector(ABC):
    spec: ConnectorSpec

    def __init__(self, clock: Clock = utc_now) -> None:
        self._clock = clock

    @abstractmethod
    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        """Raise ``MalformedEvent`` / ``UnsupportedEvent``; never return a half-valid envelope."""

    @abstractmethod
    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus: ...

    @abstractmethod
    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        """Reconciliation after a crash: has an earlier attempt already created the external object?"""

    @abstractmethod
    def _execute(self, action: Action, idempotency_key: str) -> ActionResult: ...

    def perform(self, action: Action, *, idempotency_key: str, grant: ApprovalGrant | None) -> ActionResult:
        spec = self.spec.action(action.name)
        if not _KEY.match(idempotency_key):
            raise ValueError("idempotency_key must be 1-128 characters of [A-Za-z0-9._:-]")
        if spec.requires_approval:
            check_grant(spec, idempotency_key, grant, self._clock())
        if spec.side_effect not in SAFE_EFFECTS:
            existing = self.find_existing(idempotency_key)
            if existing is not None:
                return ActionResult(existing.external_id, existing.url, created=False)
        return self._execute(action, idempotency_key)


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        name = connector.spec.name
        if name in self._connectors:
            raise ValueError(f"connector {name!r} is already registered")
        self._connectors[name] = connector

    def get(self, name: str) -> Connector:
        try:
            return self._connectors[name]
        except KeyError:
            raise KeyError(f"no connector named {name!r}") from None

    def spec(self, name: str) -> ConnectorSpec:
        return self.get(name).spec

    def names(self) -> list[str]:
        return sorted(self._connectors)
