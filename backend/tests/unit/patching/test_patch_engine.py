"""Patch engine, its legacy wrappers, and test-failure attribution.

Invariants: a real `git diff` applies exactly; stale or mismatched diffs are refused rather than guessed; edits are
atomic with rollback and sha256 preconditions; secrets are blocked only when the patch *adds* them; symlink and .git
writes are blocked; failures are attributed to the patch or to the baseline per test id.
"""

import os
import subprocess
import tempfile

import pytest

from patchquest.orchestrator.state_machine import _tool_available
from patchquest.patching import (
    CreateFile,
    DeleteFile,
    PatchApplyError,
    SearchReplace,
    apply_changes,
    changes_from_model_output,
    changes_from_unified_diff,
    rollback,
    sha256_bytes,
)
from patchquest.paths import UnsafePathError, is_inside_repo, resolve_in_repo
from patchquest.tools.patch_tools import apply_unified_diff, replace_range
from patchquest.validation import classify_failures, extract_failed_tests

# ======================================================================
# Engine
# ======================================================================

SRC = "def f():\n    x = 1\n    y = 2\n    return x + y\n\nprint(f())\n"


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "a.py").write_text(SRC)
    return tmp_path


def git_diff(repo, new_text, name="a.py"):
    """A real `git diff` (3 lines of context) — the format models are trained on."""
    old = (repo / name).read_text()
    tmp = repo / "__new__"
    tmp.write_text(new_text)
    out = subprocess.run(
        ["git", "diff", "--no-index", "--", str(repo / name), str(tmp)],
        capture_output=True, text=True,
    ).stdout
    tmp.unlink()
    assert old != new_text
    out = out.replace(str(repo / name).lstrip("/"), name).replace(str(tmp).lstrip("/"), name)
    return out


