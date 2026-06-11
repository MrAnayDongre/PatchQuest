"""Tests for path traversal protection and workspace safety."""

import os
import tempfile

from patchquest.paths import check_path_traversal, is_inside_repo, is_path_safe
from patchquest.tools.file_tools import create_file, list_files, read_file
from patchquest.tools.safety import validate_workspace_access


class TestPathTraversal:
    def test_rejects_dotdot_traversal(self):
        assert check_path_traversal("../../../etc/passwd") is False
        assert check_path_traversal("foo/../../bar/../../etc/shadow") is False

    def test_accepts_normal_paths(self):
        assert check_path_traversal("src/main.py") is True
        assert check_path_traversal("tests/test_foo.py") is True
        assert check_path_traversal("deeply/nested/path/file.ts") is True

    def test_rejects_hidden_traversal(self):
        assert check_path_traversal("src/../../../etc/passwd") is False


class TestIsInsideRepo:
    def test_inside_repo(self):
        repo = tempfile.mkdtemp()
        assert is_inside_repo(os.path.join(repo, "src/main.py"), repo) is True

    def test_outside_repo(self):
        repo = tempfile.mkdtemp()
        assert is_inside_repo("/etc/passwd", repo) is False
        assert is_inside_repo(os.path.expanduser("~/.ssh/id_rsa"), repo) is False


class TestPathSafe:
    def test_forbidden_paths_blocked(self):
        repo = tempfile.mkdtemp()
        ssh_path = os.path.expanduser("~/.ssh/id_rsa")
        assert is_path_safe(ssh_path, repo) is False

        aws_path = os.path.expanduser("~/.aws/credentials")
        assert is_path_safe(aws_path, repo) is False

    def test_repo_paths_allowed(self):
        repo = tempfile.mkdtemp()
        test_file = os.path.join(repo, "src/main.py")
        os.makedirs(os.path.dirname(test_file), exist_ok=True)
        open(test_file, "w").close()
        assert is_path_safe(test_file, repo) is True


class TestFileToolsSafety:
    def test_read_file_rejects_traversal(self):
        repo = tempfile.mkdtemp()
        result = read_file("../../../etc/passwd", repo)
        assert result["error"] is not None
        assert "traversal" in result["error"].lower()

    def test_create_file_rejects_traversal(self):
        repo = tempfile.mkdtemp()
        result = create_file("../../evil.py", "malicious", repo)
        assert result["success"] is False
        assert "traversal" in result["error"].lower()

    def test_create_file_blocks_secrets(self):
        repo = tempfile.mkdtemp()
        content = 'API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678"'
        result = create_file("config.py", content, repo)
        assert result["success"] is False
        assert "secret" in result["error"].lower()

    def test_list_files_rejects_traversal(self):
        repo = tempfile.mkdtemp()
        result = list_files("../../..", repo)
        assert result["error"] is not None


class TestWorkspaceAccess:
    def test_env_files_blocked(self):
        repo = tempfile.mkdtemp()
        ok, msg = validate_workspace_access(".env", repo)
        assert ok is False
        assert ".env" in msg

    def test_ssh_blocked(self):
        repo = tempfile.mkdtemp()
        ok, msg = validate_workspace_access(os.path.expanduser("~/.ssh/id_rsa"), repo)
        assert ok is False

    def test_repo_file_allowed(self):
        repo = tempfile.mkdtemp()
        test_path = os.path.join(repo, "src/app.py")
        os.makedirs(os.path.dirname(test_path), exist_ok=True)
        open(test_path, "w").close()
        ok, _ = validate_workspace_access(test_path, repo)
        assert ok is True
