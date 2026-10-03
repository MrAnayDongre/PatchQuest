"""Plugin lifecycle: discover, validate, enable (an explicit grant), health, invoke (policy-gated), disable.

One misbehaving plugin never takes the runtime down: every call is wrapped, bounded by a timeout, recorded in
``plugin_events`` and counted; after ``QUARANTINE_AFTER`` consecutive failures the plugin is quarantined until
someone enables it again on purpose. Discovery reads manifests only; code of a trusted plugin is imported when
its entry point is loaded, which is why only an operator-installed distribution can be trusted.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

import yaml

from patchquest import __version__
from patchquest.database import ensure_state_dir, get_db
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.domain.plugins import (
    MAX_ARGS_BYTES,
    Manifest,
    PluginError,
    PluginKind,
    PluginState,
    Trust,
    check_compatible,
    check_grant,
    parse_manifest,
    validate_config,
)
from patchquest.domain.policy import Policy, Result
from patchquest.domain.workflows import ActionInfo
from patchquest.persistence import plugins as store
from patchquest.plugins import process
from patchquest.runtime import policy as policy_runtime
from patchquest.tools.secret_guard import redact_secrets

QUARANTINE_AFTER = 3
DEFAULT_TIMEOUT = 30.0
ENTRY_POINT_GROUP = "patchquest.plugins"
MAX_RESULT_BYTES = 256_000


class PluginDenied(PermissionError):
    """Policy forbids this call."""


class PluginApprovalRequired(PermissionError):
    """Policy wants a human to approve this call first."""


class TrustedPlugin(Protocol):
    """What a trusted (in-process) plugin implements; ``manifest`` is the manifest mapping."""

    manifest: dict[str, Any]

    def initialize(self, config: dict[str, Any]) -> None: ...
    def health(self) -> dict[str, Any]: ...
    def invoke(self, capability: str, args: dict[str, Any]) -> dict[str, Any]: ...
    def shutdown(self) -> None: ...


@dataclass
class Discovered:
    name: str
    source: str
    manifest: Manifest | None = None
    error: str | None = None
    directory: Path | None = None
    instance: TrustedPlugin | None = None


class PluginHost:
    def __init__(self, plugin_dir: Path | None = None, *, scan_entry_points: bool = True,
                 factories: dict[str, Callable[[], TrustedPlugin]] | None = None) -> None:
        self._dir = plugin_dir
        self._scan_entry_points = scan_entry_points
        self._factories = dict(factories or {})  # injected trusted plugins (tests, embedding applications)
        self._found: dict[str, Discovered] = {}
        self._live: dict[str, TrustedPlugin] = {}

    # ----------------------------------------------------------------- discovery
    def plugin_dir(self) -> Path:
        return self._dir or ensure_state_dir() / "plugins"

    def discover(self) -> list[Discovered]:
        """Find installed plugins. A broken one is reported (``error``), never raised."""
        found: dict[str, Discovered] = {}

        def add(d: Discovered) -> None:
            if d.name in found:  # first source wins; a duplicate name is reported, not silently shadowed
                found[d.name].error = found[d.name].error or f"another plugin is also named {d.name} ({d.source})"
                return
            found[d.name] = d

        for name, factory in self._factories.items():
            add(self._from_instance(name, f"factory:{name}", factory))
        if self._scan_entry_points:
            for ep in entry_points(group=ENTRY_POINT_GROUP):
                add(self._from_instance(ep.name, f"entry_point:{ep.value}", ep.load))
        root = self.plugin_dir()
        if root.is_dir():
            for manifest_path in sorted(root.glob("*/plugin.yaml")):
                add(self._from_directory(manifest_path))
        self._found = found
        return list(found.values())

    @staticmethod
    def _from_instance(name: str, source: str, factory: Callable[[], Any]) -> Discovered:
        try:
            instance = factory()
            manifest = parse_manifest(instance.manifest)
            if manifest.trust is not Trust.TRUSTED:
                raise PluginError("a plugin loaded in-process must declare trust: trusted")
            return Discovered(manifest.name, source, manifest, instance=instance)
        except Exception as exc:
            return Discovered(name, source, error=f"{type(exc).__name__}: {exc}"[:300])

    @staticmethod
    def _from_directory(manifest_path: Path) -> Discovered:
        directory = manifest_path.parent
        try:
            manifest = parse_manifest(yaml.safe_load(manifest_path.read_text()))
            if manifest.trust is not Trust.EXTERNAL_PROCESS:
                raise PluginError("a plugin found in the plugin directory cannot run in-process; install it as a package")
            if manifest.name != directory.name:
                raise PluginError(f"the directory name must match the plugin name ({manifest.name})")
            process.resolve_command(manifest.command, directory)
            return Discovered(manifest.name, f"dir:{directory}", manifest, directory=directory)
        except (OSError, yaml.YAMLError, PluginError, process.ProcessFailure) as exc:
            return Discovered(directory.name, f"dir:{directory}", error=str(exc)[:300])

    def _get(self, name: str) -> Discovered:
        if name not in self._found:
            self.discover()
        found = self._found.get(name)
        if found is None:
            raise PluginError(f"no plugin named {name}")
        return found

    # ----------------------------------------------------------------- lifecycle
    def enable(self, name: str, *, grant: list[str], config: dict[str, Any] | None = None, actor: str) -> None:
        """Validate and switch a plugin on. ``grant`` must equal its declared permissions."""
        found = self._get(name)
        if found.manifest is None:
            raise PluginError(found.error or "the plugin's manifest is not valid")
        manifest = found.manifest
        check_compatible(manifest, __version__.split("+")[0])
        check_grant(manifest, frozenset(grant))
        settings = validate_config(manifest, config)
        self._start(found, settings)
        with get_db() as conn:
            store.save(conn, name, manifest.version, PluginState.ENABLED.value, grant, settings, actor)
            store.event(conn, name, "enabled", detail={"actor": actor, "version": manifest.version, "granted": sorted(grant)})

    def _start(self, found: Discovered, settings: dict[str, Any]) -> None:
        manifest = _manifest(found)
        try:
            if manifest.trust is Trust.TRUSTED and found.instance is not None:
                found.instance.initialize(settings)
                self._live[found.name] = found.instance
            else:
                directory = _directory(found)
                reply = process.call(manifest.command, directory,
                                     {"op": "health", "config": self._resolve_secrets(manifest, settings)}, 10)
                if not reply.get("ok"):
                    raise PluginError(str(reply.get("error", "health check failed")))
        except PluginError:
            raise
        except Exception as exc:
            raise PluginError(f"{found.name} did not start: {redact_secrets(str(exc))[:200]}") from None

    def disable(self, name: str, *, actor: str) -> None:
        with get_db() as conn:
            if store.get(conn, name) is None:
                raise PluginError(f"{name} is not enabled")
            store.set_state(conn, name, PluginState.DISABLED.value, actor)
            store.event(conn, name, "disabled", detail={"actor": actor})
        self._stop(name)

    def _stop(self, name: str) -> None:
        live = self._live.pop(name, None)
        if live is not None:
            try:
                live.shutdown()
            except Exception as exc:  # shutting down must not fail the caller
                with get_db() as conn:
                    store.event(conn, name, "shutdown_failed", outcome="error", detail={"error": redact_secrets(str(exc))[:200]})

    def shutdown_all(self) -> None:
        for name in list(self._live):
            self._stop(name)

    def restore(self) -> list[str]:
        """After a restart: bring back the plugins that were enabled. Returns those that failed to start."""
        failed = []
        with get_db() as conn:
            enabled = [s for s in store.list_state(conn) if s["state"] == PluginState.ENABLED.value]
        for state in enabled:
            try:
                found = self._get(state["name"])
                if found.manifest is None or found.manifest.version != state["version"]:
                    raise PluginError("installed version differs from the version that was enabled")
                self._start(found, state["config"])
            except PluginError as exc:
                failed.append(state["name"])
                with get_db() as conn:
                    store.set_state(conn, state["name"], PluginState.QUARANTINED.value, "restore")
                    store.event(conn, state["name"], "restore_failed", outcome="error", detail={"error": str(exc)[:200]})
        return failed

    def tool_actions(self) -> dict[str, ActionInfo]:
        """Workflow actions contributed by enabled tool plugins, as ``plugin.<name>.<capability>``."""
        if not self._found:
            self.discover()
        with get_db() as conn:
            enabled = {s["name"] for s in store.list_state(conn) if s["state"] == PluginState.ENABLED.value}
        return {f"plugin.{d.name}.{c.name}": ActionInfo(c.side_effect, c.idempotent)
                for d in self._found.values() if d.manifest is not None and d.name in enabled and d.manifest.kind is PluginKind.TOOL
                for c in d.manifest.capabilities.values()}

    # ----------------------------------------------------------------- inspection
    def status(self) -> list[dict[str, Any]]:
        with get_db() as conn:
            persisted = {s["name"]: s for s in store.list_state(conn)}
        rows = []
        for d in self.discover():
            s = persisted.get(d.name)
            m = d.manifest
            rows.append({"name": d.name, "source": d.source, "version": m.version if m else None, "kind": m.kind.value if m else None,
                         "trust": m.trust.value if m else None, "permissions": sorted(m.permissions) if m else [],
                         "capabilities": {c.name: c.side_effect.value for c in m.capabilities.values()} if m else {},
                         "state": s["state"] if s else (PluginState.DISCOVERED.value if m else PluginState.INVALID.value),
                         "error": d.error or (s or {}).get("last_error"),
                         "consecutive_failures": (s or {}).get("consecutive_failures", 0)})
        return rows

    def health(self, name: str) -> dict[str, Any]:
        found = self._get(name)
        with get_db() as conn:
            state = store.get(conn, name)
        base = {"name": name, "state": state["state"] if state else PluginState.DISCOVERED.value, "ok": False, "detail": ""}
        if state is None or state["state"] != PluginState.ENABLED.value or found.manifest is None:
            return {**base, "detail": "not enabled"}
        try:
            if found.manifest.trust is Trust.TRUSTED and found.instance is not None:
                report = found.instance.health()
            else:
                directory = _directory(found)
                report = process.call(found.manifest.command, directory,
                                      {"op": "health", "config": self._resolve_secrets(found.manifest, state["config"])}, 10)
            return {**base, "ok": bool(report.get("ok", True)), "detail": str(report.get("detail", report.get("error", "")))[:200]}
        except Exception as exc:
            return {**base, "detail": redact_secrets(str(exc))[:200]}

    # ----------------------------------------------------------------- invocation
    async def invoke(self, name: str, capability: str, args: dict[str, Any], *, chain: list[Policy] | None = None,
                     approved: bool = False, limit_s: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        """Run one capability. Raises ``PluginDenied`` / ``PluginApprovalRequired`` (policy), ``PluginError`` (bad request)
        or ``PatchQuestError`` with kind PLUGIN_FAILURE / TOOL_TIMEOUT (the plugin misbehaved)."""
        found = self._get(name)
        with get_db() as conn:
            state = store.get(conn, name)
        if state is None or state["state"] != PluginState.ENABLED.value or found.manifest is None:
            raise PluginError(f"{name} is not enabled")
        cap = found.manifest.capabilities.get(capability)
        if cap is None:
            raise PluginError(f"{name} has no capability {capability}")
        if not isinstance(args, dict) or len(json.dumps(args, default=str)) > MAX_ARGS_BYTES:
            raise PluginError("arguments must be an object of at most 256 KB")

        verdict = policy_runtime.decide(chain or [], f"plugin.{name}.{capability}", cap.side_effect)
        if verdict.result is Result.DENY:
            self._log(name, "denied", capability, "denied", 0, {"policy": verdict.source_policy})
            raise PluginDenied(f"blocked by policy '{verdict.source_policy}': {verdict.reason}")
        if verdict.approval_required and not approved:
            self._log(name, "needs_approval", capability, "denied", 0, {"policy": verdict.source_policy})
            raise PluginApprovalRequired(f"{name}.{capability} needs a human approval ({verdict.reason})")

        started = time.monotonic()
        try:
            result = await self._run(found, state["config"], capability, args, limit_s)
            result = json.loads(redact_secrets(json.dumps(result)))
        except Exception as exc:
            failure = self._failure(name, exc)
            elapsed = int((time.monotonic() - started) * 1000)
            with get_db() as conn:
                quarantined = store.record_outcome(conn, name, False, failure.detail, QUARANTINE_AFTER)
                store.event(conn, name, "invoke_failed", capability=capability, outcome="error", duration_ms=elapsed,
                            detail={"error": failure.detail[:200], "kind": failure.kind.value, "arg_keys": sorted(args)})
                if quarantined:
                    store.event(conn, name, "quarantined", outcome="error", detail={"after": QUARANTINE_AFTER})
            raise failure from None
        elapsed = int((time.monotonic() - started) * 1000)
        with get_db() as conn:
            store.record_outcome(conn, name, True, None, QUARANTINE_AFTER)
            store.event(conn, name, "invoked", capability=capability, duration_ms=elapsed, detail={"arg_keys": sorted(args)})
        return result

    async def _run(self, found: Discovered, settings: dict[str, Any], capability: str, args: dict[str, Any],
                   limit: float) -> dict[str, Any]:
        manifest = _manifest(found)
        if manifest.trust is Trust.TRUSTED:
            instance = self._live.get(found.name)
            if instance is None:
                raise PluginError(f"{found.name} is not running; restart or re-enable it")
            # ponytail: a thread cannot be killed, so a wedged trusted plugin keeps its thread after the timeout;
            # run it as external_process when it must be stoppable.
            reply: Any = await asyncio.wait_for(asyncio.to_thread(instance.invoke, capability, args), limit)
        else:
            directory = _directory(found)
            answer = await asyncio.to_thread(
                process.call, manifest.command, directory,
                {"op": "invoke", "capability": capability, "args": args, "config": self._resolve_secrets(manifest, settings)},
                limit)
            if not answer.get("ok"):
                raise PluginError(str(answer.get("error", "the plugin reported an error")))
            reply = answer.get("result")
        if not isinstance(reply, dict):
            raise PluginError("the plugin must answer with an object")
        if len(json.dumps(reply, default=str)) > MAX_RESULT_BYTES:
            raise PluginError("the plugin's answer is larger than allowed")
        return reply

    @staticmethod
    def _failure(name: str, exc: Exception) -> PatchQuestError:
        if isinstance(exc, PatchQuestError):
            return exc
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or getattr(exc, "timed_out", False):
            return PatchQuestError(FailureKind.TOOL_TIMEOUT, f"plugin {name} timed out")
        return PatchQuestError(FailureKind.PLUGIN_FAILURE, f"plugin {name} failed: {redact_secrets(str(exc))[:200]}")

    @staticmethod
    def _resolve_secrets(manifest: Manifest, settings: dict[str, Any]) -> dict[str, Any]:
        out = dict(settings)
        for f in manifest.config:
            if f.secret and isinstance(out.get(f.name), str) and out[f.name].startswith("env:"):
                value = os.environ.get(out[f.name][4:])
                if value is None:
                    raise PluginError(f"environment variable {out[f.name][4:]} (setting '{f.name}') is not set")
                out[f.name] = value
        return out

    @staticmethod
    def _log(name: str, type_: str, capability: str, outcome: str, ms: int, detail: dict[str, Any]) -> None:
        with get_db() as conn:
            store.event(conn, name, type_, capability=capability, outcome=outcome, duration_ms=ms, detail=detail)


def _manifest(found: Discovered) -> Manifest:
    if found.manifest is None:
        raise PluginError(found.error or f"{found.name} has no valid manifest")
    return found.manifest


def _directory(found: Discovered) -> Path:
    if found.directory is None:
        raise PluginError(f"{found.name} has no directory to run from")
    return found.directory


_host: PluginHost | None = None


def get_host() -> PluginHost:
    global _host
    if _host is None:
        _host = PluginHost()
    return _host


def set_host(host: PluginHost | None) -> None:
    global _host
    _host = host
