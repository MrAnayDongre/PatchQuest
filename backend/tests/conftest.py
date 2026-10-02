"""Shared test fixtures."""

import tempfile
from pathlib import Path

import pytest

from patchquest.config import AppConfig, set_config


@pytest.fixture(autouse=True)
def isolate_config():
    """Ensure tests use default config, not a local config.yaml."""
    set_config(AppConfig())
    yield


def _make_test_repo() -> str:
    """A tiny private repository. Tests must never index real host directories like /tmp."""
    root = Path(tempfile.mkdtemp(prefix="pq-test-repo-"))
    (root / "README.md").write_text("# Test Repo\n\nA tiny repository for pipeline tests.\n")
    (root / "app.py").write_text("def main():\n    return 0\n")
    return str(root)


TEST_REPO = _make_test_repo()
