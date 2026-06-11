"""Shared test fixtures."""

import tempfile
from pathlib import Path

import pytest

from patchquest.config import AppConfig, set_config
from patchquest.database import init_db, set_db_path


@pytest.fixture(autouse=True)
def isolate_config():
    """Ensure tests use default config, not a local config.yaml."""
    set_config(AppConfig())
    yield
