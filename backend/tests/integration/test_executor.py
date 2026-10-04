"""Executor and policy-enforcing runner with real subprocesses.

Invariants: blocked commands never run and unapproved risky ones never run; automatic commands get no shell expansion;
credentials in the parent environment never reach children; a timeout kills the whole process tree; output is capped
without blocking the command; same-second edits never run stale bytecode.
"""

import os
import sys
import time

import pytest

from patchquest.execution.executor import run_argv, scrubbed_env
from patchquest.tools.command_runner import run_command_safe


class TestRunnerEnforcesPolicy:
    def test_blocked_never_runs(self, tmp_path):
        marker = tmp_path / "ran"
        res = run_command_safe(f"touch {marker}; sudo rm -rf /", str(tmp_path))
        assert res.get("blocked") and not marker.exists()

    def test_unapproved_risky_never_runs(self, tmp_path):
        marker = tmp_path / "ran"
        res = run_command_safe(f"touch {marker}", str(tmp_path))
        assert res.get("needs_approval") and not marker.exists()

    def test_approved_composite_runs_via_shell(self, tmp_path):
        res = run_command_safe("echo a && echo b", str(tmp_path), approved=True)
        assert res["success"] and res["stdout"].split() == ["a", "b"]

    def test_automatic_commands_get_no_shell_expansion(self, tmp_path):
        res = run_command_safe("echo $HOME", str(tmp_path))
        assert res["success"] and res["stdout"].strip() == "$HOME"

    def test_secrets_in_output_are_redacted(self, tmp_path):
        (tmp_path / "k.txt").write_text("sk-notarealkeynotarealkeynotarealkey\n")
        res = run_command_safe("cat k.txt", str(tmp_path))
        assert "sk-abc123" not in res["stdout"]


class TestExecutor:
    def test_env_is_scrubbed_of_credentials(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
        monkeypatch.setenv("LANG", "C.UTF-8")
        env = scrubbed_env()
        assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
        assert env["LANG"] == "C.UTF-8" and "PATH" in env

    def test_child_cannot_see_parent_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret-value-123456789")
        res = run_argv([sys.executable, "-c", "import os;print(os.environ.get('OPENAI_API_KEY'))"], str(tmp_path))
        assert res["stdout"].strip() == "None"

    def test_timeout_kills_the_whole_process_tree(self, tmp_path):
        pidfile = tmp_path / "child.pid"
        code = (
            "import subprocess,sys,time;"
            f"p=subprocess.Popen(['sleep','60']);open({str(pidfile)!r},'w').write(str(p.pid));time.sleep(60)"
        )
        start = time.monotonic()
        res = run_argv([sys.executable, "-c", code], str(tmp_path), timeout=1)
        assert res["timed_out"] and not res["success"] and time.monotonic() - start < 10
        child = int(pidfile.read_text())
        time.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)

    def test_output_is_capped_but_command_still_completes(self, tmp_path):
        res = run_argv([sys.executable, "-c", "print('x'*5_000_000)"], str(tmp_path), max_output=1000)
        assert res["success"] and len(res["stdout"]) <= 1000 and res["truncated"]

    def test_missing_executable_is_reported_not_raised(self, tmp_path):
        res = run_argv(["definitely-not-a-binary"], str(tmp_path))
        assert not res["success"] and "error" in res["stderr"].lower()

    def test_nonzero_exit_reported(self, tmp_path):
        res = run_argv([sys.executable, "-c", "import sys;sys.exit(3)"], str(tmp_path))
        assert res["returncode"] == 3 and not res["success"]


def test_executor_disables_bytecode_so_same_second_edits_are_never_stale(tmp_path):
    """Regression: an edit with the same size within one second ran stale .pyc and failed validation."""
    mod = tmp_path / "m.py"
    mod.write_text("def f():\n    return 1\n")
    code = "import m; print(m.f())"
    first = run_argv([sys.executable, "-c", code], str(tmp_path), env=scrubbed_env())
    mod.write_text("def f():\n    return 2\n")  # same length, same second
    second = run_argv([sys.executable, "-c", code], str(tmp_path), env=scrubbed_env())
    assert first["stdout"].strip() == "1" and second["stdout"].strip() == "2"
    assert not (tmp_path / "__pycache__").exists()
