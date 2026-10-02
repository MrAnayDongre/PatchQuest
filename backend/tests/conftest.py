"""Shared test fixtures."""


import pytest

from patchquest.config import AppConfig, set_config


@pytest.fixture(autouse=True)
def isolate_config():
    """Ensure tests use default config, not a local config.yaml."""
    set_config(AppConfig())
    yield
