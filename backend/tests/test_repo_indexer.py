"""Tests for repo indexer and symbol extraction."""

import os
import tempfile
from pathlib import Path

from patchquest.database import get_db, init_db, set_db_path
from patchquest.memory.repo_indexer import IGNORED_DIRS, index_repo
from patchquest.memory.symbol_extractors import extract_symbols


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
