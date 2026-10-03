import subprocess
from pathlib import Path

from patchquest.runtime.fingerprint import Drift, RepoFingerprint, classify, compute


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("a = 1\n")
    (repo / "b.py").write_text("b = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def test_unchanged_repository_has_no_drift(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py"])
    assert old.is_git and old.head and classify(old, compute(str(repo), ["a.py"]), ["a.py"]).kind is Drift.NO_DRIFT


def test_editing_a_file_the_run_writes_is_conflicting(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py", "b.py"])
    (repo / "a.py").write_text("a = 2  # human\n")
    report = classify(old, compute(str(repo), ["a.py", "b.py"]), touched=["a.py"])
    assert report.kind is Drift.CONFLICTING_DRIFT and report.conflicting == ("a.py",)


def test_editing_only_an_unrelated_file_is_safe(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py"])
    (repo / "b.py").write_text("b = 2\n")
    report = classify(old, compute(str(repo), ["a.py"]), touched=["a.py"])
    assert report.kind is Drift.SAFE_DRIFT and "working tree" in report.reasons[0]


def test_new_commit_without_touching_our_files_is_safe(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py"])
    (repo / "c.py").write_text("c = 1\n")
    _git(repo, "add", "c.py")
    _git(repo, "commit", "-qm", "more")
    report = classify(old, compute(str(repo), ["a.py"]), touched=["a.py"])
    assert report.kind is Drift.SAFE_DRIFT and "HEAD" in report.reasons[0]


def test_changed_input_file_is_reported_as_stale_context_but_safe(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py", "b.py"])
    (repo / "b.py").write_text("b = 99\n")
    report = classify(old, compute(str(repo), ["a.py", "b.py"]), touched=["a.py"])
    assert report.kind is Drift.SAFE_DRIFT and report.stale_context == ("b.py",)


def test_deleting_a_touched_file_is_conflicting(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py"])
    (repo / "a.py").unlink()
    assert classify(old, compute(str(repo), ["a.py"]), ["a.py"]).kind is Drift.CONFLICTING_DRIFT


def test_non_git_directory_compares_files_only(tmp_path):
    (tmp_path / "x.py").write_text("1")
    old = compute(str(tmp_path), ["x.py"])
    assert not old.is_git
    assert classify(old, compute(str(tmp_path), ["x.py"]), ["x.py"]).kind is Drift.NO_DRIFT
    (tmp_path / "x.py").write_text("2")
    assert classify(old, compute(str(tmp_path), ["x.py"]), ["x.py"]).kind is Drift.CONFLICTING_DRIFT


def test_nothing_recorded_is_unknown_not_assumed_safe(tmp_path):
    assert classify(RepoFingerprint(), compute(str(tmp_path)), []).kind is Drift.UNKNOWN_DRIFT


def test_git_to_non_git_is_unknown(tmp_path):
    repo = _repo(tmp_path)
    old = compute(str(repo), ["a.py"])
    import shutil

    shutil.rmtree(repo / ".git")
    assert classify(old, compute(str(repo), ["a.py"]), []).kind is Drift.UNKNOWN_DRIFT


def test_roundtrip_through_json():
    fp = RepoFingerprint(head="h", branch="b", status_hash="s", is_git=True, files={"a": "x", "b": None})
    assert RepoFingerprint.from_dict(fp.to_dict()) == fp


def test_hostile_repo_config_cannot_execute_during_fingerprinting(tmp_path):
    """A repository's own .git/config must not get to run commands when PatchQuest reads its status."""
    repo = _repo(tmp_path)
    marker = tmp_path / "pwned"
    hook = tmp_path / "fsmon.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    hooks = repo / "hooks"
    hooks.mkdir()
    _git(repo, "config", "core.hooksPath", str(hooks))
    fp = compute(str(repo), ["a.py"])
    assert fp.is_git and not marker.exists()


def test_watch_paths_cannot_escape_the_repo(tmp_path):
    repo = _repo(tmp_path)
    (tmp_path / "secret.txt").write_text("s")
    assert compute(str(repo), ["../secret.txt"]).files == {"../secret.txt": None}


async def test_a_read_only_run_in_an_uncomparable_repository_still_resumes_automatically(tmp_path):
    """Found by killing a worker container mid-batch: a read-only run was left 'interrupted' because a plain folder had nothing to compare."""
    from patchquest.runtime.resume import RecoveryCategory, plan_resume
    from tests.support import crash_run, fetch_events

    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    run_id = await crash_run(tmp_path, {}, lambda e: e["type"] == "checkpoint_created", task="read only: explain the repo")
    assert fetch_events(run_id)
    plan = plan_resume(run_id)
    assert plan.category is RecoveryCategory.SAFE_RESUME and any("read-only run" in r for r in plan.reasons)
