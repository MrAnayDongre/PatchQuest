"""Plugin lifecycle and containment: manifests, explicit grants, policy, failures, and a real subprocess."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from patchquest.database import get_db
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.identity import LOCAL_WORKSPACE_ID
from patchquest.domain.plugins import PluginError, parse_manifest
from patchquest.persistence import plugins as store
from patchquest.plugins import PluginHost, set_host
from patchquest.plugins.host import QUARANTINE_AFTER, PluginApprovalRequired, PluginDenied
from patchquest.runtime import policy as pol


def manifest(name="echo", **kw):
    base = {"name": name, "version": "1.0.0", "kind": "tool", "trust": "trusted",
            "capabilities": {"say": {"side_effect": "READ_ONLY"}, "post": {"side_effect": "EXTERNAL_WRITE", "idempotent": False}}}
    return {**base, **kw}


class Echo:
    def __init__(self, **kw):
        self.manifest = manifest(**kw)
        self.calls = []
        self.fail = None
        self.started = None
        self.stopped = False

    def initialize(self, config):
        self.started = config

    def health(self):
        return {"ok": True, "detail": "fine"}

    def invoke(self, capability, args):
        if self.fail:
            raise self.fail
        self.calls.append((capability, args))
        return {"echo": args, "cap": capability}

    def shutdown(self):
        self.stopped = True


def host_with(*plugins, directory=None):
    host = PluginHost(directory or Path("/nonexistent-plugins"), scan_entry_points=False,
                      factories={p.manifest["name"]: (lambda p=p: p) for p in plugins})
    set_host(host)
    return host


# ------------------------------------------------------------------ manifests
@pytest.mark.parametrize("bad,why", [
    ({"name": "Bad Name"}, "name"), ({"version": "one"}, "version"), ({"kind": "toaster"}, "toaster"),
    ({"capabilities": {}}, "capabilities"), ({"capabilities": {"x": {}}}, "side_effect"),
    ({"capabilities": {"x": {"side_effect": "SOMETIMES"}}}, "side_effect"), ({"permissions": ["root"]}, "permission"),
    ({"surprise": 1}, "unknown manifest"), ({"trust": "external_process"}, "command"), ({"command": ["x"]}, "command"),
    ({"runtime": "1.0"}, "runtime"), ({"config": {"k": {"secret": True}}}, "secrets.read"),
])
def test_bad_manifests_are_refused_with_a_reason(bad, why):
    with pytest.raises(PluginError, match=why):
        parse_manifest(manifest(**bad))


# ------------------------------------------------------------------ lifecycle
async def test_enable_requires_the_exact_declared_grant_and_initializes():
    p = Echo(permissions=["net.http"], config={"greeting": {"type": "string", "default": "hi"}})
    host = host_with(p)
    for wrong in ([], ["net.http", "fs.write"]):
        with pytest.raises(PluginError, match="grant must match"):
            host.enable("echo", grant=wrong, actor="user:a")
    host.enable("echo", grant=["net.http"], actor="user:a")
    assert p.started == {"greeting": "hi"}
    out = await host.invoke("echo", "say", {"x": 1})
    assert out == {"echo": {"x": 1}, "cap": "say"}
    with get_db() as conn:
        assert [e["type"] for e in reversed(store.events(conn, "echo"))] == ["enabled", "invoked"]
        assert store.events(conn, "echo")[0]["detail"] == {"arg_keys": ["x"]}  # argument values are never recorded


async def test_a_plugin_that_is_not_enabled_cannot_be_invoked_and_disable_stops_it():
    p = Echo()
    host = host_with(p)
    with pytest.raises(PluginError, match="not enabled"):
        await host.invoke("echo", "say", {})
    host.enable("echo", grant=[], actor="a")
    host.disable("echo", actor="a")
    assert p.stopped
    with pytest.raises(PluginError, match="not enabled"):
        await host.invoke("echo", "say", {})


async def test_unknown_capability_and_oversized_arguments_are_refused():
    host = host_with(Echo())
    host.enable("echo", grant=[], actor="a")
    with pytest.raises(PluginError, match="no capability"):
        await host.invoke("echo", "nope", {})
    with pytest.raises(PluginError, match="256 KB"):
        await host.invoke("echo", "say", {"blob": "x" * 300_000})


def test_settings_are_validated_and_secrets_must_be_references():
    p = Echo(permissions=["secrets.read"], config={"token": {"type": "string", "secret": True, "required": True},
                                                  "limit": {"type": "integer", "default": 3}})
    host = host_with(p)
    for bad, why in (({}, "required"), ({"token": "ghp_realvalue"}, "env:"), ({"token": "env:X", "limit": "3"}, "integer"),
                     ({"token": "env:X", "other": 1}, "unknown setting")):
        with pytest.raises(PluginError, match=why):
            host.enable("echo", grant=["secrets.read"], config=bad, actor="a")
    host.enable("echo", grant=["secrets.read"], config={"token": "env:ECHO_TOKEN"}, actor="a")
    with get_db() as conn:
        assert "ECHO_TOKEN" in str(store.get(conn, "echo")["config"]) and store.get(conn, "echo")["config"]["limit"] == 3


def test_an_incompatible_runtime_is_refused():
    host = host_with(Echo(runtime=">=99.0"))
    with pytest.raises(PluginError, match=r"99\.0"):
        host.enable("echo", grant=[], actor="a")


def test_a_broken_plugin_is_reported_not_raised():
    class Broken:
        @property
        def manifest(self):
            raise RuntimeError("boom")

    host = PluginHost(Path("/nonexistent"), scan_entry_points=False, factories={"broken": Broken, "ok": Echo})
    names = {d.name: d for d in host.discover()}
    assert names["broken"].error and "boom" in names["broken"].error and names["echo"].error is None


async def test_a_plugin_that_fails_to_initialize_is_not_enabled():
    class Fails(Echo):
        def initialize(self, config):
            raise RuntimeError("no database")

    host = host_with(Fails())
    with pytest.raises(PluginError, match="did not start"):
        host.enable("echo", grant=[], actor="a")
    with get_db() as conn:
        assert store.get(conn, "echo") is None


# ------------------------------------------------------------------ containment
async def test_failures_are_wrapped_counted_and_quarantine_the_plugin():
    p = Echo()
    host = host_with(p)
    host.enable("echo", grant=[], actor="a")
    p.fail = RuntimeError("secret sk-ant-api03-" + "a" * 40 + " leaked")
    for _ in range(QUARANTINE_AFTER):
        with pytest.raises(PatchQuestError) as exc:
            await host.invoke("echo", "say", {})
        assert exc.value.kind is FailureKind.PLUGIN_FAILURE and "sk-ant" not in str(exc.value)
    with get_db() as conn:
        assert store.get(conn, "echo")["state"] == "quarantined"
        assert "quarantined" in [e["type"] for e in store.events(conn, "echo")]
    with pytest.raises(PluginError, match="not enabled"):
        await host.invoke("echo", "say", {})
    p.fail = None
    host.enable("echo", grant=[], actor="a")  # a deliberate re-enable clears it
    assert (await host.invoke("echo", "say", {}))["cap"] == "say"


async def test_one_success_resets_the_failure_count():
    p = Echo()
    host = host_with(p)
    host.enable("echo", grant=[], actor="a")
    for _ in range(QUARANTINE_AFTER - 1):
        p.fail = RuntimeError("x")
        with pytest.raises(PatchQuestError):
            await host.invoke("echo", "say", {})
    p.fail = None
    await host.invoke("echo", "say", {})
    with get_db() as conn:
        assert store.get(conn, "echo")["consecutive_failures"] == 0


async def test_a_trusted_plugin_that_hangs_times_out():
    import time as _t

    class Slow(Echo):
        def invoke(self, capability, args):
            _t.sleep(0.5)
            return {}

    host = host_with(Slow())
    host.enable("echo", grant=[], actor="a")
    with pytest.raises(PatchQuestError) as exc:
        await host.invoke("echo", "say", {}, limit_s=0.05)
    assert exc.value.kind is FailureKind.TOOL_TIMEOUT


async def test_a_plugin_must_answer_with_an_object_and_secrets_are_scrubbed_from_it():
    class Odd(Echo):
        def invoke(self, capability, args):
            return ["not", "a", "dict"] if args.get("bad") else {"key": "sk-ant-api03-" + "b" * 40}

    host = host_with(Odd())
    host.enable("echo", grant=[], actor="a")
    with pytest.raises(PatchQuestError):
        await host.invoke("echo", "say", {"bad": True})
    assert "sk-ant" not in str(await host.invoke("echo", "say", {}))


# ------------------------------------------------------------------ policy
async def test_policy_denies_or_requires_approval_for_plugin_calls():
    p = Echo()
    host = host_with(p)
    host.enable("echo", grant=[], actor="a")
    pol.store({"name": "p", "scope": "workspace", "rules": [{"action": "plugin.echo.say", "result": "DENY", "reason": "off"}]},
              scope_ref=LOCAL_WORKSPACE_ID, actor="a")
    chain = pol.chain_for(workspace_id=LOCAL_WORKSPACE_ID)
    with pytest.raises(PluginDenied, match="off"):
        await host.invoke("echo", "say", {}, chain=chain)
    # an EXTERNAL_WRITE capability asks for approval even with no stored policy (system floor)
    with pytest.raises(PluginApprovalRequired):
        await host.invoke("echo", "post", {})
    assert (await host.invoke("echo", "post", {}, approved=True))["cap"] == "post"
    assert [c for c, _ in p.calls] == ["post"]
    with get_db() as conn:
        assert {"denied", "needs_approval"} <= {e["type"] for e in store.events(conn, "echo")}


# ------------------------------------------------------------------ external process
PLUGIN_PY = textwrap.dedent('''
    import json, os, sys, time
    req = json.loads(sys.stdin.read())
    op, cap, args = req["op"], req.get("capability"), req.get("args", {})
    if op == "health":
        print(json.dumps({"ok": True, "detail": "up"}))
    elif cap == "env":
        print(json.dumps({"ok": True, "result": {"vars": sorted(os.environ), "cwd": os.getcwd(), "cfg": req["config"]}}))
    elif cap == "sleep":
        time.sleep(args["s"]); print(json.dumps({"ok": True, "result": {}}))
    elif cap == "crash":
        sys.stderr.write("kaboom"); sys.exit(3)
    elif cap == "garbage":
        print("not json")
    elif cap == "flood":
        sys.stdout.write("x" * 5_000_000)
    elif cap == "orphan":
        import subprocess
        pid = subprocess.Popen(["sleep", "30"]).pid
        open("pid.txt", "w").write(str(pid)); print(json.dumps({"ok": True, "result": {}}))
    else:
        print(json.dumps({"ok": True, "result": {"echo": args}}))
''')


@pytest.fixture
def ext(tmp_path):
    d = tmp_path / "plugins" / "proc"
    d.mkdir(parents=True)
    (d / "run.py").write_text(PLUGIN_PY)
    doc = {"name": "proc", "version": "1.0.0", "kind": "tool", "trust": "external_process", "command": ["python3", "run.py"],
           "permissions": ["secrets.read"], "config": {"token": {"type": "string", "secret": True}},
           "capabilities": {c: {"side_effect": "READ_ONLY"} for c in ("echo", "env", "sleep", "crash", "garbage", "flood", "orphan")}}
    import yaml
    (d / "plugin.yaml").write_text(yaml.safe_dump(doc))
    host = PluginHost(tmp_path / "plugins", scan_entry_points=False)
    set_host(host)
    return host


async def test_an_external_plugin_runs_in_a_scrubbed_environment(ext, monkeypatch):
    monkeypatch.setenv("PROC_TOKEN", "s3cret-value")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    ext.enable("proc", grant=["secrets.read"], config={"token": "env:PROC_TOKEN"}, actor="a")
    out = await ext.invoke("proc", "env", {})
    assert "AWS_SECRET_ACCESS_KEY" not in out["vars"] and "PROC_TOKEN" not in out["vars"]
    assert out["cwd"].endswith("plugins/proc") and out["cfg"]["token"] == "s3cret-value"
    assert (await ext.invoke("proc", "echo", {"a": 1})) == {"echo": {"a": 1}}
    with get_db() as conn:
        assert "s3cret-value" not in str(store.get(conn, "proc")) and "s3cret-value" not in str(store.events(conn, "proc"))


async def test_external_failures_are_contained(ext, monkeypatch):
    monkeypatch.setenv("PROC_TOKEN", "x")
    ext.enable("proc", grant=["secrets.read"], config={"token": "env:PROC_TOKEN"}, actor="a")
    with pytest.raises(PatchQuestError, match=r"status 3.*kaboom"):
        await ext.invoke("proc", "crash", {})
    with pytest.raises(PatchQuestError, match="JSON line"):
        await ext.invoke("proc", "garbage", {})
    with pytest.raises(PatchQuestError, match=r"more output|status"):
        await ext.invoke("proc", "flood", {})
    ext.enable("proc", grant=["secrets.read"], config={"token": "env:PROC_TOKEN"}, actor="a")  # three failures quarantined it
    with pytest.raises(PatchQuestError) as exc:
        await ext.invoke("proc", "sleep", {"s": 5}, limit_s=0.5)
    assert exc.value.kind is FailureKind.TOOL_TIMEOUT
    assert (await ext.invoke("proc", "echo", {"ok": 1}))["echo"] == {"ok": 1}


async def test_processes_a_plugin_leaves_behind_are_killed(ext, monkeypatch, tmp_path):
    import os
    import time
    monkeypatch.setenv("PROC_TOKEN", "x")
    ext.enable("proc", grant=["secrets.read"], config={"token": "env:PROC_TOKEN"}, actor="a")
    await ext.invoke("proc", "orphan", {})
    pid = int((tmp_path / "plugins" / "proc" / "pid.txt").read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError("the plugin's child process survived the call")


async def test_a_missing_secret_variable_fails_the_call_not_the_runtime(ext, monkeypatch):
    monkeypatch.setenv("PROC_TOKEN", "x")
    ext.enable("proc", grant=["secrets.read"], config={"token": "env:PROC_TOKEN"}, actor="a")
    monkeypatch.delenv("PROC_TOKEN")
    with pytest.raises(PatchQuestError, match="PROC_TOKEN"):
        await ext.invoke("proc", "echo", {})


def test_a_directory_plugin_cannot_escape_or_run_in_process(tmp_path):
    import yaml
    root = tmp_path / "plugins"
    for name, patch in (("escape", {"command": ["../../bin/sh"]}), ("inproc", {"trust": "trusted", "command": []})):
        d = root / name
        d.mkdir(parents=True)
        (d / "plugin.yaml").write_text(yaml.safe_dump({**manifest(name, trust="external_process", command=["./x"]), **patch}))
    (root / "mismatch").mkdir()
    (root / "mismatch" / "plugin.yaml").write_text(yaml.safe_dump(manifest("other", trust="external_process", command=["./x"])))
    found = {d.name: d for d in PluginHost(root, scan_entry_points=False).discover()}
    assert "outside the plugin directory" in found["escape"].error
    assert "in-process" in found["inproc"].error
    assert "directory name must match" in found["mismatch"].error


async def test_restore_brings_back_enabled_plugins_and_quarantines_changed_ones(tmp_path):
    p = Echo()
    host = host_with(p)
    host.enable("echo", grant=[], actor="a")
    fresh = Echo()
    host2 = host_with(fresh)
    assert host2.restore() == [] and fresh.started == {}
    upgraded = host_with(Echo(version="2.0.0"))
    assert upgraded.restore() == ["echo"]
    with get_db() as conn:
        assert store.get(conn, "echo")["state"] == "quarantined"


def test_tool_plugins_become_workflow_actions_only_while_enabled():
    host = host_with(Echo())
    assert host.tool_actions() == {}
    host.enable("echo", grant=[], actor="a")
    actions = host.tool_actions()
    assert set(actions) == {"plugin.echo.say", "plugin.echo.post"}
    assert actions["plugin.echo.post"].idempotent is False
