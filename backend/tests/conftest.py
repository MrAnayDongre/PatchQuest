"""Global test configuration: one isolation fixture for every test.

Every test gets a fresh SQLite database, default configuration, an isolated workspace directory and
clean process-wide registries, so tests cannot leak state into each other. Layer-specific fixtures live
in ``tests/<layer>/conftest.py``; reusable helpers live in ``tests/support``.
"""

import pytest

from patchquest.agents import providers_openai_compatible as _poc
from patchquest.agents import roles as _roles
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.config import AppConfig, set_config
from patchquest.database import init_db, set_db_path
from patchquest.plugins import PluginHost, set_host
from patchquest.providers import health as _health
from patchquest.runtime import retry as _retry
from patchquest.workflows import runtime as _wf_runtime


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path_factory, tmp_path, monkeypatch):
    # The database lives outside tmp_path: many tests use tmp_path itself as the repository under test.
    set_db_path(tmp_path_factory.mktemp("state") / "patchquest.db")
    init_db()
    set_config(AppConfig())
    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path_factory.mktemp("workspaces"))
    monkeypatch.setattr(_retry, "DEFAULT_POLICY", _retry.RetryPolicy(max_attempts=3, base_delay_s=0, max_delay_s=0))
    for var in ("PATCHQUEST_API_TOKEN", "PATCHQUEST_DB", "PATCHQUEST_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    _health.reset()
    set_host(PluginHost(tmp_path_factory.mktemp("plugins"), scan_entry_points=False))
    _wf_runtime.set_engine(None)
    _poc._UNSUPPORTED.clear()
    _roles._CONSTRAINED_UNRELIABLE.clear()
    ScriptedProvider.scripts.clear()
    yield
    set_config(AppConfig())
