"""The one shape every inbound event is normalised into before the rest of PatchQuest sees it."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

MAX_PAYLOAD_BYTES = 256 * 1024


class SignatureStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"  # no signature was presented
    INVALID = "INVALID"  # presented and wrong, stale or malformed
    NOT_APPLICABLE = "NOT_APPLICABLE"  # the transport carries no signature (e.g. polled data)


class EventEnvelope(BaseModel):
    """Payload text is untrusted third-party content (it may contain prompt injection): treat as data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    type: str = Field(min_length=1, max_length=128)
    external_id: str = Field(min_length=1, max_length=256)
    timestamp: AwareDatetime
    workspace_id: str = Field(min_length=1, max_length=128)
    actor: str | None = Field(default=None, max_length=256)
    payload: dict[str, Any]
    signature_status: SignatureStatus

    @field_validator("payload")
    @classmethod
    def _payload_fits(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            size = len(json.dumps(value, separators=(",", ":")).encode())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload is not JSON-serialisable: {exc}") from exc
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload is {size} bytes; the limit is {MAX_PAYLOAD_BYTES}")
        return value

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, text: str) -> EventEnvelope:
        return cls.model_validate_json(text)
