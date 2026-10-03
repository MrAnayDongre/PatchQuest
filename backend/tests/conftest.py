"""Global test configuration: one isolation fixture for every test.

Every test gets a fresh SQLite database, default configuration, an isolated workspace directory and
clean process-wide registries, so tests cannot leak state into each other. Layer-specific fixtures live
in ``tests/<layer>/conftest.py``; reusable helpers live in ``tests/support``.
"""

import os
import uuid

import pytest

from patchquest import database
from patchquest.agents import providers_openai_compatible as _poc
from patchquest.agents import roles as _roles
from patchquest.agents.providers_scripted import ScriptedProvider
from patchquest.config import AppConfig, set_config
from patchquest.database import init_db, set_db_path
from patchquest.plugins import PluginHost, set_host
from patchquest.providers import health as _health
from patchquest.runtime import retry as _retry
from patchquest.workflows import runtime as _wf_runtime

PG_DSN = os.environ.get("PATCHQUEST_TEST_PG")  # e.g. postgresql://user@127.0.0.1:5432/db: run the suite on PostgreSQL


def pytest_collection_modifyitems(config, items):
    if PG_DSN:
        skip = pytest.mark.skip(reason="exercises SQLite files directly (backup, restore, file permissions) or spawns SQLite-only processes")
        for item in items:
            if item.get_closest_marker("sqlite_only"):
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path_factory, tmp_path, monkeypatch):
    # The database lives outside tmp_path: many tests use tmp_path itself as the repository under test.
    set_db_path(tmp_path_factory.mktemp("state") / "patchquest.db")
    schema = None
    if PG_DSN:  # every test gets its own schema in the shared PostgreSQL database
        import psycopg

        schema = "t_" + uuid.uuid4().hex[:12]
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {schema}")
        database.use_postgres(PG_DSN, schema)
    else:
        database.use_sqlite()
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
    if schema:
        import psycopg

        database.use_sqlite()  # closes the pool
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
