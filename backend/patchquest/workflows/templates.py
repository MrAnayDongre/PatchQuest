"""Ready-made workflows. Each one validates against the action catalogue and is executed end to end in the tests."""

from __future__ import annotations

from typing import Any

_AGENT_DEFAULTS = {"provider": "{{vars.provider}}", "model": "{{vars.model}}"}
_VARS = {"repo": {"required": True}, "provider": {"default": "mock"}, "model": {"default": ""}}


def _agent(task: str, node_id: str = "investigate") -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "config": {"task": task, "repo": "{{vars.repo}}", **_AGENT_DEFAULTS}}


TEMPLATES: dict[str, dict[str, Any]] = {
    "issue-to-proposal": {
        "schema": 1, "name": "issue-to-proposal",
        "description": "A labelled issue becomes a validated patch; after a person approves, the proposal is posted on the issue. "
                       "(Opening a pull request needs a branch-push action PatchQuest does not have yet.)",
        "trigger": {"type": "github.issues.labeled", "filter": {"payload.label": "agent-ready"}},
        "variables": _VARS,
        "nodes": [
            _agent("Resolve this issue: {{trigger.payload.title}}\n\n{{trigger.payload.body}}"),
            {"id": "validated", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "review", "type": "approval", "config": {"message": "Post the proposed fix from run {{nodes.investigate.output.run_id}} on the issue?", "timeout_s": 86400}},
            {"id": "propose", "type": "action", "config": {"action": "github.comment", "params": {
                "issue_number": "{{trigger.payload.number}}",
                "body": "PatchQuest validated a fix for this issue (run {{nodes.investigate.output.run_id}}). A maintainer can review its diff there."}}},
            {"id": "explain", "type": "action", "config": {"action": "notify.log", "params": {"message": "Not validated: nothing was posted"}}},
            {"id": "done", "type": "end"},
        ],
        "edges": [{"from": "investigate", "to": "validated"}, {"from": "validated", "to": "review", "when": "true"},
                  {"from": "validated", "to": "explain", "when": "false"}, {"from": "review", "to": "propose", "when": "approved"},
                  {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
                  {"from": "propose", "to": "done"}, {"from": "explain", "to": "done"}],
    },
    "failed-ci-repair": {
        "schema": 1, "name": "failed-ci-repair",
        "description": "A failing CI run is investigated and repaired in isolation; after approval the proposed fix is recorded for review.",
        "trigger": {"type": "github.check_suite.completed", "filter": {"payload.conclusion": "failure"}},
        "variables": _VARS,
        "nodes": [
            _agent("CI failed on {{trigger.payload.head_branch}}. Reproduce the failure and fix it with the smallest change."),
            {"id": "fixed", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "review", "type": "approval", "config": {"message": "Record the proposed fix for {{trigger.payload.head_branch}}?", "timeout_s": 43200}},
            {"id": "report", "type": "action", "config": {"action": "notify.log", "params": {
                "message": "Proposed fix for {{trigger.payload.head_branch}} is ready in run {{nodes.investigate.output.run_id}}"}}},
            {"id": "note", "type": "action", "config": {"action": "notify.log", "params": {"message": "Could not fix {{trigger.payload.head_branch}}"}}},
            {"id": "done", "type": "end"},
        ],
        "edges": [{"from": "investigate", "to": "fixed"}, {"from": "fixed", "to": "review", "when": "true"},
                  {"from": "fixed", "to": "note", "when": "false"}, {"from": "review", "to": "report", "when": "approved"},
                  {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
                  {"from": "report", "to": "done"}, {"from": "note", "to": "done"}],
    },
    "dependency-upgrade": {
        "schema": 1, "name": "dependency-upgrade",
        "description": "Started by hand: upgrade dependencies in isolation, run the tests, and ask before recording the result for review.",
        "trigger": {"type": "manual"},
        "variables": _VARS,
        "nodes": [
            _agent("Upgrade outdated dependencies one at a time, keeping the test suite green. Do not change application code."),
            {"id": "green", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "review", "type": "approval", "config": {"message": "Record the upgrade from run {{nodes.investigate.output.run_id}}?", "timeout_s": 259200}},
            {"id": "open_pr", "type": "action", "config": {"action": "notify.log", "params": {
                "message": "Dependency upgrade validated in run {{nodes.investigate.output.run_id}}; review its diff"}}},
            {"id": "done", "type": "end"},
        ],
        "edges": [{"from": "investigate", "to": "green"}, {"from": "green", "to": "review", "when": "true"},
                  {"from": "green", "to": "done", "when": "false"}, {"from": "review", "to": "open_pr", "when": "approved"},
                  {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
                  {"from": "open_pr", "to": "done"}],
    },
    "oncall-investigation": {
        "schema": 1, "name": "oncall-investigation",
        "description": "A message in an incident channel starts a read-only investigation whose findings are posted back after approval.",
        "trigger": {"type": "slack.message.mention"},
        "variables": _VARS,
        "nodes": [
            {"id": "investigate", "type": "agent", "config": {
                "task": "Read only: investigate and explain the likely cause of: {{trigger.payload.text}}. Do not modify any files.",
                "repo": "{{vars.repo}}", **_AGENT_DEFAULTS}},
            {"id": "review", "type": "approval", "config": {"message": "Post the findings of run {{nodes.investigate.output.run_id}} to the channel?", "timeout_s": 3600}},
            {"id": "reply", "type": "action", "config": {"action": "slack.post_message",
                                                        "params": {"text": "Findings are ready (run {{nodes.investigate.output.run_id}})."}}},
            {"id": "done", "type": "end"},
        ],
        "edges": [{"from": "investigate", "to": "review"}, {"from": "review", "to": "reply", "when": "approved"},
                  {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
                  {"from": "reply", "to": "done"}],
    },
}


def instantiate(name: str, **variables: str) -> dict[str, Any]:
    """A template with its variables bound (``repo``, ``provider``, ``model``): the definition to save.

    The raw templates declare ``repo`` as required, so an event-triggered one cannot be saved unbound:
    validation refuses a workflow that an event could start but could not give its values.
    """
    import copy

    definition = copy.deepcopy(TEMPLATES[name])
    unknown = set(variables) - set(definition["variables"])
    if unknown:
        raise ValueError(f"unknown variable(s) for {name}: {', '.join(sorted(unknown))}")
    for key, value in variables.items():
        definition["variables"][key] = {"default": value}
    return definition


def requires(name: str) -> list[str]:
    """Connectors a template needs (from its actions and trigger), so a user knows what to connect first."""
    definition = TEMPLATES[name]
    found = {definition["trigger"]["type"].split(".", 1)[0]} - {"manual"}
    for node in definition["nodes"]:
        action = node.get("config", {}).get("action")
        if node["type"] == "action" and action and not action.startswith("notify."):
            found.add(action.split(".", 1)[0])
    return sorted(found)
