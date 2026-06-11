"""Tests for memory invalidation."""

import tempfile
from pathlib import Path

from patchquest.database import get_db, init_db, now_iso, set_db_path
from patchquest.memory.invalidation import check_and_invalidate


def setup_function():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    set_db_path(Path(tmp.name))
    init_db()


def test_same_hash_remains_fresh():
    repo_dir = tempfile.mkdtemp()
    test_file = Path(repo_dir) / "test.py"
    test_file.write_text("hello")

    import hashlib
    h = hashlib.sha256(b"hello").hexdigest()[:16]

    with get_db() as conn:
        conn.execute(
            """INSERT INTO memory_records (scope, record_type, key, value_json, source_path, file_hash, status, created_at, updated_at)
               VALUES ('repo', 'fact', 'test', '"value"', 'test.py', ?, 'fresh', ?, ?)""",
            (h, now_iso(), now_iso()),
        )

    result = check_and_invalidate(repo_dir)
    assert result["stale"] == 0
    assert result["invalid"] == 0


def test_changed_hash_marks_stale():
    repo_dir = tempfile.mkdtemp()
    test_file = Path(repo_dir) / "test.py"
    test_file.write_text("changed content")

    with get_db() as conn:
        conn.execute(
            """INSERT INTO memory_records (scope, record_type, key, value_json, source_path, file_hash, status, created_at, updated_at)
               VALUES ('repo', 'fact', 'test', '"value"', 'test.py', 'oldhash123456789', 'fresh', ?, ?)""",
            (now_iso(), now_iso()),
        )

    result = check_and_invalidate(repo_dir)
    assert result["stale"] == 1


def test_deleted_file_marks_invalid():
    repo_dir = tempfile.mkdtemp()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO memory_records (scope, record_type, key, value_json, source_path, file_hash, status, created_at, updated_at)
               VALUES ('repo', 'fact', 'test', '"value"', 'deleted.py', 'somehash', 'fresh', ?, ?)""",
            (now_iso(), now_iso()),
        )

    result = check_and_invalidate(repo_dir)
    assert result["invalid"] == 1
