"""Tests for patch application tools with safety checks."""

import os
import tempfile

from patchquest.tools.patch_tools import apply_unified_diff, replace_range


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
