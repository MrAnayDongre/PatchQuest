"""Tests for code graph."""

import pytest
from pathlib import Path

from patchquest.database import init_db, set_db_path
from patchquest.memory.code_graph import (
    add_edge,
    clear_repo,
    find_definitions,
    find_importers,
    find_test_files,
    get_file_symbols,
    get_graph_stats,
    get_most_connected,
    index_file_symbols,
    init_code_graph,
    upsert_node,
)


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    init_db()


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
