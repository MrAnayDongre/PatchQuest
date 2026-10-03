"""Side-effect class of a command, derived from its parsed argv (never from substrings of free text)."""

from __future__ import annotations

import os

from patchquest.domain.effects import SideEffect
from patchquest.tools.command_risk import Decision, RiskLevel

_NETWORK = frozenset({"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "telnet", "ftp", "docker", "podman",
                      "kubectl", "helm", "terraform", "aws", "gcloud", "az"})
_DELETERS = frozenset({"rm", "rmdir", "shred", "dd", "mkfs", "truncate"})
_WORKSPACE_WRITERS = frozenset({"cp", "mv", "mkdir", "touch", "tee", "chmod", "chown", "ln", "sed", "patch", "tar", "unzip"})
_INSTALLERS = frozenset({"pip", "pip3", "npm", "yarn", "pnpm", "gem", "cargo", "apt", "apt-get", "brew", "go", "dnf", "yum"})
_GIT_HOST = frozenset({"push", "remote"})


def effect_of(decision: Decision) -> SideEffect:
    """Side-effect class for a command the policy gate has already classified."""
    if decision.level is RiskLevel.BLOCKED:
        return SideEffect.DESTRUCTIVE
    if decision.level is RiskLevel.NO_RISK_AUTO:
        return SideEffect.READ_ONLY if decision.argv else SideEffect.PURE
    if decision.level is RiskLevel.CAREFUL_AUTO:
        return SideEffect.WORKSPACE_WRITE  # build/test tools write caches and artefacts in the workspace
    if decision.shell_syntax or not decision.argv:
        return SideEffect.UNKNOWN  # a shell can do anything
    exe = os.path.basename(decision.argv[0])
    if exe == "git":
        sub = next((a for a in decision.argv[1:] if not a.startswith("-")), "")
        return SideEffect.EXTERNAL_WRITE if sub in _GIT_HOST or sub in {"fetch", "pull", "clone"} else SideEffect.REPOSITORY_WRITE
    if exe in _NETWORK:
        return SideEffect.EXTERNAL_WRITE
    if exe in _DELETERS:
        return SideEffect.DESTRUCTIVE
    if exe in _INSTALLERS:
        return SideEffect.HOST_MUTATION
    if exe in _WORKSPACE_WRITERS:
        return SideEffect.WORKSPACE_WRITE
    return SideEffect.UNKNOWN
