"""TaskService: the single application entry point for create/run/stream/inspect.

Invariants: a run persists everything it did; bad repo paths are rejected before anything is stored; a client that
connects late (or reconnects with after_id) gets the complete history and the stream ends; live and replayed events
merge without gaps or duplicates.
"""

import asyncio

import pytest

from patchquest.application.service import RunNotActive, RunNotFound, TaskService
from patchquest.security import RepoPathError


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
