"""Readiness: the things this instance needs in order to do its job, each checked for real."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from patchquest.database import get_db
from patchquest.persistence.migrations import current_version
from patchquest.persistence.schema import MIGRATIONS
from patchquest.runtime import workspace


def readiness() -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    try:
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
            applied = current_version(conn)
        latest = max(m.version for m in MIGRATIONS)
        checks["database"] = {"ok": True}
        checks["schema"] = {"ok": applied == latest, "applied": applied, "expected": latest,
                            **({} if applied == latest else {"hint": "run `patchquest doctor`; migrations apply on start"})}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}
        checks["schema"] = {"ok": False}
    try:
        base = workspace.WORKSPACE_BASE
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=base):
            pass
        checks["workspace_storage"] = {"ok": os.access(base, os.W_OK)}
    except OSError as exc:
        checks["workspace_storage"] = {"ok": False, "error": type(exc).__name__}
    return checks
