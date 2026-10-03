"""The repository index is incremental, never duplicates symbols, forgets deleted files, and says how much it reused."""

from __future__ import annotations

import os
import time

from patchquest.database import get_db
from patchquest.memory.repo_indexer import index_repo
from patchquest.memory.repo_map import get_repo_map


def age(path, seconds=60):
    """Make a file look old enough that its timestamp is trusted (a just-written file is always re-hashed)."""
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def make(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        age(p)
    return str(tmp_path)


def test_a_second_pass_over_an_unchanged_repository_reads_nothing(tmp_path):
    repo = make(tmp_path, {"a.py": "def a():\n    pass\n", "b.py": "def b():\n    pass\n\nclass C:\n    pass\n", "notes.md": "hi\n"})
    cold = index_repo(repo)
    assert cold["files_new"] == 3 and cold["files_reprocessed"] == 3 and cold["symbols_reprocessed"] == 3 and cold["cache_hit_rate"] == 0.0
    warm = index_repo(repo)
    assert warm["files_unchanged"] == 3 and warm["files_reprocessed"] == 0 and warm["symbols_reprocessed"] == 0 and warm["cache_hit_rate"] == 1.0
    assert warm["symbols_indexed"] == 3  # still there, and not doubled


def test_symbols_are_never_duplicated_by_repeated_runs(tmp_path):
    repo = make(tmp_path, {"a.py": "def a():\n    pass\n"})
    for _ in range(5):
        index_repo(repo)
    assert [s["name"] for s in get_repo_map(repo)["symbols"]] == ["a"]


def test_only_the_changed_file_is_reprocessed_and_its_symbols_replaced(tmp_path):
    repo = make(tmp_path, {"a.py": "def a():\n    pass\n", "b.py": "def b():\n    pass\n", "c.py": "def c():\n    pass\n"})
    index_repo(repo)
    (tmp_path / "b.py").write_text("def b2():\n    pass\n\ndef b3():\n    pass\n")
    age(tmp_path / "b.py")
    out = index_repo(repo)
    assert (out["files_changed"], out["files_unchanged"], out["files_reprocessed"], out["symbols_reprocessed"]) == (1, 2, 1, 2)
    assert sorted(s["name"] for s in get_repo_map(repo)["symbols"]) == ["a", "b2", "b3", "c"]  # b's old symbol is gone


def test_a_touched_file_with_the_same_content_is_not_reparsed(tmp_path):
    repo = make(tmp_path, {"a.py": "def a():\n    pass\n"})
    index_repo(repo)
    age(tmp_path / "a.py", seconds=30)  # same bytes, new timestamp
    out = index_repo(repo)
    assert out["files_touched_only"] == 1 and out["files_reprocessed"] == 0 and out["symbols_reprocessed"] == 0 and out["cache_hit_rate"] == 1.0


def test_deleted_files_and_their_symbols_leave_the_index(tmp_path):
    repo = make(tmp_path, {"a.py": "def a():\n    pass\n", "gone.py": "def gone():\n    pass\n"})
    index_repo(repo)
    (tmp_path / "gone.py").unlink()
    out = index_repo(repo)
    assert out["files_removed"] == 1 and out["files_indexed"] == 1
    m = get_repo_map(repo)
    assert [f["file_path"] for f in m["files"]] == ["a.py"] and [s["name"] for s in m["symbols"]] == ["a"]


def test_a_file_edited_within_the_timestamp_granularity_is_still_noticed(tmp_path):
    repo = make(tmp_path, {})
    path = tmp_path / "a.py"
    path.write_text("def old():\n    pass\n")  # just written: racily clean, so always re-hashed
    index_repo(repo)
    path.write_text("def new1():\n    pass\n")  # same size, same second
    out = index_repo(repo)
    assert out["files_changed"] == 1 and [s["name"] for s in get_repo_map(repo)["symbols"]] == ["new1"]


def test_the_migration_removes_duplicates_written_by_older_releases(tmp_path):
    repo = make(tmp_path, {"a.py": "def a():\n    pass\n"})
    index_repo(repo)
    with get_db() as conn:
        conn.execute("DROP INDEX idx_symbols_unique")  # what an old database looks like, with its duplicates
        row = conn.execute("SELECT * FROM repo_symbols").fetchone()
        for _ in range(3):
            conn.execute("INSERT INTO repo_symbols (repo_path, file_path, symbol_type, name, line_start, line_end, parent, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (row["repo_path"], row["file_path"], row["symbol_type"], row["name"], row["line_start"], row["line_end"], row["parent"], row["indexed_at"]))
        from patchquest.persistence.schema import _index_incremental
        _index_incremental(conn)
        assert conn.execute("SELECT COUNT(*) FROM repo_symbols").fetchone()[0] == 1
