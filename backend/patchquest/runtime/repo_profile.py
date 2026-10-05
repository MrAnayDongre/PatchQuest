"""A repository profile: what PatchQuest knows about how a codebase is built and tested.

Detection reads manifests and layout only and never executes anything it finds; running a discovered command
stays behind the command policy and sandbox. Each field is stored as a memory (kind ``repository``, scope
``repository``, key ``profile.<field>``) so it inherits provenance, confidence, versioning and invalidation:
the files it was derived from are its evidence, and when their hashes change the field is recomputed. A person
can override any field; a detection refresh never replaces a person's value.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from patchquest.domain.memory import Memory, MemoryKind, Source, Status, WriteOutcome
from patchquest.domain.policy import Scope
from patchquest.persistence import memories
from patchquest.persistence.memories import Owner

FIELDS = ("languages", "package_managers", "test_frameworks", "test_commands", "lint_commands", "typecheck_commands",
          "build_commands", "ci_provider", "monorepo", "source_roots", "test_roots", "generated_dirs", "vendor_dirs",
          "protected_paths")
PREFIX = "profile."
FINGERPRINT_KEY = PREFIX + "_layout"
MAX_FILES = 5000
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "env", "target", "dist", "build", "__pycache__", ".tox", ".mypy_cache",
                       ".pytest_cache", ".ruff_cache", ".next", "coverage", "htmlcov", "site-packages"})
EXTENSIONS = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript", ".go": "go",
              ".rs": "rust", ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".c": "c", ".h": "c", ".cc": "c++", ".cpp": "c++",
              ".cs": "csharp", ".php": "php", ".swift": "swift"}
CANDIDATES = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "poetry.lock", "uv.lock", "Pipfile", "tox.ini", "pytest.ini",
              "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "pnpm-workspace.yaml", "tsconfig.json", "Cargo.toml",
              "Cargo.lock", "go.mod", "go.work", "Makefile", ".gitlab-ci.yml", "Jenkinsfile", ".circleci/config.yml", ".travis.yml",
              "azure-pipelines.yml", ".gitattributes")
CI_DIR = ".github/workflows"


@dataclass
class Entry:
    value: Any
    files: list[str] = field(default_factory=list)  # evidence
    confidence: float = 0.8


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _evidence_files(root: Path) -> list[str]:
    found = [c for c in CANDIDATES if (root / c).is_file()]
    ci = root / CI_DIR
    if ci.is_dir():
        found += sorted(f"{CI_DIR}/{p.name}" for p in ci.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())[:20]
    return found


def layout_fingerprint(repo: str) -> tuple[str, dict[str, str]]:
    """A hash over the manifests we read and the names at the top level: cheap, and changes when profile inputs change."""
    root = Path(repo)
    evidence = {f: _sha(root / f) for f in _evidence_files(root)}
    try:
        top = sorted(e.name for e in root.iterdir() if e.name != ".git")[:200]
    except OSError:
        top = []
    digest = hashlib.sha256(json.dumps([sorted(evidence.items()), top]).encode()).hexdigest()[:20]
    return digest, evidence


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(errors="replace"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _walk(root: Path) -> list[str]:
    counts: dict[str, int] = {}
    seen = 0
    for _, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            seen += 1
            lang = EXTENSIONS.get(Path(name).suffix.lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
        if seen >= MAX_FILES:
            break
    return sorted(counts, key=lambda k: (-counts[k], k))


def detect(repo: str) -> dict[str, Entry]:
    """Compute every field from the repository's files. Pure reads; empty fields are omitted."""
    from patchquest.tools.test_runner import detect_check_commands, detect_test_commands

    root = Path(repo)
    out: dict[str, Entry] = {}
    has = lambda name: (root / name).is_file()  # noqa: E731
    pyproject = _toml(root / "pyproject.toml") if has("pyproject.toml") else {}
    pkg = _json(root / "package.json") if has("package.json") else {}

    ordered = _walk(root)
    if ordered:
        out["languages"] = Entry(ordered, [], 0.9)

    managers: list[tuple[str, str]] = []
    if has("uv.lock"):
        managers.append(("uv", "uv.lock"))
    if has("poetry.lock") or "poetry" in pyproject.get("tool", {}):
        managers.append(("poetry", "poetry.lock" if has("poetry.lock") else "pyproject.toml"))
    if has("Pipfile"):
        managers.append(("pipenv", "Pipfile"))
    if has("requirements.txt") or (has("pyproject.toml") and not managers):
        managers.append(("pip", "requirements.txt" if has("requirements.txt") else "pyproject.toml"))
    for name, lock in (("pnpm", "pnpm-lock.yaml"), ("yarn", "yarn.lock"), ("npm", "package-lock.json")):
        if has(lock):
            managers.append((name, lock))
    if has("package.json") and not any(m[0] in ("pnpm", "yarn", "npm") for m in managers):
        managers.append(("npm", "package.json"))
    if has("Cargo.toml"):
        managers.append(("cargo", "Cargo.toml"))
    if has("go.mod"):
        managers.append(("go", "go.mod"))
    if managers:
        out["package_managers"] = Entry([m for m, _ in managers], sorted({f for _, f in managers}), 0.9)

    frameworks: list[tuple[str, str]] = []
    if has("pytest.ini") or "pytest" in str(pyproject.get("tool", {})) or "pytest" in _safe_text(root / "tox.ini"):
        frameworks.append(("pytest", "pytest.ini" if has("pytest.ini") else "pyproject.toml" if has("pyproject.toml") else "tox.ini"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name in ("vitest", "jest", "mocha"):
        if name in deps:
            frameworks.append((name, "package.json"))
    if has("Cargo.toml"):
        frameworks.append(("cargo test", "Cargo.toml"))
    if has("go.mod"):
        frameworks.append(("go test", "go.mod"))
    if frameworks:
        out["test_frameworks"] = Entry([f for f, _ in frameworks], sorted({f for _, f in frameworks}), 0.85)

    tests = detect_test_commands(repo)
    if tests:
        out["test_commands"] = Entry(tests, [f for f in ("pyproject.toml", "pytest.ini", "tox.ini", "package.json", "Cargo.toml", "Makefile", "go.mod") if has(f)], 0.7)
    lint: list[str] = []
    typecheck: list[str] = []
    build: list[str] = []
    for cmd in detect_check_commands(repo):
        (typecheck if cmd.startswith(("mypy", "cargo check")) or "typecheck" in cmd else build if cmd.endswith("build") else lint).append(cmd)
    for key, cmds in (("lint_commands", lint), ("typecheck_commands", typecheck), ("build_commands", build)):
        if cmds:
            out[key] = Entry(cmds, [f for f in ("pyproject.toml", "package.json", "Cargo.toml") if has(f)], 0.7)

    ci = root / CI_DIR
    for provider, probe in (("github_actions", ci.is_dir() and any(ci.glob("*.y*ml"))), ("gitlab_ci", has(".gitlab-ci.yml")),
                            ("jenkins", has("Jenkinsfile")), ("circleci", has(".circleci/config.yml")),
                            ("travis", has(".travis.yml")), ("azure_pipelines", has("azure-pipelines.yml"))):
        if probe:
            out["ci_provider"] = Entry(provider, [f for f in _evidence_files(root) if f.startswith((CI_DIR, ".gitlab", "Jenkins", ".circleci", ".travis", "azure"))], 0.95)
            break

    packages = _packages(root, pkg)
    if packages:
        out["monorepo"] = Entry({"packages": packages[:20], "count": len(packages)}, [f for f in ("package.json", "pnpm-workspace.yaml", "go.work", "Cargo.toml") if has(f)], 0.8)

    dirs = {p.name for p in root.iterdir() if p.is_dir()} if root.is_dir() else set()
    for key, names in (("source_roots", ("src", "lib", "app", "pkg", "cmd", "packages")), ("test_roots", ("tests", "test", "spec", "__tests__")),
                       ("generated_dirs", ("dist", "build", "out", ".next", "target", "coverage", "htmlcov")),
                       ("vendor_dirs", ("node_modules", "vendor", "third_party", ".venv", "venv"))):
        found = [n for n in names if n in dirs]
        if found:
            out[key] = Entry(found, [], 0.85)
    protected = [p for p in (".git/", ".github/workflows/", ".env") if (root / p.rstrip("/")).exists()]
    if protected:
        out["protected_paths"] = Entry(protected, [], 0.9)
    return out


def _safe_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _packages(root: Path, pkg: dict[str, Any]) -> list[str]:
    declared = pkg.get("workspaces")
    if isinstance(declared, dict):
        declared = declared.get("packages")
    names: list[str] = []
    if isinstance(declared, list):
        for pattern in declared[:20]:
            if isinstance(pattern, str) and ".." not in pattern and not pattern.startswith("/"):
                names += sorted(str(p.relative_to(root)) for p in root.glob(pattern) if p.is_dir())[:50]
    if not names and (root / "pnpm-workspace.yaml").is_file():
        names = sorted(str(p.parent.relative_to(root)) for p in root.glob("packages/*/package.json"))[:50]
    return names


@dataclass
class Refresh:
    fast_path: bool = False
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept_user_value: list[str] = field(default_factory=list)

    @property
    def any_change(self) -> bool:
        return bool(self.changed or self.removed)

    def to_dict(self) -> dict[str, Any]:
        return {"fast_path": self.fast_path, "changed": self.changed, "removed": self.removed, "unchanged_count": len(self.unchanged),
                "kept_user_value": self.kept_user_value}


def refresh(conn: sqlite3.Connection, owner: Owner, repo: str, *, force: bool = False) -> Refresh:
    """Bring the stored profile up to date. When no manifest and no top-level name changed this is one hash pass."""
    repo = str(Path(repo).resolve())
    digest, evidence = layout_fingerprint(repo)
    stored = memories.active(conn, owner, MemoryKind.REPOSITORY, Scope.REPOSITORY, repo, FINGERPRINT_KEY)
    if stored is not None and stored.value == digest and not force:
        return Refresh(fast_path=True)

    result = Refresh()
    detected = detect(repo)
    for name in FIELDS:
        key = PREFIX + name
        entry = detected.get(name)
        if entry is None:
            current = memories.active(conn, owner, MemoryKind.REPOSITORY, Scope.REPOSITORY, repo, key)
            if current is not None and current.source is Source.REPOSITORY_DETECTED:
                memories.set_status(conn, current.id, Status.STALE)  # what it described is gone
                result.removed.append(name)
            continue
        outcome, _ = memories.put(
            conn, owner, kind=MemoryKind.REPOSITORY, scope=Scope.REPOSITORY, scope_id=repo, key=key, value=entry.value,
            source=Source.REPOSITORY_DETECTED, reason=f"detected from {', '.join(entry.files) or 'repository layout'}",
            authored_by="detector:repo_profile", confidence=entry.confidence,
            evidence={f: evidence.get(f) or _sha(Path(repo) / f) for f in entry.files})
        if outcome in (WriteOutcome.CREATED, WriteOutcome.UPDATED):
            result.changed.append(name)
        elif outcome is WriteOutcome.KEPT_EXISTING:
            result.kept_user_value.append(name)
        else:
            result.unchanged.append(name)
    memories.put(conn, owner, kind=MemoryKind.REPOSITORY, scope=Scope.REPOSITORY, scope_id=repo, key=FINGERPRINT_KEY, value=digest,
                 source=Source.REPOSITORY_DETECTED, reason="fingerprint of the manifests and top-level names the profile was built from",
                 authored_by="detector:repo_profile", evidence=evidence)
    return result


def override(conn: sqlite3.Connection, owner: Owner, repo: str, name: str, value: Any, actor: str) -> Memory:
    """A person states a field's value; detection will not replace it."""
    repo = str(Path(repo).resolve())
    if name not in FIELDS:
        raise ValueError(f"unknown profile field '{name}' (known: {', '.join(FIELDS)})")
    return memories.put(conn, owner, kind=MemoryKind.REPOSITORY, scope=Scope.REPOSITORY, scope_id=repo, key=PREFIX + name, value=value,
                        source=Source.USER_EXPLICIT, reason="set by a person", authored_by=actor)[1]


def profile(conn: sqlite3.Connection, owner: Owner, repo: str) -> dict[str, dict[str, Any]]:
    """The current profile: per field its value and where that value came from."""
    from patchquest.domain.memory import utcnow

    now = utcnow()
    repo = str(Path(repo).resolve())
    out = {}
    for m in memories.visible(conn, owner, repo=repo, kinds=(MemoryKind.REPOSITORY,)):
        if m.scope is Scope.REPOSITORY and m.key.startswith(PREFIX) and m.key != FINGERPRINT_KEY:
            out[m.key[len(PREFIX):]] = {"value": m.value, "source": m.source.value, "reason": m.reason, "confidence": m.effective_confidence(now),
                                        "last_verified": m.last_verified_at.isoformat(), "memory_id": m.id, "evidence": dict(m.evidence)}
    return dict(sorted(out.items(), key=lambda kv: FIELDS.index(kv[0]) if kv[0] in FIELDS else 99))
