"""Shadow workspace: a filtered copy of the repo where patches are applied and validated.

The real repository is only touched by :meth:`ShadowWorkspace.promote`, after the change has
been proven in the copy, and only if every touched file still has the content the change was
planned against. Local and Docker runtimes both execute inside this directory.
"""

from __future__ import annotations

import difflib
import os
import shutil
import stat
from pathlib import Path

from patchquest.memory.repo_indexer import IGNORED_DIRS
from patchquest.patching import Change, DeleteFile, PatchResult, WriteFile, apply_changes, sha256_bytes
from patchquest.paths import resolve_in_repo

WORKSPACE_BASE = Path.home() / ".patchquest" / "sandboxes"
MAX_COPY_BYTES = 750 * 1024 * 1024
SECRET_FILE_NAMES = {
    ".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".netrc", ".git-credentials", ".npmrc", ".pypirc",
}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx")


def is_secret_file(name: str) -> bool:
    lower = name.lower()
    return lower in SECRET_FILE_NAMES or lower.startswith(".env") or lower.endswith(SECRET_SUFFIXES)


def copy_repo(source: Path, dest: Path) -> int:
    """Copy ``source`` to ``dest`` without secrets, ignored dirs or symlinks. Returns bytes copied."""
    total = 0
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not (Path(dirpath) / d).is_symlink()]
        rel_dir = Path(dirpath).relative_to(source)
        (dest / rel_dir).mkdir(parents=True, exist_ok=True)
        for name in filenames:
            src = Path(dirpath) / name
            if is_secret_file(name):
                continue
            try:
                mode = os.lstat(src).st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):  # symlinks, sockets, FIFOs, devices are never copied
                continue
            total += os.lstat(src).st_size
            if total > MAX_COPY_BYTES:
                raise RuntimeError(f"repository is larger than {MAX_COPY_BYTES // 2**20} MiB; refusing to copy")
            shutil.copy2(src, dest / rel_dir / name)
    return total


class ShadowWorkspace:
    def __init__(self, run_id: str, repo_path: str, base: Path | None = None) -> None:
        self.run_id = run_id
        self.repo_path = str(Path(repo_path).resolve())
        self.root = (base or WORKSPACE_BASE) / run_id
        self.path = self.root / "workspace"
        # rel path -> original bytes (None => did not exist), captured on first touch.
        self.base: dict[str, bytes | None] = {}

    def create(self) -> Path:
        if self.root.is_relative_to(Path.home() / ".patchquest"):
            from patchquest.database import ensure_state_dir

            ensure_state_dir()
        if self.root.exists():
            shutil.rmtree(self.root)
        copy_repo(Path(self.repo_path), self.path)
        return self.path

    def cleanup(self) -> None:
        if self.root.exists() and self.root.resolve().is_relative_to(WORKSPACE_BASE.resolve()):
            shutil.rmtree(self.root, ignore_errors=True)

    # --- state tracking -------------------------------------------------
    def _read(self, rel: str) -> bytes | None:
        target = resolve_in_repo(str(self.path), rel)
        return target.read_bytes() if target.is_file() else None

    def note_touched(self, rel_paths: list[str]) -> None:
        for rel in rel_paths:
            if rel not in self.base:
                self.base[rel] = self._read(rel)

    @property
    def touched(self) -> list[str]:
        return sorted(self.base)

    def checkpoint(self) -> dict[str, bytes | None]:
        return {rel: self._read(rel) for rel in self.base}

    def restore(self, state: dict[str, bytes | None]) -> None:
        for rel, raw in state.items():
            target = resolve_in_repo(str(self.path), rel, for_write=True)
            if raw is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)

    def restore_base(self) -> None:
        self.restore(self.base)

    def diff(self) -> str:
        """Unified diff of every touched file: original (base) -> current workspace state."""
        parts: list[str] = []
        for rel in self.touched:
            old, new = self.base[rel], self._read(rel)
            if old == new:
                continue
            old_l = (old or b"").decode("utf-8", "replace").split("\n") if old else []
            new_l = (new or b"").decode("utf-8", "replace").split("\n") if new else []
            parts.append("\n".join(difflib.unified_diff(
                old_l, new_l, f"a/{rel}" if old is not None else "/dev/null",
                f"b/{rel}" if new is not None else "/dev/null", lineterm="",
            )))
        return "\n".join(parts) + ("\n" if parts else "")

    def summary(self) -> list[dict]:
        out = []
        for rel in self.touched:
            old, new = self.base[rel], self._read(rel)
            if old == new:
                continue
            action = "create" if old is None else "delete" if new is None else "modify"
            old_l = set((old or b"").decode("utf-8", "replace").split("\n"))
            new_l = (new or b"").decode("utf-8", "replace").split("\n")
            added = sum(1 for ln in new_l if ln not in old_l)
            out.append({"path": rel, "action": action, "added": added})
        return out

    # --- promotion -------------------------------------------------------
    def promotion_manifest(self) -> dict[str, dict[str, str | None]]:
        """What promotion will write: per changed file, the sha256 expected now (``base``) and the one
        it will have afterwards (``new``). Journaled before promotion so a crash can be reconciled."""
        manifest: dict[str, dict[str, str | None]] = {}
        for rel in self.touched:
            new = self._read(rel)
            if new != self.base[rel]:
                manifest[rel] = {"base": None if self.base[rel] is None else sha256_bytes(self.base[rel]),
                                 "new": None if new is None else sha256_bytes(new)}
        return manifest

    def adopt(self, base: dict[str, bytes | None], current: dict[str, bytes | None]) -> None:
        """Rebuild a workspace's tracked state from a checkpoint: originals plus current contents."""
        self.base = dict(base)
        self.restore(current)

    def promote(self) -> PatchResult:
        """Write every touched file's workspace state into the real repo, atomically."""
        changes: list[Change] = []
        expected: dict[str, str | None] = {}
        for rel in self.touched:
            expected[rel] = sha256_bytes(self.base[rel])
            current = self._read(rel)
            if current is None:
                if self.base[rel] is not None:
                    changes.append(DeleteFile(rel))
            elif current != self.base[rel]:
                changes.append(WriteFile(rel, current.decode("utf-8")))
        return apply_changes(self.repo_path, changes, expected_hashes=expected)
