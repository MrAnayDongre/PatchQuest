"""Memory invalidation based on file hash changes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from patchquest.database import get_db, now_iso


def check_and_invalidate(repo_path: str) -> dict[str, int]:
    stale_count = 0
    invalid_count = 0
    now = now_iso()

    with get_db() as conn:
        records = conn.execute(
            "SELECT id, source_path, file_hash FROM memory_records WHERE status = 'fresh' AND source_path IS NOT NULL"
        ).fetchall()

        for record in records:
            full_path = Path(repo_path) / record["source_path"]

            if not full_path.exists():
                conn.execute(
                    "UPDATE memory_records SET status = 'invalid', updated_at = ? WHERE id = ?",
                    (now, record["id"]),
                )
                invalid_count += 1
                continue

            if record["file_hash"]:
                current_hash = _hash_file(full_path)
                if current_hash != record["file_hash"]:
                    conn.execute(
                        "UPDATE memory_records SET status = 'stale', updated_at = ? WHERE id = ?",
                        (now, record["id"]),
                    )
                    stale_count += 1

    return {"stale": stale_count, "invalid": invalid_count}


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
    except (OSError, PermissionError):
        return ""
    return h.hexdigest()[:16]
