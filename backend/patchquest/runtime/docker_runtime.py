"""Docker sandbox runtime - executes commands in isolated containers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from patchquest.config import get_config
from patchquest.memory.repo_indexer import IGNORED_DIRS
from patchquest.runtime.sandbox_base import RuntimeBase
from patchquest.tools.secret_guard import redact_secrets

SANDBOX_BASE = Path.home() / ".patchquest" / "sandboxes"

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
        docker_cfg = getattr(config, "runtime", None)
        image = "patchquest-sandbox:latest"
        memory = "2g"
        cpus = "2"
        pids_limit = "512"
        network = False
        output_limit = config.safety.max_output_bytes

        if docker_cfg and hasattr(docker_cfg, "docker"):
            dc = docker_cfg.docker
            image = getattr(dc, "image", image)
            memory = getattr(dc, "memory", memory)
            cpus = str(getattr(dc, "cpus", cpus))
            pids_limit = str(getattr(dc, "pids_limit", pids_limit))
            network = getattr(dc, "network", False)
            timeout = getattr(dc, "timeout_seconds", timeout)
            output_limit = getattr(dc, "output_limit_bytes", output_limit)

        workspace = self._get_sandbox_workspace()
        if not workspace.exists():
            return {
                "success": False, "returncode": -1,
                "stdout": "", "stderr": "Sandbox workspace not created. Call prepare_sandbox() first.",
                "truncated": False,
            }

        docker_cmd = build_docker_command(
            command=command,
            workspace=str(workspace),
            image=image,
            memory=memory,
            cpus=cpus,
            pids_limit=pids_limit,
            network=network,
            timeout=timeout,
        )

        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
            )
            stdout = result.stdout[:output_limit] if result.stdout else ""
            stderr = result.stderr[:output_limit] if result.stderr else ""
            stdout = redact_secrets(stdout)
            stderr = redact_secrets(stderr)

            return {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": len(result.stdout or "") > output_limit,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False, "returncode": -1,
                "stdout": "", "stderr": f"Docker command timed out after {timeout}s",
                "truncated": False,
            }
        except Exception as e:
            return {
                "success": False, "returncode": -1,
                "stdout": "", "stderr": f"Docker execution error: {e}",
                "truncated": False,
            }

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
        if not str(resolved).startswith(str(SANDBOX_BASE.resolve())):
            return False
        try:
            shutil.rmtree(workspace)
            return True
        except Exception:
            return False

    def _get_sandbox_workspace(self) -> Path:
        if self._sandbox_path is None:
            self._sandbox_path = SANDBOX_BASE / self.run_id / "workspace"
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
    pids_limit: str = "512",
    network: bool = False,
    timeout: int = 120,
) -> list[str]:
    cmd = [
        "docker", "run", "--rm",
        "--memory", memory,
        "--cpus", cpus,
        "--pids-limit", pids_limit,
        "-v", f"{workspace}:/workspace",
        "-w", "/workspace",
    ]

    if not network:
        cmd.append("--network=none")

    cmd.extend(["--stop-timeout", str(timeout)])
    cmd.append(image)
    cmd.extend(["sh", "-c", command])

    return cmd


def _copy_repo_to_sandbox(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    for item in source.iterdir():
        if item.name in COPY_EXCLUDE_DIRS:
            continue
        if item.is_file() and _should_exclude_file(item):
            continue

        target = dest / item.name
        if item.is_dir():
            shutil.copytree(
                item, target,
                ignore=shutil.ignore_patterns(*COPY_EXCLUDE_DIRS, "*.env", "*.env.*"),
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(item, target)


def _should_exclude_file(path: Path) -> bool:
    name = path.name.lower()
    if name in COPY_EXCLUDE_FILES:
        return True
    if name.startswith(".env"):
        return True
    if path.suffix in (".pem", ".key", ".p12", ".pfx"):
        return True
    return False
