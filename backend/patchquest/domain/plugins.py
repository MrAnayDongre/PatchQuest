"""What a plugin declares about itself, and the rules for accepting it.

A plugin is code an operator installs; it is never uploaded through the API and never enabled implicitly.
Its manifest says what it is (``kind``), what it can do (``capabilities``, each with a side-effect class),
what it needs (``permissions``) and how much it is trusted (``trust``). Enabling a plugin is an explicit
grant of exactly the permissions it declares. Permissions are declarations that policy and operators can
read and refuse; only ``external_process`` plugins get any enforcement (separate process, scrubbed
environment, resource limits, timeout) - see ``docs/plugins.md`` for what is and is not isolated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from patchquest.domain.effects import SideEffect


class PluginKind(StrEnum):
    TOOL = "tool"  # becomes workflow actions
    CONNECTOR = "connector"
    PROVIDER = "provider"
    CONTEXT_SOURCE = "context_source"
    EVALUATOR = "evaluator"
    WORKFLOW_NODE = "workflow_node"


class Trust(StrEnum):
    TRUSTED = "trusted"  # runs inside the PatchQuest process: full privileges, no isolation
    EXTERNAL_PROCESS = "external_process"  # one subprocess per call, with limits


class PluginState(StrEnum):
    DISCOVERED = "discovered"
    ENABLED = "enabled"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"  # too many consecutive failures; needs a deliberate re-enable
    INVALID = "invalid"  # its manifest was refused


PERMISSIONS = frozenset({"fs.read", "fs.write", "net.http", "process.spawn", "secrets.read", "repo.read", "repo.write"})
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
_CAP = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+([.+-][0-9A-Za-z.+-]*)?$")
_FIELD_TYPES: dict[str, type | tuple[type, ...]] = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
MAX_ARGS_BYTES = 256_000


class PluginError(ValueError):
    """A manifest, configuration or call was refused. The message is safe to show."""


@dataclass(frozen=True)
class Capability:
    name: str
    side_effect: SideEffect
    idempotent: bool = True
    description: str = ""


@dataclass(frozen=True)
class ConfigField:
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    secret: bool = False  # given as "env:NAME"; the value itself is never stored


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    kind: PluginKind
    trust: Trust
    capabilities: Mapping[str, Capability]
    permissions: frozenset[str] = frozenset()
    config: tuple[ConfigField, ...] = ()
    min_runtime: str = "0.0"
    command: tuple[str, ...] = ()  # external_process only
    description: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(p) for p in parts.groups(default="0")) if parts else (0, 0, 0)


def parse_manifest(doc: Any) -> Manifest:
    """Validate a manifest mapping. Raises ``PluginError`` naming the first problem."""
    if not isinstance(doc, Mapping):
        raise PluginError("a manifest must be a mapping")
    allowed = {"name", "version", "kind", "trust", "capabilities", "permissions", "config", "runtime", "command", "description"}
    if unknown := set(doc) - allowed:
        raise PluginError(f"unknown manifest field(s): {', '.join(sorted(unknown))}")
    name, version = doc.get("name"), doc.get("version")
    if not isinstance(name, str) or not _NAME.match(name):
        raise PluginError("name must be 2-41 lowercase letters, digits, '-' or '_', starting with a letter")
    if not isinstance(version, str) or not _VERSION.match(version):
        raise PluginError("version must look like 1.2.3")
    try:
        kind, trust = PluginKind(str(doc.get("kind"))), Trust(str(doc.get("trust", "external_process")))
    except ValueError as exc:
        raise PluginError(str(exc)) from None
    perms = doc.get("permissions", [])
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise PluginError("permissions must be a list of names")
    if unknown_perms := set(perms) - PERMISSIONS:
        raise PluginError(f"unknown permission(s): {', '.join(sorted(unknown_perms))}")
    caps_in = doc.get("capabilities")
    if not isinstance(caps_in, Mapping) or not caps_in or len(caps_in) > 50:
        raise PluginError("capabilities must name 1-50 things the plugin can do")
    caps = {}
    for cname, c in caps_in.items():
        if not isinstance(cname, str) or not _CAP.match(cname):
            raise PluginError(f"capability name {cname!r} is not valid")
        c = c or {}
        if not isinstance(c, Mapping) or set(c) - {"side_effect", "idempotent", "description"}:
            raise PluginError(f"capability {cname}: expected side_effect/idempotent/description")
        if "side_effect" not in c:
            raise PluginError(f"capability {cname}: declare its side_effect (unsure? UNKNOWN)")
        try:
            effect = SideEffect(str(c["side_effect"]).upper())
        except ValueError:
            raise PluginError(f"capability {cname}: unknown side_effect {c['side_effect']!r}") from None
        caps[cname] = Capability(cname, effect, bool(c.get("idempotent", True)), str(c.get("description", ""))[:200])
    fields = []
    for fname, f in (doc.get("config") or {}).items():
        f = f or {}
        if not isinstance(f, Mapping) or set(f) - {"type", "required", "default", "secret"} or f.get("type", "string") not in _FIELD_TYPES:
            raise PluginError(f"config field {fname}: expected type string/integer/number/boolean, required, default, secret")
        fields.append(ConfigField(str(fname), f.get("type", "string"), bool(f.get("required", False)), f.get("default"),
                                  bool(f.get("secret", False))))
        if fields[-1].secret and fields[-1].type != "string":
            raise PluginError(f"config field {fname}: a secret must be a string")
        if fields[-1].secret and "secrets.read" not in perms:
            raise PluginError(f"config field {fname}: reading a secret requires the secrets.read permission")
    command = doc.get("command", [])
    if trust is Trust.EXTERNAL_PROCESS:
        if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
            raise PluginError("an external_process plugin needs a command (a list of strings)")
    elif command:
        raise PluginError("a trusted plugin is loaded from an entry point and takes no command")
    runtime = str(doc.get("runtime", ">=0.0"))
    if not runtime.startswith(">="):
        raise PluginError("runtime must be a minimum version such as '>=0.1'")
    return Manifest(name, version, kind, trust, caps, frozenset(perms), tuple(fields), runtime[2:], tuple(command),
                    str(doc.get("description", ""))[:300])


def check_compatible(manifest: Manifest, runtime_version: str) -> None:
    if _version_tuple(runtime_version) < _version_tuple(manifest.min_runtime):
        raise PluginError(f"{manifest.name} needs PatchQuest {manifest.min_runtime} or newer (this is {runtime_version})")


def validate_config(manifest: Manifest, values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Check supplied settings against the manifest; fill defaults. Secrets must be ``env:NAME`` references."""
    values = dict(values or {})
    declared = {f.name: f for f in manifest.config}
    if unknown := set(values) - set(declared):
        raise PluginError(f"unknown setting(s) for {manifest.name}: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for name, f in declared.items():
        if name not in values:
            if f.required and f.default is None:
                raise PluginError(f"setting '{name}' is required")
            out[name] = f.default
            continue
        v = values[name]
        expected = _FIELD_TYPES[f.type]
        if (isinstance(v, bool) and f.type != "boolean") or not isinstance(v, expected):
            raise PluginError(f"setting '{name}' must be a {f.type}")
        if f.secret and not (isinstance(v, str) and re.fullmatch(r"env:[A-Z_][A-Z0-9_]{0,63}", v)):
            raise PluginError(f"setting '{name}' is a secret: give it as env:VARIABLE_NAME, not the value")
        out[name] = v
    return out


def check_grant(manifest: Manifest, granted: frozenset[str]) -> None:
    """Enabling grants exactly what the plugin declares: more is refused, and so is less."""
    if granted != manifest.permissions:
        missing, extra = manifest.permissions - granted, granted - manifest.permissions
        raise PluginError("grant must match the declared permissions"
                          + (f"; missing: {', '.join(sorted(missing))}" if missing else "")
                          + (f"; not declared: {', '.join(sorted(extra))}" if extra else ""))
