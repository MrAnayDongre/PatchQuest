"""Recorded provider: replays the model responses stored with an earlier run.

Replaying a run re-executes PatchQuest's own (deterministic) orchestration while the model's answers
come from the original run's ``model_calls``, in the order they were given. If orchestration asks for
something the recording does not have next (a different role, or more calls than were recorded), the
replay has *diverged* and stops rather than inventing an answer.

The model name encodes the session: ``replay:<original_run_id>:<replay_run_id>``.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass

from patchquest.agents.provider_base import Capabilities, ModelConfig, ProviderBase, ProviderResponse
from patchquest.agents.providers_scripted import detect_role
from patchquest.database import get_db
from patchquest.domain.failures import FailureKind, PatchQuestError

PREFIX = "replay:"


@dataclass(frozen=True)
class RecordedCall:
    role: str
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


def session_name(original_run_id: str, replay_run_id: str) -> str:
    return f"{PREFIX}{original_run_id}:{replay_run_id}"


def load_recording(run_id: str) -> list[RecordedCall]:
    """The successful model calls of ``run_id`` in the order they happened."""
    with get_db() as conn:
        rows = conn.execute("SELECT role, request_json, response_text, model, prompt_tokens, completion_tokens FROM model_calls "
                            "WHERE run_id = ? AND status = 'ok' AND response_text IS NOT NULL ORDER BY id", (run_id,)).fetchall()
    return [RecordedCall(_role_of(r), r["response_text"], r["model"] or "", r["prompt_tokens"] or 0, r["completion_tokens"] or 0)
            for r in rows]


def _role_of(row: sqlite3.Row) -> str:
    """The role as the replay will recognise it: derived from the recorded prompt, exactly like the asked
    role (the stored ``role`` is the model *profile*, e.g. repair calls use ``coder``)."""
    try:
        system = json.loads(row["request_json"])[0]["content"]
    except (TypeError, ValueError, KeyError, IndexError):
        return str(row["role"])
    derived = detect_role(system)
    return derived if derived != "unknown" else str(row["role"])


class RecordedProvider(ProviderBase):
    sessions: dict[str, deque[RecordedCall]] = {}

    @classmethod
    def end_session(cls, name: str) -> None:
        cls.sessions.pop(name, None)

    def capabilities(self, config: ModelConfig) -> Capabilities:
        return Capabilities()  # the recording already holds whatever shape the model produced

    async def complete(self, messages: list[dict[str, str]], config: ModelConfig,
                       response_format: dict | None = None) -> ProviderResponse:
        name = config.model
        if not name.startswith(PREFIX):
            raise PatchQuestError(FailureKind.INTERNAL_INVARIANT, f"not a replay session: {name!r}")
        if name not in self.sessions:
            original = name.removeprefix(PREFIX).split(":", 1)[0]
            self.sessions[name] = deque(load_recording(original))
        queue = self.sessions[name]
        asked = detect_role(messages[0]["content"] if messages else "")
        if not queue:
            raise PatchQuestError(FailureKind.REPLAY_DIVERGED,
                                  f"the replay asked the '{asked}' role for another answer but the recording has none left")
        nxt = queue[0]
        if asked != "unknown" and nxt.role != asked:
            raise PatchQuestError(FailureKind.REPLAY_DIVERGED,
                                  f"the replay asked the '{asked}' role but the recording's next answer is for '{nxt.role}'")
        queue.popleft()
        return ProviderResponse(content=nxt.content, model=nxt.model,
                                usage={"prompt_tokens": nxt.prompt_tokens, "completion_tokens": nxt.completion_tokens})