class TestUnifiedDiff:
    def test_real_git_diff_with_context_applies_exactly(self, repo):
        """Regression: the old applier ignored context and deleted `def f():`."""
        new = SRC.replace("y = 2", "y = 20")
        res = apply_unified_diff(git_diff(repo, new), str(repo))
        assert res["success"], res
        assert (repo / "a.py").read_text() == new

    def test_stale_diff_is_rejected_not_guessed(self, repo):
        diff = git_diff(repo, SRC.replace("y = 2", "y = 20"))
        (repo / "a.py").write_text("completely\ndifferent\ncontent\n")
        res = apply_unified_diff(diff, str(repo))
        assert res["success"] is False
        assert (repo / "a.py").read_text() == "completely\ndifferent\ncontent\n"

    def test_wrong_line_numbers_are_relocated_by_context(self, repo):
        diff = (
            "--- a/a.py\n+++ b/a.py\n@@ -40,3 +40,3 @@\n"
            "     x = 1\n-    y = 2\n+    y = 7\n     return x + y\n"
        )
        res = apply_unified_diff(diff, str(repo))
        assert res["success"], res
        assert "y = 7" in (repo / "a.py").read_text()

    def test_multiple_hunks_track_offsets(self, tmp_path):
        body = "".join(f"line{i}\n" for i in range(1, 31))
        (tmp_path / "f.txt").write_text(body)
        new = body.replace("line3\n", "line3\nINSERTED-A\nINSERTED-B\n").replace("line25\n", "CHANGED\n")
        res = apply_unified_diff(git_diff(tmp_path, new, "f.txt"), str(tmp_path))
        assert res["success"], res
        assert (tmp_path / "f.txt").read_text() == new

    def test_new_file_and_delete_file(self, repo):
        diff = "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
        assert apply_unified_diff(diff, str(repo))["success"]
        assert (repo / "new.txt").read_text() == "hello\nworld\n"
        diff = "--- a/new.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-hello\n-world\n"
        assert apply_unified_diff(diff, str(repo))["success"]
        assert not (repo / "new.txt").exists()

    def test_blank_context_lines_without_leading_space(self, repo):
        diff = (
            "--- a/a.py\n+++ b/a.py\n@@ -3,4 +3,4 @@\n"
            "     y = 2\n-    return x + y\n+    return x * y\n\n print(f())\n"
        )
        res = apply_unified_diff(diff, str(repo))
        assert res["success"], res
        assert "return x * y" in (repo / "a.py").read_text()

    def test_crlf_files_keep_crlf(self, tmp_path):
        (tmp_path / "w.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
        diff = "--- a/w.txt\n+++ b/w.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
        assert apply_unified_diff(diff, str(tmp_path))["success"]
        assert (tmp_path / "w.txt").read_bytes() == b"one\r\nTWO\r\nthree\r\n"

    def test_removing_an_existing_secret_is_allowed(self, tmp_path):
        key = "sk-abc123def456ghi789jkl012mno345pqr678"
        (tmp_path / "c.py").write_text(f'KEY = "{key}"\nx = 1\n')
        diff = f'--- a/c.py\n+++ b/c.py\n@@ -1,2 +1,2 @@\n-KEY = "{key}"\n+KEY = None\n x = 1\n'
        assert apply_unified_diff(diff, str(tmp_path))["success"]

    def test_introducing_a_secret_is_blocked(self, repo):
        diff = '--- a/a.py\n+++ b/a.py\n@@ -2,1 +2,2 @@\n     x = 1\n+K = "sk-abc123def456ghi789jkl012mno345pqr678"\n'
        res = apply_unified_diff(diff, str(repo))
        assert res["success"] is False and "SecretGuard" in res["error"]
        assert (repo / "a.py").read_text() == SRC


class TestSearchReplace:
    def test_unique_match_applies(self, repo):
        res = apply_changes(str(repo), [SearchReplace("a.py", "y = 2", "y = 3")])
        assert res.success and "y = 3" in (repo / "a.py").read_text()

    def test_ambiguous_match_is_rejected(self, tmp_path):
        (tmp_path / "d.py").write_text("a = 1\na = 1\n")
        res = apply_changes(str(tmp_path), [SearchReplace("d.py", "a = 1", "a = 2")])
        assert not res.success and "2 places" in res.error
        assert (tmp_path / "d.py").read_text() == "a = 1\na = 1\n"

    def test_replace_all(self, tmp_path):
        (tmp_path / "d.py").write_text("a = 1\na = 1\n")
        res = apply_changes(str(tmp_path), [SearchReplace("d.py", "a = 1", "a = 2", replace_all=True)])
        assert res.success and (tmp_path / "d.py").read_text() == "a = 2\na = 2\n"

    def test_missing_snippet_is_rejected(self, repo):
        res = apply_changes(str(repo), [SearchReplace("a.py", "nonexistent()", "x")])
        assert not res.success and "not found" in res.error

    def test_trailing_whitespace_tolerated(self, tmp_path):
        (tmp_path / "t.py").write_text("x = 1   \ny = 2\n")
        res = apply_changes(str(tmp_path), [SearchReplace("t.py", "x = 1\ny = 2", "x = 9\ny = 9")])
        assert res.success, res.errors
        assert (tmp_path / "t.py").read_text().startswith("x = 9\ny = 9")

    def test_model_output_parsing(self):
        changes = changes_from_model_output({
            "edits": [{"path": "a.py", "search": "s", "replace": "r"}],
            "create": [{"path": "n.py", "content": "c"}], "delete": ["old.py"],
        })
        assert [type(c) for c in changes] == [SearchReplace, CreateFile, DeleteFile]


class TestAtomicity:
    def test_second_file_failure_leaves_first_untouched(self, repo):
        (repo / "b.py").write_text("keep\n")
        res = apply_changes(str(repo), [
            SearchReplace("a.py", "y = 2", "y = 3"),
            SearchReplace("b.py", "does-not-exist", "x"),
        ])
        assert not res.success
        assert (repo / "a.py").read_text() == SRC

    def test_rollback_restores_snapshot_including_created_files(self, repo):
        res = apply_changes(str(repo), [
            SearchReplace("a.py", "y = 2", "y = 3"), CreateFile("sub/new.py", "x = 1\n"),
        ])
        assert res.success and (repo / "sub/new.py").exists()
        rollback(str(repo), res.snapshot)
        assert (repo / "a.py").read_text() == SRC and not (repo / "sub/new.py").exists()

    def test_dry_run_writes_nothing_and_returns_diff(self, repo):
        res = apply_changes(str(repo), [SearchReplace("a.py", "y = 2", "y = 3")], dry_run=True)
        assert res.success and "+    y = 3" in res.diff
        assert (repo / "a.py").read_text() == SRC

    def test_hash_precondition_detects_concurrent_edit(self, repo):
        planned = sha256_bytes((repo / "a.py").read_bytes())
        (repo / "a.py").write_text(SRC + "# someone else edited this\n")
        res = apply_changes(str(repo), [SearchReplace("a.py", "y = 2", "y = 3")],
                            expected_hashes={"a.py": planned})
        assert not res.success and "precondition" in res.error

    def test_binary_files_refused(self, tmp_path):
        (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02binary")
        res = apply_changes(str(tmp_path), [SearchReplace("b.bin", "x", "y")])
        assert not res.success and "binary" in res.error

    def test_preserves_file_mode(self, repo):
        os.chmod(repo / "a.py", 0o755)
        assert apply_changes(str(repo), [SearchReplace("a.py", "y = 2", "y = 3")]).success
        assert (repo / "a.py").stat().st_mode & 0o777 == 0o755


class TestPathSafety:
    def test_prefix_sibling_is_not_inside_repo(self, tmp_path):
        repo, evil = tmp_path / "repo", tmp_path / "repo-evil"
        repo.mkdir(), evil.mkdir()
        assert is_inside_repo(str(evil / "x"), str(repo)) is False

    @pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/../../x", "", "~/x", ".git/config", ".git/hooks/pre-commit"])
    def test_rejected_paths(self, repo, bad):
        with pytest.raises(UnsafePathError):
            resolve_in_repo(str(repo), bad, for_write=True)

    def test_symlink_escape_blocked(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "link").symlink_to(outside)
        res = apply_changes(str(repo), [CreateFile("link/pwned.txt", "x")])
        assert not res.success and not (outside / "pwned.txt").exists()

    def test_symlinked_file_pointing_outside_blocked(self, tmp_path):
        target = tmp_path / "secret.txt"
        target.write_text("top secret\n")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "s.txt").symlink_to(target)
        res = apply_changes(str(repo), [SearchReplace("s.txt", "top", "TOP")])
        assert not res.success and target.read_text() == "top secret\n"

    def test_diff_cannot_write_git_hooks(self, repo):
        diff = "--- /dev/null\n+++ b/.git/hooks/pre-commit\n@@ -0,0 +1 @@\n+#!/bin/sh\n"
        assert apply_unified_diff(diff, str(repo))["success"] is False


def test_changes_from_unified_diff_rejects_garbage():
    with pytest.raises(PatchApplyError):
        changes_from_unified_diff("this is not a diff")


# ======================================================================
# Legacy result-dict wrappers
# ======================================================================

class TestApplyUnifiedDiff:
    def test_blocks_diff_with_secrets(self):
        diff = """\
--- a/config.py
+++ b/config.py
@@ -1,2 +1,3 @@
 import os
+API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678"
 x = 1
"""
        repo = tempfile.mkdtemp()
        result = apply_unified_diff(diff, repo)
        assert result["success"] is False
        assert "SecretGuard" in result["error"]

    def test_rejects_path_traversal_in_diff(self):
        diff = """\
--- a/../../../etc/passwd
+++ b/../../../etc/passwd
@@ -1,1 +1,2 @@
 root:x:0:0
+hacked:x:0:0
"""
        repo = tempfile.mkdtemp()
        result = apply_unified_diff(diff, repo)
        assert result["success"] is False

    def test_applies_valid_diff(self):
        repo = tempfile.mkdtemp()
        filepath = os.path.join(repo, "hello.py")
        with open(filepath, "w") as f:
            f.write("def hello():\n    return 'world'\n")

        diff = """\
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,3 @@
 def hello():
-    return 'world'
+    \"\"\"Say hello.\"\"\"
+    return 'hello world'
"""
        result = apply_unified_diff(diff, repo)
        assert result["success"] is True
        assert "hello.py" in result["files_changed"]

    def test_empty_diff_fails(self):
        repo = tempfile.mkdtemp()
        result = apply_unified_diff("", repo)
        assert result["success"] is False


class TestReplaceRange:
    def test_blocks_secret_in_replacement(self):
        repo = tempfile.mkdtemp()
        filepath = os.path.join(repo, "app.py")
        with open(filepath, "w") as f:
            f.write("line1\nline2\nline3\n")

        result = replace_range(
            "app.py", 2, 2,
            'key = "sk-abc123def456ghi789jkl012mno345pqr678"',
            repo,
        )
        assert result["success"] is False
        assert "secret" in result["error"].lower()

    def test_rejects_path_traversal(self):
        repo = tempfile.mkdtemp()
        result = replace_range("../../etc/hosts", 1, 1, "hacked", repo)
        assert result["success"] is False
        assert "traversal" in result["error"].lower()

    def test_valid_replacement(self):
        repo = tempfile.mkdtemp()
        filepath = os.path.join(repo, "app.py")
        with open(filepath, "w") as f:
            f.write("line1\nline2\nline3\n")

        result = replace_range("app.py", 2, 2, "new_line2", repo)
        assert result["success"] is True

        with open(filepath) as f:
            content = f.read()
        assert "new_line2" in content
        assert "line1" in content
        assert "line3" in content


# ======================================================================
# Failure attribution and test detection
# ======================================================================

def result_validation(cmd, out="", ok=False, code=1):
    """A command-result dict as produced by the runner."""
    return {"command": cmd, "success": ok, "returncode": code, "stdout": out, "stderr": ""}


@pytest.mark.parametrize("output, expected", [
    ("FAILED tests/test_a.py::test_x - assert 1 == 2\nERROR tests/test_b.py::test_y", {"tests/test_a.py::test_x", "tests/test_b.py::test_y"}),
    ("FAIL: test_add (test_calc.T.test_add)\nERROR: test_z (mod.C)", {"test_add.test_calc.T.test_add", "test_z.mod.C"}),
    ("--- FAIL: TestFoo (0.00s)\ntest a::b ... FAILED", {"TestFoo", "a::b"}),
    ("  ✕ renders the header (3 ms)\n  × other", {"renders the header", "other"}),
    ("all good", set()),
    ("", set()),
])
def test_extract_failed_tests(output, expected):
    assert extract_failed_tests(output) == expected


def test_new_failure_in_an_already_red_suite_is_not_masked():
    cls = classify_failures(
        [result_validation("pytest", "FAILED t.py::old\nFAILED t.py::new")],
        [result_validation("pytest", "FAILED t.py::old")],
    )
    assert cls["pytest"] == {"new": ["t.py::new"], "preexisting": ["t.py::old"]}


def test_same_failures_are_preexisting():
    cls = classify_failures([result_validation("pytest", "FAILED t.py::a")], [result_validation("pytest", "FAILED t.py::a")])
    assert cls["pytest"]["new"] == []


def test_unparseable_failure_falls_back_to_exit_code_identity():
    cls = classify_failures([result_validation("make", "boom", code=2)], [result_validation("make", "bang", code=2)])
    assert cls["make"]["new"] == [] and cls["make"]["preexisting"] == ["<exit 2>"]
    cls = classify_failures([result_validation("make", "boom", code=2)], [result_validation("make", "ok", ok=True, code=0)])
    assert cls["make"]["new"] == ["<exit 2>"]


def test_passing_commands_are_ignored():
    assert classify_failures([result_validation("pytest", ok=True, code=0)], []) == {}


def test_missing_tools_are_not_treated_as_test_failures():
    assert _tool_available("python3 -m unittest") is True
    assert _tool_available("definitely-not-installed --flag") is False
    assert _tool_available("unterminated 'quote") is False


def test_plain_test_layouts_are_detected(tmp_path):
    from patchquest.tools.test_runner import detect_test_commands

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    cmds = detect_test_commands(str(tmp_path))
    assert len(cmds) == 1 and ("pytest" in cmds[0] or "unittest discover" in cmds[0])
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert detect_test_commands(str(empty)) == []
