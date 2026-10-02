"""Docker sandbox runtime - executes commands in isolated containers."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from patchquest.config import get_config
from patchquest.execution.executor import run_argv, scrubbed_env
from patchquest.memory.repo_indexer import IGNORED_DIRS
from patchquest.runtime import workspace as workspace_mod  # single source of truth for the base directory
from patchquest.runtime.sandbox_base import RuntimeBase

COPY_EXCLUDE_DIRS = IGNORED_DIRS | {".env", ".env.local", ".env.production"}

COPY_EXCLUDE_FILES = {
    ".env", ".env.local", ".env.development", ".env.production",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    ".pem", ".key", ".p12", ".pfx",
}


class DockerRuntime(RuntimeBase):
    def __init__(self, run_id: str = "default", repo_path: str = "") -> None:
        self.run_id = run_id
        self.repo_path = repo_path
        self._sandbox_path: Path | None = None

    def run_command(self, command: str, cwd: str, timeout: int = 60) -> dict[str, Any]:
        if not self.is_available():
            return {
                "success": False, "returncode": -1,
                "stdout": "", "stderr": "Docker is not available on this system.",
                "truncated": False,
            }

        config = get_config()
        dc = config.runtime.docker
        timeout = dc.timeout_seconds or timeout or config.safety.max_command_timeout
        output_limit = config.safety.max_output_bytes

        workspace = self._get_sandbox_workspace()
        if not workspace.exists():
            return {
                "success": False, "returncode": -1,
                "stdout": "", "stderr": "Sandbox workspace not created. Call prepare_sandbox() first.",
                "truncated": False,
            }

        name = f"pq-{self.run_id[:12]}-{uuid.uuid4().hex[:8]}"
        docker_cmd = build_docker_command(
            command=command,
            workspace=str(workspace),
            image=dc.image,
            memory=dc.memory,
            cpus=dc.cpus,
            pids_limit=str(dc.pids_limit),
            network=dc.network,
            timeout=timeout,
            name=name,
            tmpfs_size=dc.tmpfs_size,
            read_only=dc.read_only_rootfs,
        )

        # The docker CLI runs through the bounded, process-group-aware executor. If it times out the
        # CLI is killed, but that does not stop the container, so the container is removed explicitly.
        result = run_argv(docker_cmd, cwd=str(workspace), timeout=timeout + 10, max_output=output_limit,
                          env=scrubbed_env(passthrough=("DOCKER_*", "XDG_RUNTIME_DIR")))
        if result.get("timed_out"):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
            result["stderr"] = f"Docker command timed out after {timeout}s; container removed"
        return result

    def is_available(self) -> bool:
        return check_docker_available()

    def prepare_sandbox(self) -> dict[str, Any]:
        workspace = self._get_sandbox_workspace()
        if workspace.exists():
            return {"success": True, "path": str(workspace), "already_exists": True}

        if not self.repo_path or not Path(self.repo_path).is_dir():
            return {"success": False, "error": f"Invalid repo path: {self.repo_path}"}

        try:
            workspace.parent.mkdir(parents=True, exist_ok=True)
            _copy_repo_to_sandbox(Path(self.repo_path), workspace)
            return {"success": True, "path": str(workspace)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_sandbox_diff(self) -> str | None:
        workspace = self._get_sandbox_workspace()
        if not workspace.exists():
            return None
        try:
            result = subprocess.run(
                ["diff", "-ruN", "--no-dereference", self.repo_path, str(workspace)],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout if result.stdout else None
        except Exception:
            return None

    def cleanup(self) -> bool:
        workspace = self._get_sandbox_workspace()
        if not workspace.exists():
            return True
        resolved = workspace.resolve()
        if not resolved.is_relative_to(workspace_mod.WORKSPACE_BASE.resolve()):
            return False
        try:
            shutil.rmtree(workspace)
            return True
        except Exception:
            return False

    def _get_sandbox_workspace(self) -> Path:
        if self._sandbox_path is None:
            self._sandbox_path = workspace_mod.WORKSPACE_BASE / self.run_id / "workspace"
        return self._sandbox_path


def check_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def get_docker_version() -> str | None:
    try:
        result = subprocess.run(
            ["docker", "--version"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def check_image_available(image: str = "patchquest-sandbox:latest") -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def build_docker_command(
    command: str,
    workspace: str,
    image: str = "patchquest-sandbox:latest",
    memory: str = "2g",
    cpus: str = "2",
    pids_limit: str = "256",
    network: bool = False,
    timeout: int = 120,
    name: str | None = None,
    tmpfs_size: str = "512m",
    read_only: bool = True,
) -> list[str]:
    """Build the ``docker run`` argv. The workspace is the only host path that is mounted.

    Hardening: no capabilities, no privilege escalation, optional read-only root filesystem (a small
    noexec tmpfs provides /tmp), memory without swap, bounded pids, no network unless enabled, and the
    caller's uid:gid so files written to the workspace stay owned by the user. Host environment
    variables are never forwarded.
    """
    cmd = ["docker", "run", "--rm"]
    if name:
        cmd += ["--name", name]
    cmd += [
        "--memory", memory,
        "--memory-swap", memory,
        "--cpus", cpus,
        "--pids-limit", pids_limit,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "HOME=/tmp",
        "-v", f"{workspace}:/workspace",
        "-w", "/workspace",
    ]
    if read_only:
        cmd += ["--read-only", "--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}"]  # noqa: S108 - container tmpfs
    if hasattr(os, "getuid"):
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]

    if not network:
        cmd.append("--network=none")

    cmd.extend(["--stop-timeout", str(timeout)])
    cmd.append(image)
    cmd.extend(["sh", "-c", command])

    return cmd


def _copy_repo_to_sandbox(source: Path, dest: Path) -> None:
    """Kept for callers; the implementation is the symlink-safe ``runtime.workspace.copy_repo``."""
    from patchquest.runtime.workspace import copy_repo

    copy_repo(source, dest)


def _should_exclude_file(path: Path) -> bool:
    name = path.name.lower()
    if name in COPY_EXCLUDE_FILES:
        return True
    if name.startswith(".env"):
        return True
    if path.suffix in (".pem", ".key", ".p12", ".pfx"):
        return True
    return False
