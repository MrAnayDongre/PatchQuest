"""Serialise a run's resumable state to plain JSON and back.

Only *state* is captured, never behaviour: the run context's data fields, which phases are done, the
handful of machine flags that steer later phases, and the shadow workspace's touched files (original
and current bytes) so the workspace can be rebuilt from the real repository after a crash.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, fields, is_dataclass
from typing import TYPE_CHECKING, Any

from patchquest.orchestrator.phases import Phase, PhaseStatus
from patchquest.orchestrator.run_context import RunContext
from patchquest.tools.secret_guard import SecretFinding, redact_secrets

if TYPE_CHECKING:
    from patchquest.orchestrator.state_machine import RunStateMachine

# Runtime-only members of RunContext: re-created on resume, never persisted.
_EPHEMERAL = {"event_sink", "workspace_path"}
# Fields holding captured process output. Anything a command printed may include a credential, and a
# checkpoint outlives the run, so it is redacted before it is written (file contents are left intact).
_OUTPUT_FIELDS = ("commands_run", "test_results", "baseline_results")
_FLAGS = ("_blocked", "_patch_secret", "_no_patch", "_promotion_reconciled")


def _redacted(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {k: _redacted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redacted(v) for v in value]
    return value


def _b64(raw: bytes | None) -> str | None:
    return None if raw is None else base64.b64encode(raw).decode("ascii")


def _unb64(text: str | None) -> bytes | None:
    return None if text is None else base64.b64decode(text)


def capture(machine: RunStateMachine) -> dict[str, Any]:
    ctx = {f.name: getattr(machine.ctx, f.name) for f in fields(RunContext) if f.name not in _EPHEMERAL}
    for name in _OUTPUT_FIELDS:
        ctx[name] = _redacted(ctx[name])
    ctx["secret_findings"] = [asdict(x) if is_dataclass(x) and not isinstance(x, type) else str(x)
                              for x in machine.ctx.secret_findings]
    state: dict[str, Any] = {
        "ctx": ctx,
        "phase_statuses": {p.value: s.value for p, s in machine.phase_statuses.items()},
        "flags": {name: getattr(machine, name) for name in _FLAGS},
        "workspace": None,
    }
    ws = machine._workspace
    if ws is not None:
        current = ws.checkpoint()
        state["workspace"] = {rel: {"base": _b64(ws.base[rel]), "current": _b64(current[rel])} for rel in ws.touched}
    return state


def restore(machine: RunStateMachine, state: dict[str, Any]) -> dict[str, dict[str, bytes | None]] | None:
    """Load ``state`` into ``machine``. Returns the workspace files to re-materialise (or None).

    Unknown keys are ignored and missing ones keep their defaults, so checkpoints survive field changes.
    """
    known = {f.name for f in fields(RunContext)} - _EPHEMERAL
    for name, value in (state.get("ctx") or {}).items():
        if name in known:
            setattr(machine.ctx, name, value)
    machine.ctx.secret_findings = [
        SecretFinding(**x) if isinstance(x, dict) and set(x) <= {f.name for f in fields(SecretFinding)} else x
        for x in machine.ctx.secret_findings]
    for name, value in (state.get("phase_statuses") or {}).items():
        machine.phase_statuses[Phase(name)] = PhaseStatus(value)
    for name, value in (state.get("flags") or {}).items():
        if name in _FLAGS:
            setattr(machine, name, value)
    touched = state.get("workspace")
    if touched is None:
        return None
    return {rel: {"base": _unb64(v["base"]), "current": _unb64(v["current"])} for rel, v in touched.items()}
