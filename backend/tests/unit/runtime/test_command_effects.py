import pytest

from patchquest.domain.effects import GRANTABLE, SideEffect
from patchquest.tools.command_effects import effect_of
from patchquest.tools.command_risk import classify


@pytest.mark.parametrize("command,effect", [
    ("", SideEffect.PURE),
    ("ls -la", SideEffect.READ_ONLY),
    ("git status", SideEffect.READ_ONLY),
    ("python3 -m pytest -q", SideEffect.WORKSPACE_WRITE),
    ("git commit -m x", SideEffect.REPOSITORY_WRITE),
    ("git checkout main", SideEffect.REPOSITORY_WRITE),
    ("git push origin main", SideEffect.EXTERNAL_WRITE),
    ("curl https://example.com", SideEffect.EXTERNAL_WRITE),
    ("docker run alpine", SideEffect.EXTERNAL_WRITE),
    ("rm -r build", SideEffect.DESTRUCTIVE),
    ("pip install flask", SideEffect.HOST_MUTATION),
    ("npm install left-pad", SideEffect.HOST_MUTATION),
    ("touch notes.txt", SideEffect.WORKSPACE_WRITE),
    ("make deploy", SideEffect.UNKNOWN),
    ("echo hi > out.txt", SideEffect.UNKNOWN),       # shell syntax: a shell can do anything
    ("ls && rm -rf x", SideEffect.UNKNOWN),
    ("./run.sh", SideEffect.UNKNOWN),
])
def test_commands_get_the_conservative_class(command, effect):
    assert effect_of(classify(command, "/repo")) is effect


def test_blocked_commands_are_destructive():
    assert effect_of(classify("rm -rf /", "/repo")) is SideEffect.DESTRUCTIVE


def test_nothing_that_leaves_the_sandbox_is_grantable():
    for effect in (SideEffect.EXTERNAL_WRITE, SideEffect.HOST_MUTATION, SideEffect.DESTRUCTIVE, SideEffect.UNKNOWN,
                   SideEffect.REPOSITORY_WRITE, SideEffect.NETWORK_READ):
        assert effect not in GRANTABLE
