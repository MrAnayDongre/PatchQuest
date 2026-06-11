"""Tests for Tree-sitter and regex symbol extraction."""

import pytest
from pathlib import Path

from patchquest.memory.symbol_extractors import extract_symbols

FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- Python (uses AST, always available) ---

def test_python_functions():
    content = _read_fixture("sample.py")
    symbols = extract_symbols(content, "python")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "top_level_function" in names
    assert "async_top_level" in names


def test_python_classes():
    content = _read_fixture("sample.py")
    symbols = extract_symbols(content, "python")
    classes = [s for s in symbols if s["type"] == "class"]
    assert any(c["name"] == "MyClass" for c in classes)


def test_python_methods():
    content = _read_fixture("sample.py")
    symbols = extract_symbols(content, "python")
    methods = [s for s in symbols if s["type"] == "method"]
    assert any(m["name"] == "method_one" for m in methods)
    assert any(m["name"] == "async_method" for m in methods)


def test_python_imports():
    content = _read_fixture("sample.py")
    symbols = extract_symbols(content, "python")
    imports = [s["name"] for s in symbols if s["type"] == "import"]
    assert "os" in imports
    assert "pathlib" in imports


def test_python_line_ranges():
    content = _read_fixture("sample.py")
    symbols = extract_symbols(content, "python")
    funcs = [s for s in symbols if s["type"] == "function"]
    for f in funcs:
        assert "line_start" in f
        assert f["line_start"] > 0


def test_python_parser_source():
    content = _read_fixture("sample.py")
    symbols = extract_symbols(content, "python")
    for s in symbols:
        assert s.get("parser_source") in ("ast", "tree_sitter")


# --- TypeScript ---

def test_typescript_functions():
    content = _read_fixture("sample.ts")
    symbols = extract_symbols(content, "typescript")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "processData" in names or "MainComponent" in names


def test_typescript_classes():
    content = _read_fixture("sample.ts")
    symbols = extract_symbols(content, "typescript")
    classes = [s["name"] for s in symbols if s["type"] == "class"]
    assert "DataService" in classes


def test_typescript_imports():
    content = _read_fixture("sample.ts")
    symbols = extract_symbols(content, "typescript")
    imports = [s["name"] for s in symbols if s["type"] == "import"]
    assert len(imports) >= 1


def test_typescript_has_parser_source():
    content = _read_fixture("sample.ts")
    symbols = extract_symbols(content, "typescript")
    for s in symbols:
        assert "parser_source" in s


# --- JavaScript ---

def test_javascript_functions():
    content = _read_fixture("sample.js")
    symbols = extract_symbols(content, "javascript")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "greet" in names or "fetchData" in names


def test_javascript_classes():
    content = _read_fixture("sample.js")
    symbols = extract_symbols(content, "javascript")
    classes = [s["name"] for s in symbols if s["type"] == "class"]
    assert "EventEmitter" in classes


# --- Rust ---

def test_rust_functions():
    content = _read_fixture("sample.rs")
    symbols = extract_symbols(content, "rust")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "create_config" in names or "fetch_data" in names


def test_rust_structs():
    content = _read_fixture("sample.rs")
    symbols = extract_symbols(content, "rust")
    structs = [s["name"] for s in symbols if s["type"] == "struct"]
    assert "Config" in structs


def test_rust_enums():
    content = _read_fixture("sample.rs")
    symbols = extract_symbols(content, "rust")
    enums = [s["name"] for s in symbols if s["type"] == "enum"]
    assert "Status" in enums


def test_rust_traits():
    content = _read_fixture("sample.rs")
    symbols = extract_symbols(content, "rust")
    traits = [s["name"] for s in symbols if s["type"] == "trait"]
    assert "Processor" in traits


# --- C ---

def test_c_functions():
    content = _read_fixture("sample.c")
    symbols = extract_symbols(content, "c")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "add" in names or "main" in names


def test_c_includes():
    content = _read_fixture("sample.c")
    symbols = extract_symbols(content, "c")
    includes = [s["name"] for s in symbols if s["type"] == "include"]
    assert "stdio.h" in includes


def test_c_structs():
    content = _read_fixture("sample.c")
    symbols = extract_symbols(content, "c")
    structs = [s["name"] for s in symbols if s["type"] == "struct"]
    assert "Point" in structs


# --- C++ ---

def test_cpp_classes():
    content = _read_fixture("sample.cpp")
    symbols = extract_symbols(content, "cpp")
    types = {s["type"]: s["name"] for s in symbols}
    assert "Engine" in [s["name"] for s in symbols if s["type"] == "class"] or \
           "patchquest" in [s["name"] for s in symbols if s["type"] == "namespace"]


def test_cpp_functions():
    content = _read_fixture("sample.cpp")
    symbols = extract_symbols(content, "cpp")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "process" in names or "main" in names


# --- Go ---

def test_go_functions():
    content = _read_fixture("sample.go")
    symbols = extract_symbols(content, "go")
    names = [s["name"] for s in symbols if s["type"] == "function"]
    assert "NewConfig" in names or "main" in names


def test_go_structs():
    content = _read_fixture("sample.go")
    symbols = extract_symbols(content, "go")
    structs = [s["name"] for s in symbols if s["type"] == "struct"]
    assert "Config" in structs


def test_go_interfaces():
    content = _read_fixture("sample.go")
    symbols = extract_symbols(content, "go")
    interfaces = [s["name"] for s in symbols if s["type"] == "interface"]
    assert "Handler" in interfaces


def test_go_methods():
    content = _read_fixture("sample.go")
    symbols = extract_symbols(content, "go")
    methods = [s["name"] for s in symbols if s["type"] == "method"]
    assert "GetName" in methods


# --- Java ---

def test_java_classes():
    content = _read_fixture("sample.java")
    symbols = extract_symbols(content, "java")
    classes = [s["name"] for s in symbols if s["type"] == "class"]
    assert "UserService" in classes


def test_java_interfaces():
    content = _read_fixture("sample.java")
    symbols = extract_symbols(content, "java")
    interfaces = [s["name"] for s in symbols if s["type"] == "interface"]
    assert "Repository" in interfaces


def test_java_methods():
    content = _read_fixture("sample.java")
    symbols = extract_symbols(content, "java")
    methods = [s["name"] for s in symbols if s["type"] == "method"]
    assert "createUser" in methods or "getUser" in methods


def test_java_imports():
    content = _read_fixture("sample.java")
    symbols = extract_symbols(content, "java")
    imports = [s["name"] for s in symbols if s["type"] == "import"]
    assert len(imports) >= 1


# --- Fallback ---

def test_unsupported_language_returns_empty():
    symbols = extract_symbols("some content", "brainfuck")
    assert symbols == []


def test_empty_content():
    symbols = extract_symbols("", "python")
    assert symbols == []
