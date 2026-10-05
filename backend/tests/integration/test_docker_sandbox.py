"""Real-container sandbox: no network, no host credentials/environment, read-only root, dropped capabilities,
bounded processes, no orphaned containers, and a malicious repository blocked on every escape attempt.
"""

import os
import subprocess
import textwrap
import uuid

import pytest

from patchquest.config import AppConfig, set_config
from patchquest.runtime.docker_runtime import DockerRuntime, check_docker_available, check_image_available

pytestmark = [pytest.mark.docker, pytest.mark.skipif(
    not (check_docker_available() and check_image_available()),
    reason="needs a reachable Docker daemon and the patchquest-sandbox:latest image",
)]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "sandboxes")
    cfg = AppConfig()
    cfg.safety.max_command_timeout = 30
    set_config(cfg)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.txt").write_text("hello from the host repo\n")
    rt = DockerRuntime(run_id=f"it-{uuid.uuid4().hex[:8]}", repo_path=str(repo))
    assert rt.prepare_sandbox()["success"]
    yield rt, repo
    rt.cleanup()
    set_config(AppConfig())


def run(rt, command, timeout=30):
    return rt.run_command(command, str(rt._get_sandbox_workspace()), timeout=timeout)


def test_command_sees_the_workspace_and_can_write_to_it(sandbox):
    rt, _ = sandbox
    res = run(rt, "cat hello.txt && echo made > made.txt")
    assert res["success"] and "hello from the host repo" in res["stdout"]
    made = rt._get_sandbox_workspace() / "made.txt"
    assert made.read_text().strip() == "made" and made.stat().st_uid == os.getuid()  # --user mapping


def test_the_real_repository_is_never_modified_by_container_commands(sandbox):
    rt, repo = sandbox
    run(rt, "echo tampered > hello.txt && echo new > extra.txt")
    assert (repo / "hello.txt").read_text() == "hello from the host repo\n" and not (repo / "extra.txt").exists()


def test_no_network(sandbox):
    rt, _ = sandbox
    res = run(rt, "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"")
    assert not res["success"]


def test_host_environment_and_home_are_invisible(sandbox, monkeypatch):
    rt, _ = sandbox
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak-1234567890abcdef")
    res = run(rt, "env; ls -la /home /root 2>&1; ls " + os.path.expanduser("~") + " 2>&1")
    assert "sk-must-not-leak" not in res["stdout"]
    assert os.path.expanduser("~/.ssh") not in res["stdout"] and "id_rsa" not in res["stdout"]


def test_root_filesystem_is_read_only_but_tmp_is_writable(sandbox):
    rt, _ = sandbox
    assert not run(rt, "touch /etc/pwned")["success"]
    assert not run(rt, "touch /usr/bin/pwned")["success"]
    assert run(rt, "touch /tmp/ok")["success"]
    assert not run(rt, "cp /bin/true /tmp/x && /tmp/x")["success"]  # tmpfs is noexec


def test_capabilities_are_dropped(sandbox):
    rt, _ = sandbox
    res = run(rt, "grep CapEff /proc/self/status")
    assert res["success"] and res["stdout"].split()[-1].strip("0") == ""


def test_timeout_removes_the_container(sandbox):
    rt, _ = sandbox
    res = run(rt, "sleep 60", timeout=3)
    assert res["timed_out"] and not res["success"]
    left = subprocess.run(["docker", "ps", "-q", "--filter", f"name=pq-{rt.run_id[:12]}"],
                          capture_output=True, text=True).stdout.strip()
    assert left == ""  # previously the container kept running after the CLI was killed


def test_fork_bomb_is_contained_by_the_pids_limit(sandbox):
    import time

    rt, _ = sandbox
    cfg = AppConfig()
    cfg.runtime.docker.pids_limit = 32
    set_config(cfg)
    started = time.monotonic()
    res = run(rt, "i=0; while [ $i -lt 400 ]; do sleep 20 & i=$((i+1)); done; wait", timeout=12)
    assert time.monotonic() - started < 40  # the host was never hung by the attempt
    assert isinstance(res["returncode"], int)  # it returned a result instead of hanging the runtime
    left = subprocess.run(["docker", "ps", "-q", "--filter", f"name=pq-{rt.run_id[:12]}"],
                          capture_output=True, text=True).stdout.strip()
    assert left == ""  # and nothing is left running afterwards


def test_symlinks_in_the_repo_are_not_carried_into_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "sandboxes")
    set_config(AppConfig())
    secret = tmp_path / "host-secret.txt"
    secret.write_text("TOP SECRET")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "innocent.txt").symlink_to(secret)
    (repo / "dir").symlink_to(tmp_path)
    (repo / "real.txt").write_text("real")
    rt = DockerRuntime(run_id=f"it-{uuid.uuid4().hex[:8]}", repo_path=str(repo))
    rt.prepare_sandbox()
    listing = run(rt, "ls -A")["stdout"].split()
    assert listing == ["real.txt"]  # neither symlink was carried over
    read = run(rt, "cat innocent.txt")
    assert not read["success"] and "TOP SECRET" not in read["stdout"] + read["stderr"]
    rt.cleanup()


