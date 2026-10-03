"""Two organisations, each with a workspace, people in several roles, and a run in each."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

from patchquest.application import TaskService
from patchquest.database import get_db
from patchquest.domain.identity import Role
from patchquest.persistence import identity as ids
from patchquest.persistence import tenancy


@dataclass
class World:
    ws: dict[str, str] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)
    token_ids: dict[str, str] = field(default_factory=dict)
    runs: dict[str, str] = field(default_factory=dict)
    repos: dict[str, str] = field(default_factory=dict)

    def client(self, who: str | None = None, **headers) -> httpx.AsyncClient:
        from patchquest.main import app

        if who:
            headers["Authorization"] = f"Bearer {self.tokens[who]}"
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers=headers)


@pytest.fixture
def world(tmp_path) -> World:
    w = World()
    repos = {}
    for name in ("a", "b"):  # a path belongs to one workspace, so each tenant has its own repository
        repos[name] = tmp_path / f"repo-{name}"
        repos[name].mkdir()
        (repos[name] / "a.py").write_text("x = 1\n")
    with get_db() as conn:
        for org_name, ws_name in (("Acme", "a"), ("Globex", "b")):
            org = ids.create_org(conn, org_name)
            w.ws[ws_name] = ids.create_workspace(conn, org, ws_name)
            for who, role, kind in ((f"owner_{ws_name}", Role.OWNER, "user"), (f"admin_{ws_name}", Role.ADMIN, "user"),
                                    (f"dev_{ws_name}", Role.DEVELOPER, "user"), (f"ops_{ws_name}", Role.OPERATOR, "user"),
                                    (f"viewer_{ws_name}", Role.VIEWER, "user"), (f"ci_{ws_name}", Role.SERVICE, "service")):
                pid = ids.create_principal(conn, org, who, kind)
                ids.set_role(conn, pid, w.ws[ws_name], role)
                w.token_ids[who], w.tokens[who] = ids.issue_token(conn, pid, who)
    svc = TaskService()
    for name in ("a", "b"):
        with get_db() as conn:
            tenancy.register_repository(conn, w.ws[name], str(repos[name]), None, None, "test")
        w.repos[name] = str(repos[name])
        run = svc.create_run(repo_path=str(repos[name]), task=f"task of {name}", workspace_id=w.ws[name], created_by=f"user:{name}")
        w.runs[name] = run["id"]
    return w
