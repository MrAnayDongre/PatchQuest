"""A deterministic demo environment: realistic repositories, history, integrations (simulated) and a live approval.

Everything here is clearly demo data. It lives in its own directory (never in the real state), uses scripted models and
simulated GitHub/Slack servers, and is rebuilt from nothing by ``patchquest demo reset``. See ``docs/demo.md``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

MARKER = ".patchquest-demo"


def demo_dir() -> Path:
    return Path(os.environ.get("PATCHQUEST_DEMO_DIR") or Path.home() / ".patchquest" / "demo").expanduser()


def is_demo() -> bool:
    return os.environ.get("PATCHQUEST_DEMO") == "1"


def reset(path: Path | None = None) -> Path:
    """Delete the demo directory and nothing else: it must carry the demo marker and sit apart from the real state."""
    target = (path or demo_dir()).resolve()
    real = (Path.home() / ".patchquest").resolve()
    if target == real or target in real.parents or target == Path.home().resolve():
        raise RuntimeError(f"refusing to reset {target}: it is not a demo directory")
    if target.exists() and not (target / MARKER).is_file():
        raise RuntimeError(f"refusing to reset {target}: it has no {MARKER} marker, so it was not created by the demo")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / MARKER).write_text("created by `patchquest demo`; safe to delete\n")
    return target
