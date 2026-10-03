"""Ready-made workflows. Each one validates against the action catalogue and is executed end to end in the tests."""

from __future__ import annotations

from typing import Any

_AGENT_DEFAULTS = {"provider": "{{vars.provider}}", "model": "{{vars.model}}"}
_VARS = {"repo": {"required": True}, "provider": {"default": "mock"}, "model": {"default": ""}}


def _agent(task: str, node_id: str = "investigate") -> dict[str, Any]:
    return {"id": node_id, "type": "agent", "config": {"task": task, "repo": "{{vars.repo}}", **_AGENT_DEFAULTS}}


TEMPLATES: dict[str, dict[str, Any]] = {
    "issue-to-pr": {
        "schema": 1, "name": "issue-to-pr",
        "description": "A labelled issue becomes a validated patch, a human decides, then a pull request is opened and the team is told.",
        "trigger": {"type": "github.issues.labeled", "filter": {"payload.label": "agent-ready"}},
        "variables": _VARS,
        "nodes": [
            _agent("Resolve this issue: {{trigger.payload.title}}\n\n{{trigger.payload.body}}"),
            {"id": "validated", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "review", "type": "approval", "config": {"message": "Open a pull request for run {{nodes.investigate.output.run_id}}?", "timeout_s": 86400}},
            {"id": "open_pr", "type": "action", "config": {"action": "github.create_pull_request",
                                                           "params": {"title": "{{trigger.payload.title}}", "body": "Opened by PatchQuest run {{nodes.investigate.output.run_id}}"}}},
            {"id": "tell", "type": "action", "config": {"action": "slack.post_message", "params": {"text": "PR opened for: {{trigger.payload.title}}"}}},
            {"id": "explain", "type": "action", "config": {"action": "notify.log", "params": {"message": "Not validated: no pull request"}}},
            {"id": "done", "type": "end"},
        ],
        "edges": [{"from": "investigate", "to": "validated"}, {"from": "validated", "to": "review", "when": "true"},
                  {"from": "validated", "to": "explain", "when": "false"}, {"from": "review", "to": "open_pr", "when": "approved"},
                  {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
                  {"from": "open_pr", "to": "tell"}, {"from": "tell", "to": "done"}, {"from": "explain", "to": "done"}],
    },
    "failed-ci-repair": {
        "schema": 1, "name": "failed-ci-repair",
        "description": "A failing CI run is investigated and repaired; the result is reported on the pull request after approval.",
        "trigger": {"type": "github.check_suite.completed", "filter": {"payload.conclusion": "failure"}},
        "variables": _VARS,
        "nodes": [
            _agent("CI failed on {{trigger.payload.branch}}. Reproduce the failure and fix it with the smallest change."),
            {"id": "fixed", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "review", "type": "approval", "config": {"message": "Comment the proposed fix on {{trigger.payload.branch}}?", "timeout_s": 43200}},
            {"id": "report", "type": "action", "config": {"action": "github.comment",
                                                          "params": {"body": "PatchQuest proposes a fix (run {{nodes.investigate.output.run_id}})."}}},
            {"id": "note", "type": "action", "config": {"action": "notify.log", "params": {"message": "Could not fix {{trigger.payload.branch}}"}}},
            {"id": "done", "type": "end"},
        ],
        "edges": [{"from": "investigate", "to": "fixed"}, {"from": "fixed", "to": "review", "when": "true"},
                  {"from": "fixed", "to": "note", "when": "false"}, {"from": "review", "to": "report", "when": "approved"},
                  {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
                  {"from": "report", "to": "done"}, {"from": "note", "to": "done"}],
    },
    "dependency-upgrade": {
        "schema": 1, "name": "dependency-upgrade",
        "description": "Started on a schedule or by hand: upgrade dependencies in isolation, run the tests, and ask before proposing it.",
        "trigger": {"type": "manual"},
        "variables": _VARS,
        "nodes": [
            _agent("Upgrade outdated dependencies one at a time, keeping the test suite green. Do not change application code."),
            {"id": "green", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "review", "type": "approval", "config": {"message": "Propose the upgrade from run {{nodes.investigate.output.run_id}}?", "timeout_s": 259200}},
            {"id": "open_pr", "type": "action", "config": {"action": "github.create_pull_request",
                                                           "params": {"title": "Upgrade dependencies", "body": "Validated by PatchQuest run {{nodes.investigate.output.run_id}}"}}},
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
