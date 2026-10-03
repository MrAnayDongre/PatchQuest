"""The checkpoint codec: what survives a round trip, and what must not."""

import json

from patchquest.orchestrator.phases import Phase, PhaseStatus
from patchquest.orchestrator.snapshot import capture, restore
from patchquest.orchestrator.state_machine import RunStateMachine
from patchquest.tools.secret_guard import SecretFinding


def _machine(run_id="s"):
    return RunStateMachine(run_id, "/repo", "Fix add() in calc.py")


def test_state_survives_a_json_round_trip():
    a = _machine()
    a.ctx.plan = {"plan": "x", "test_commands": ["pytest"]}
    a.ctx.model_calls, a.ctx.tokens_used = 4, 1234
    a.ctx.selected_context = {"calc.py": "def add(a, b): ...\n"}
    a.ctx.secret_findings = [SecretFinding("OpenAI API Key", 3, "a.py", "sk-…", "use env")]
    a.phase_statuses[Phase.INTAKE] = PhaseStatus.COMPLETE
    a.phase_statuses[Phase.RESEARCH] = PhaseStatus.SKIPPED
    a._blocked, a._patch_secret = True, True

    b = _machine()
    assert restore(b, json.loads(json.dumps(capture(a)))) is None  # no workspace yet
    assert (b.ctx.plan, b.ctx.model_calls, b.ctx.tokens_used) == (a.ctx.plan, 4, 1234)
    assert b.ctx.selected_context == a.ctx.selected_context
    assert b.ctx.secret_findings == a.ctx.secret_findings and isinstance(b.ctx.secret_findings[0], SecretFinding)
    assert b.phase_statuses[Phase.INTAKE] is PhaseStatus.COMPLETE and b.phase_statuses[Phase.RESEARCH] is PhaseStatus.SKIPPED
    assert b.phase_statuses[Phase.PLANNING] is PhaseStatus.PENDING
    assert b._blocked and b._patch_secret and not b._no_patch


def test_runtime_only_members_are_not_persisted():
    state = capture(_machine())
    assert "event_sink" not in state["ctx"] and "workspace_path" not in state["ctx"]
    json.dumps(state)  # plain JSON, no objects


def test_unknown_and_missing_keys_do_not_break_restore():
    b = _machine()
    restore(b, {"ctx": {"task": "kept", "field_from_the_future": 1}, "phase_statuses": {}, "flags": {"_bogus": True}})
    assert b.ctx.task == "kept" and not hasattr(b, "_bogus") and b.attempt == 1


def test_workspace_files_round_trip_as_bytes(tmp_path, monkeypatch):
    from patchquest.runtime.workspace import ShadowWorkspace

    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "w")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bin.dat").write_bytes(b"\x00\xff\x10 binary")
    a = RunStateMachine("w", str(repo), "task")
    ws = ShadowWorkspace("w", str(repo))
    ws.create()
    ws.note_touched(["bin.dat", "new.txt"])
    (ws.path / "bin.dat").write_bytes(b"changed")
    (ws.path / "new.txt").write_bytes(b"created")
    a._workspace = ws

    files = restore(_machine("w2"), json.loads(json.dumps(capture(a))))
    assert files == {"bin.dat": {"base": b"\x00\xff\x10 binary", "current": b"changed"},
                     "new.txt": {"base": None, "current": b"created"}}


def test_captured_command_output_is_redacted_but_file_contents_are_not():
    a = _machine()
    key = "sk-" + "a" * 30
    a.ctx.commands_run = [{"command": "env", "result": {"stdout": f"OPENAI_API_KEY={key}\nok"}}]
    a.ctx.test_results = [{"stdout": f"token {key}", "nested": [{"stderr": key}]}]
    a.ctx.selected_context = {"config.py": f'EXAMPLE = "{key}"  # the user\'s own file\n'}
    blob = json.dumps(capture(a))
    assert key not in json.dumps(capture(a)["ctx"]["commands_run"]) and key not in json.dumps(capture(a)["ctx"]["test_results"])
    assert "ok" in blob  # surrounding output is kept
    assert key in capture(a)["ctx"]["selected_context"]["config.py"]
