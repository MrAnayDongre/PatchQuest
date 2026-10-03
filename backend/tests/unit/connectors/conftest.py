"""Shared fixtures for connector tests. The connector migration is applied locally (not yet registered)."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from patchquest.connectors.migration import apply_fn
from patchquest.database import get_db

PUBLIC = "93.184.216.34"
SECRET_VALUE = "ghp_" + "A1b2C3d4" * 5  # sentinel that must never appear in any output


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "mocked_protocol: runs only against a simulator; never verified live")


@pytest.fixture(autouse=True)
def connector_tables(isolated_runtime):
    with get_db() as conn:
        apply_fn(conn)


def resolver(mapping: dict[str, list[str]] | None = None, default: str = PUBLIC):
    table = mapping or {}

    def resolve(host, port, **_kw):
        out = []
        for ip in table.get(host, [default]):
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            out.append((family, socket.SOCK_STREAM, 6, "", (ip, port)))
        return out

    return resolve


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
