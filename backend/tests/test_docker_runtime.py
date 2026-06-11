"""Tests for Docker runtime — does not require Docker daemon for most tests."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from patchquest.runtime.docker_runtime import (
    COPY_EXCLUDE_DIRS,
    DockerRuntime,
    _copy_repo_to_sandbox,
    _should_exclude_file,
    build_docker_command,
    check_docker_available,
)
from patchquest.runtime.runtime_registry import get_runtime, get_runtime_status


class TestDockerCommandConstruction:
    def test_network_disabled_by_default(self):
        cmd = build_docker_command("python --version", "/tmp/ws")
        assert "--network=none" in cmd

    def test_network_enabled_when_configured(self):
        cmd = build_docker_command("python --version", "/tmp/ws", network=True)
        assert "--network=none" not in cmd

    def test_mounts_only_workspace(self):
        cmd = build_docker_command("ls", "/tmp/workspace")
        volume_args = [a for a in cmd if a.startswith("/tmp/workspace")]
        assert any("/tmp/workspace:/workspace" in a for a in cmd)
        # No home directory
        home = os.path.expanduser("~")
        assert all(home not in a for a in cmd)

    def test_no_home_mount(self):
        cmd = build_docker_command("ls", "/tmp/ws")
        home = os.path.expanduser("~")
        for arg in cmd:
            if arg.startswith("-v") or ":" in arg:
                assert home not in arg or "/tmp/ws" in arg

    def test_no_ssh_mount(self):
        cmd = build_docker_command("ls", "/tmp/ws")
        assert all(".ssh" not in a for a in cmd)

    def test_no_cloud_credential_mount(self):
        cmd = build_docker_command("ls", "/tmp/ws")
        assert all(".aws" not in a for a in cmd)
        assert all(".gcp" not in a for a in cmd)

    def test_memory_limit_set(self):
        cmd = build_docker_command("ls", "/tmp/ws", memory="1g")
        idx = cmd.index("--memory")
        assert cmd[idx + 1] == "1g"

    def test_cpu_limit_set(self):
        cmd = build_docker_command("ls", "/tmp/ws", cpus="1")
        idx = cmd.index("--cpus")
        assert cmd[idx + 1] == "1"

    def test_pids_limit_set(self):
        cmd = build_docker_command("ls", "/tmp/ws", pids_limit="256")
        idx = cmd.index("--pids-limit")
        assert cmd[idx + 1] == "256"

    def test_uses_rm_flag(self):
        cmd = build_docker_command("ls", "/tmp/ws")
        assert "--rm" in cmd

    def test_workdir_is_workspace(self):
        cmd = build_docker_command("ls", "/tmp/ws")
        idx = cmd.index("-w")
        assert cmd[idx + 1] == "/workspace"


class TestSandboxCopyExclusions:
    def test_excludes_node_modules(self):
        repo = tempfile.mkdtemp()
        nm = Path(repo) / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")
        (Path(repo) / "src").mkdir()
        (Path(repo) / "src" / "app.py").write_text("x = 1")

        dest = Path(tempfile.mkdtemp()) / "sandbox"
        _copy_repo_to_sandbox(Path(repo), dest)

        assert (dest / "src" / "app.py").exists()
        assert not (dest / "node_modules").exists()

    def test_excludes_env_files(self):
        repo = tempfile.mkdtemp()
        (Path(repo) / ".env").write_text("SECRET=bad")
        (Path(repo) / ".env.local").write_text("SECRET=bad")
        (Path(repo) / "app.py").write_text("x = 1")

        dest = Path(tempfile.mkdtemp()) / "sandbox"
        _copy_repo_to_sandbox(Path(repo), dest)

        assert (dest / "app.py").exists()
        assert not (dest / ".env").exists()
        assert not (dest / ".env.local").exists()

    def test_excludes_private_keys(self):
        repo = tempfile.mkdtemp()
        (Path(repo) / "server.key").write_text("private key")
        (Path(repo) / "cert.pem").write_text("cert")
        (Path(repo) / "main.py").write_text("x = 1")

        dest = Path(tempfile.mkdtemp()) / "sandbox"
        _copy_repo_to_sandbox(Path(repo), dest)

        assert (dest / "main.py").exists()
        assert not (dest / "server.key").exists()
        assert not (dest / "cert.pem").exists()

    def test_excludes_git_dir(self):
        repo = tempfile.mkdtemp()
        (Path(repo) / ".git").mkdir()
        (Path(repo) / ".git" / "HEAD").write_text("ref")
        (Path(repo) / "code.py").write_text("x = 1")

        dest = Path(tempfile.mkdtemp()) / "sandbox"
        _copy_repo_to_sandbox(Path(repo), dest)

        assert (dest / "code.py").exists()
        assert not (dest / ".git").exists()


class TestDockerAvailability:
    @patch("patchquest.runtime.docker_runtime.check_docker_available", return_value=False)
    def test_unavailable_returns_graceful_status(self, mock):
        rt = DockerRuntime(run_id="test", repo_path="/tmp")
        assert not rt.is_available()
        result = rt.run_command("ls", "/tmp")
        assert result["success"] is False
        assert "not available" in result["stderr"]

    def test_runtime_status_schema(self):
        status = get_runtime_status()
        assert "local" in status
        assert "docker" in status
        assert status["local"]["available"] is True
        assert "available" in status["docker"]
        assert "version" in status["docker"]
        assert "image" in status["docker"]

    def test_cleanup_never_targets_original(self):
        repo = tempfile.mkdtemp()
        rt = DockerRuntime(run_id="safe-test", repo_path=repo)
        # Cleanup with no sandbox created should be safe
        assert rt.cleanup() is True
        # Original repo still exists
        assert Path(repo).exists()


class TestRuntimeRegistry:
    def test_local_runtime_returned(self):
        rt = get_runtime("local")
        assert rt.__class__.__name__ == "LocalRuntime"

    def test_docker_runtime_returned(self):
        rt = get_runtime("docker", run_id="test", repo_path="/tmp")
        assert rt.__class__.__name__ == "DockerRuntime"

    def test_unknown_mode_defaults_to_local(self):
        rt = get_runtime("unknown")
        assert rt.__class__.__name__ == "LocalRuntime"