def test_malicious_repository_cannot_escape_through_its_own_tests(sandbox):
    """A repo whose test suite actively tries to steal credentials, phone home and persist."""
    rt, _ = sandbox
    ws = rt._get_sandbox_workspace()
    (ws / "tests").mkdir()
    (ws / "tests" / "test_evil.py").write_text(textwrap.dedent(f'''
        import os, socket, json, pathlib

        def attempt(fn):
            try:
                fn()
                return "SUCCEEDED"
            except Exception as exc:
                return "blocked: " + type(exc).__name__

        def test_evil():
            results = {{
                "read_ssh": attempt(lambda: open({os.path.expanduser("~/.ssh/id_rsa")!r}).read()),
                "read_etc_shadow": attempt(lambda: open("/etc/shadow").read()),
                "env_key": os.environ.get("OPENAI_API_KEY", "absent"),
                "network": attempt(lambda: socket.create_connection(("1.1.1.1", 53), 3)),
                "write_etc": attempt(lambda: open("/etc/evil", "w").write("x")),
                "write_usr": attempt(lambda: open("/usr/local/bin/evil", "w").write("x")),
                "write_host_home": attempt(lambda: open({os.path.expanduser("~/pwned")!r}, "w").write("x")),
            }}
            pathlib.Path("results.json").write_text(json.dumps(results))
    '''))
    res = run(rt, "python -m pytest -q -p no:cacheprovider tests")
    assert res["success"], res["stdout"] + res["stderr"]
    import json
    results = json.loads((ws / "results.json").read_text())
    assert results.pop("env_key") == "absent"
    assert all(v.startswith("blocked") for v in results.values()), results
    assert not os.path.exists(os.path.expanduser("~/pwned"))


@pytest.mark.asyncio
async def test_full_pipeline_validates_inside_a_real_container(tmp_path, monkeypatch):
    """Patch applied in the shadow workspace, tests executed *in the container*, then promoted."""
    from patchquest.agents.providers_scripted import ScriptedProvider
    from patchquest.database import get_db, init_db, now_iso, set_db_path
    from patchquest.orchestrator.state_machine import RunStateMachine

    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "sandboxes")
    set_db_path(tmp_path / "it.db")
    init_db()
    set_config(AppConfig())
    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests" / "test_calc.py").write_text(
        "import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "from calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
    cmd = "python -m unittest discover -s tests -q"
    name = f"docker-pipeline-{uuid.uuid4().hex[:6]}"
    ScriptedProvider.register(name, {
        "planner": [{"plan": "p", "files_to_inspect": ["calc.py"], "expected_patch_scope": "1 file", "test_commands": [cmd]}],
        "coder": [{"edits": [{"path": "calc.py", "search": "return a - b", "replace": "return a + b"}]}]})
    rid = str(uuid.uuid4())
    now = now_iso()
    with get_db() as conn:
        conn.execute("INSERT INTO runs (id, repo_path, task, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (rid, str(repo), "Fix add()", "created", now, now))
    sm = RunStateMachine(rid, str(repo), "Fix add() in calc.py", provider="scripted", model=name, runtime_mode="docker")
    await sm.execute()
    with get_db() as conn:
        run_row = conn.execute("SELECT outcome, verdict FROM runs WHERE id = ?", (rid,)).fetchone()
    assert (run_row["outcome"], run_row["verdict"]) == ("applied", "passed")
    assert (repo / "calc.py").read_text().endswith("a + b\n")
    assert subprocess.run(["docker", "ps", "-q", "--filter", f"name=pq-{rid[:12]}"],
                          capture_output=True, text=True).stdout.strip() == ""
