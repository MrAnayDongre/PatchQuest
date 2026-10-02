"""Application service: one code path for create/run/stream/inspect."""

import asyncio

import pytest

from patchquest.application.service import RunNotActive, RunNotFound, TaskService
from patchquest.config import AppConfig, set_config
from patchquest.database import init_db, set_db_path
from patchquest.security import RepoPathError


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr("patchquest.runtime.workspace.WORKSPACE_BASE", tmp_path / "ws")
    set_db_path(tmp_path / "svc.db")
    init_db()
    set_config(AppConfig())
    yield


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "README.md").write_text("# proj\n")
    return root


READ_ONLY = "Summarize this repo. Do not modify files."


@pytest.mark.asyncio
async def test_run_to_completion_persists_everything(repo):
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task=READ_ONLY)
    assert run["status"] == "created"
    done = await svc.run_to_completion(run["id"])
    assert done["status"] == "completed" and done["outcome"] == "read_only"
    types = [e["type"] for e in svc.events(run["id"])]
    assert types[0] == "run_created" and types[-1] == "run_completed"
    assert svc.report(run["id"]) is not None and svc.diff(run["id"]) == ""
    assert not svc.is_active(run["id"])


def test_create_run_validates_path(tmp_path):
    with pytest.raises(RepoPathError):
        TaskService().create_run(repo_path="/etc", task="x")


def test_unknown_run_and_inactive_cancel():
    svc = TaskService()
    with pytest.raises(RunNotFound):
        svc.get_run("nope")
    with pytest.raises(RunNotActive):
        svc.cancel("nope")


@pytest.mark.asyncio
async def test_stream_after_completion_replays_history_and_terminates(repo):
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task=READ_ONLY)
    await svc.run_to_completion(run["id"])
    got = [e async for e in svc.stream(run["id"])]  # must not hang
    assert [e["type"] for e in got] == [e["type"] for e in svc.events(run["id"])]
    assert got[-1]["type"] == "run_completed"


@pytest.mark.asyncio
async def test_stream_resumes_from_last_event_id(repo):
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task=READ_ONLY)
    await svc.run_to_completion(run["id"])
    events = svc.events(run["id"])
    cut = events[3]["id"]
    got = [e async for e in svc.stream(run["id"], after_id=cut)]
    assert [e["id"] for e in got] == [e["id"] for e in events if e["id"] > cut]


@pytest.mark.asyncio
async def test_live_stream_has_no_duplicates_or_gaps(repo):
    svc = TaskService()
    run = svc.create_run(repo_path=str(repo), task=READ_ONLY)
    svc.launch(run["id"])  # run is in flight while we subscribe
    got = await asyncio.wait_for(_collect(svc, run["id"]), timeout=20)
    ids = [e["id"] for e in got]
    assert ids == sorted(set(ids))  # strictly increasing, no duplicates
    assert [e["id"] for e in svc.events(run["id"])] == ids  # and nothing missing


async def _collect(svc, run_id):
    return [e async for e in svc.stream(run_id) if e["type"] != "ping"]
