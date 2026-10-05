"""What an action can change in the world, as a small ordered vocabulary.

The command policy gate decides *whether* something may run; the side-effect class says *what it could
do*, and drives approvals (what may be remembered for a run) and recovery (what must never be repeated
blindly). Anything unrecognised is ``UNKNOWN``, which is treated as the most dangerous kind.
``tools.command_effects`` derives the class for commands.
"""

from __future__ import annotations

from enum import StrEnum


class SideEffect(StrEnum):
    PURE = "PURE"  # no observable effect
    READ_ONLY = "READ_ONLY"  # reads local state
    NETWORK_READ = "NETWORK_READ"  # fetches from the network, writes nothing remote
    WORKSPACE_WRITE = "WORKSPACE_WRITE"  # writes inside the isolated shadow workspace
    REPOSITORY_WRITE = "REPOSITORY_WRITE"  # changes the user's repository or its git state
    EXTERNAL_WRITE = "EXTERNAL_WRITE"  # changes something outside this machine
    HOST_MUTATION = "HOST_MUTATION"  # changes the host outside the repository (installs, config)
    DESTRUCTIVE = "DESTRUCTIVE"  # deletes or overwrites data
    UNKNOWN = "UNKNOWN"


# Remembering an approval for the rest of a run is only offered for effects that stay inside the sandbox.
GRANTABLE = frozenset({SideEffect.PURE, SideEffect.READ_ONLY, SideEffect.WORKSPACE_WRITE})
