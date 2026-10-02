"""Repository indexing, the code graph and memory invalidation.

Invariants: indexing finds files and symbols and ignores build/vendor directories; the graph relates files and
symbols; changed content invalidates stale memory.
"""

import os
import tempfile
from pathlib import Path

from patchquest.database import get_db, init_db, now_iso, set_db_path
from patchquest.memory.code_graph import (
    add_edge,
    clear_repo,
    find_definitions,
    find_importers,
    get_file_symbols,
    get_graph_stats,
    get_most_connected,
    index_file_symbols,
    upsert_node,
)
from patchquest.memory.invalidation import check_and_invalidate
from patchquest.memory.repo_indexer import index_repo
from patchquest.memory.symbol_extractors import extract_symbols

# ======================================================================
# Indexing
# ======================================================================

class TestRepoIndexer:
    def setup_method(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        set_db_path(Path(tmp.name))
        init_db()

    def test_indexes_python_files(self):
        repo = tempfile.mkdtemp()
        src_dir = os.path.join(repo, "src")
        os.makedirs(src_dir)
        with open(os.path.join(src_dir, "main.py"), "w") as f:
            f.write("def hello():\n    pass\n")

        result = index_repo(repo)
        assert result["files_indexed"] >= 1
        assert result["error"] is None

    def test_skips_ignored_directories(self):
        repo = tempfile.mkdtemp()
        for ignored in ["node_modules", "__pycache__", ".git"]:
            ignored_dir = os.path.join(repo, ignored)
            os.makedirs(ignored_dir)
            with open(os.path.join(ignored_dir, "file.py"), "w") as f:
                f.write("should_not_be_indexed = True\n")

        with open(os.path.join(repo, "real.py"), "w") as f:
            f.write("should_index = True\n")

        result = index_repo(repo)
        assert result["files_indexed"] == 1

    def test_handles_nonexistent_directory(self):
        result = index_repo("/nonexistent/path/does/not/exist")
        assert result["error"] is not None

    def test_extracts_symbols_from_python(self):
        repo = tempfile.mkdtemp()
        with open(os.path.join(repo, "app.py"), "w") as f:
            f.write("class MyClass:\n    def method(self):\n        pass\n\ndef standalone():\n    pass\n")

        result = index_repo(repo)
        assert result["symbols_indexed"] >= 3  # class + method + function


class TestSymbolExtractors:
    def test_python_functions(self):
        code = "def foo():\n    pass\n\nasync def bar():\n    pass\n"
        symbols = extract_symbols(code, "python")
        names = [s["name"] for s in symbols]
        assert "foo" in names
        assert "bar" in names

    def test_python_classes(self):
        code = "class MyClass:\n    def method(self):\n        pass\n"
        symbols = extract_symbols(code, "python")
        types = {s["name"]: s["type"] for s in symbols}
        assert types["MyClass"] == "class"
        assert types["method"] == "method"

    def test_typescript_extraction(self):
        code = "export function handleClick() {}\nconst doThing = async () => {}\nclass App {}\n"
        symbols = extract_symbols(code, "typescript")
        names = [s["name"] for s in symbols]
        assert "handleClick" in names
        assert "App" in names

    def test_rust_extraction(self):
        code = "pub fn main() {}\nstruct Config {}\nenum Status {}\n"
        symbols = extract_symbols(code, "rust")
        names = [s["name"] for s in symbols]
        assert "main" in names
        assert "Config" in names
        assert "Status" in names

    def test_handles_syntax_errors(self):
        code = "def broken(\n"
        symbols = extract_symbols(code, "python")
        assert symbols == []

    def test_unsupported_language_returns_empty(self):
        symbols = extract_symbols("whatever", "brainfuck")
        assert symbols == []


# ======================================================================
# Code graph
# ======================================================================

def test_upsert_and_find_node():
    upsert_node("repo", "function", "process", "main.py", 10, 20, "ast")
    defs = find_definitions("repo", "process")
    assert len(defs) == 1
    assert defs[0]["file_path"] == "main.py"
    assert defs[0]["parser_source"] == "ast"


def test_add_and_find_edges():
    upsert_node("repo", "import", "utils", "main.py", 1, parser_source="ast")
    add_edge("repo", "main.py", "utils", "imports", "utils")

    importers = find_importers("repo", "utils")
    assert len(importers) == 1
    assert importers[0]["source_file"] == "main.py"


def test_index_file_symbols():
    symbols = [
        {"type": "function", "name": "foo", "line_start": 1, "line_end": 5, "parser_source": "tree_sitter"},
        {"type": "import", "name": "bar", "line_start": 1, "parser_source": "tree_sitter"},
    ]
    index_file_symbols("repo", "test.py", symbols)

    file_syms = get_file_symbols("repo", "test.py")
    assert len(file_syms) == 2

    defs = find_definitions("repo", "foo")
    assert len(defs) == 1


def test_index_replaces_previous():
    index_file_symbols("repo", "test.py", [{"type": "function", "name": "old", "line_start": 1, "parser_source": "ast"}])
    index_file_symbols("repo", "test.py", [{"type": "function", "name": "new", "line_start": 1, "parser_source": "ast"}])

    syms = get_file_symbols("repo", "test.py")
    assert len(syms) == 1
    assert syms[0]["name"] == "new"


def test_graph_stats():
    index_file_symbols("repo", "a.py", [
        {"type": "function", "name": "a_func", "line_start": 1, "parser_source": "ast"},
        {"type": "import", "name": "os", "line_start": 1, "parser_source": "ast"},
    ])
    stats = get_graph_stats("repo")
    assert stats["nodes"] == 2
    assert stats["edges"] >= 1


def test_most_connected():
    for i in range(5):
        add_edge("repo", f"file{i}.py", "os", "imports", "os")
    add_edge("repo", "file0.py", "sys", "imports", "sys")

    top = get_most_connected("repo", limit=2)
    assert top[0]["target_name"] == "os"
    assert top[0]["ref_count"] == 5


def test_clear_repo():
    index_file_symbols("repo", "f.py", [{"type": "function", "name": "x", "line_start": 1, "parser_source": "ast"}])
    clear_repo("repo")
    assert get_graph_stats("repo")["nodes"] == 0


# ======================================================================
# Memory invalidation
# ======================================================================

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
