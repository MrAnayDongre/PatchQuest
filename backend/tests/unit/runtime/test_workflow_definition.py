"""Workflow definitions: parsing, templates, conditions, trigger filters and validation (before anything runs)."""

import pytest

from patchquest.domain.effects import SideEffect
from patchquest.domain.workflows import (
    ActionInfo,
    DefinitionError,
    NodeType,
    TemplateError,
    ValidationPolicy,
    evaluate,
    matches,
    parse,
    references,
    render,
    to_dict,
    validate,
)

ACTIONS = {"github.comment": ActionInfo(SideEffect.EXTERNAL_WRITE), "slack.post": ActionInfo(SideEffect.EXTERNAL_WRITE),
           "log.note": ActionInfo(SideEffect.READ_ONLY)}


def flow(**overrides):
    base = {
        "name": "issue-to-comment",
        "trigger": {"type": "github.issues.labeled", "filter": {"payload.label": "agent-ready"}},
        "variables": {"repo": {"default": "/work/app"}},
        "nodes": [
            {"id": "fix", "type": "agent", "config": {"task": "Fix {{trigger.payload.title}}", "repo": "{{vars.repo}}"}},
            {"id": "ok", "type": "condition", "config": {"if": {"left": "{{nodes.fix.output.verdict}}", "op": "eq", "right": "passed"}}},
            {"id": "gate", "type": "approval", "config": {"message": "Post the result?", "timeout_s": 3600}},
            {"id": "post", "type": "action", "config": {"action": "github.comment", "params": {"body": "{{nodes.fix.output.status}}"}}},
            {"id": "done", "type": "end", "config": {}},
        ],
        "edges": [{"from": "fix", "to": "ok"}, {"from": "ok", "to": "gate", "when": "true"}, {"from": "ok", "to": "done", "when": "false"},
                  {"from": "gate", "to": "post", "when": "approved"}, {"from": "gate", "to": "done", "when": "denied"},
                  {"from": "post", "to": "done"}],
    }
    return {**base, **overrides}


def codes(raw, **policy):
    return {p.code for p in validate(parse(raw), ValidationPolicy(known_actions=ACTIONS, **policy))}


class TestParsing:
    def test_a_good_definition_parses_validates_and_round_trips(self):
        wf = parse(flow())
        assert validate(wf, ValidationPolicy(known_actions=ACTIONS)) == []
        assert [n.type for n in wf.nodes][:2] == [NodeType.AGENT, NodeType.CONDITION]
        assert parse(to_dict(wf)) == wf

    @pytest.mark.parametrize("mutate,expected", [
        (lambda r: r.pop("name"), "name"),
        (lambda r: r.update(name="bad/name"), "name"),
        (lambda r: r.update(extra=1), "unknown_field"),
        (lambda r: r.update(schema=99), "schema_version"),
        (lambda r: r.pop("trigger"), "trigger"),
        (lambda r: r.update(trigger={"type": ""}), "trigger"),
        (lambda r: r.update(nodes=[]), "nodes"),
        (lambda r: r["nodes"][0].update(type="shell"), "node_type"),
        (lambda r: r["nodes"][0].update(id="1bad"), "node_id"),
        (lambda r: r["nodes"][0]["config"].update(command="rm -rf /"), "unknown_config"),
        (lambda r: r["nodes"][0].update(max_visits=0), "max_visits"),
        (lambda r: r["edges"][0].update(when="maybe"), "edge_label"),
        (lambda r: r["edges"].append({"from": "x"}), "edge"),
        (lambda r: r.update(variables={"v": 3}), "variables"),
    ])
    def test_structural_errors_are_reported_with_stable_codes(self, mutate, expected):
        raw = flow()
        mutate(raw)
        with pytest.raises(DefinitionError) as err:
            parse(raw)
        assert expected in {p.code for p in err.value.problems}

    def test_all_problems_are_listed_together(self):
        with pytest.raises(DefinitionError) as err:
            parse({"name": "", "nodes": [{"id": "1", "type": "x"}]})
        assert len(err.value.problems) >= 3

    def test_size_limit(self):
        raw = flow(nodes=[{"id": f"n{i}", "type": "end"} for i in range(250)], edges=[])
        with pytest.raises(DefinitionError) as err:
            parse(raw)
        assert "too_many_nodes" in {p.code for p in err.value.problems}


class TestValidation:
    def test_dangling_duplicate_and_unreachable(self):
        raw = flow()
        raw["edges"].append({"from": "post", "to": "ghost"})
        assert "dangling_edge" in codes(raw)
        raw = flow()
        raw["nodes"].append({"id": "fix", "type": "end"})
        assert "duplicate_node" in codes(raw)
        raw = flow()
        raw["nodes"].append({"id": "orphan", "type": "end"})
        raw["edges"].append({"from": "orphan", "to": "fix"})  # nothing leads to orphan, but it is an entry: allowed
        assert "unreachable" not in codes(raw)

    def test_every_node_having_an_incoming_edge_means_nothing_starts_it(self):
        raw = flow()
        raw["edges"].append({"from": "post", "to": "fix"})
        assert "no_entry" in codes(raw)

    def test_conditions_need_both_branches_and_a_valid_comparison(self):
        raw = flow()
        raw["edges"] = [e for e in raw["edges"] if e.get("when") != "false"] + [{"from": "ok", "to": "done"}]
        assert "condition_edges" in codes(raw)
        raw = flow()
        raw["nodes"][1]["config"]["if"] = {"left": 1, "op": "regex", "right": "x"}
        assert "bad_condition" in codes(raw)
        raw = flow()
        raw["edges"].append({"from": "fix", "to": "done", "when": "true"})
        assert "edge_label" in codes(raw)

    def test_loops_must_be_bounded(self):
        raw = flow()
        raw["edges"].append({"from": "ok", "to": "fix", "when": "false"})
        raw["edges"] = [e for e in raw["edges"] if not (e["from"] == "ok" and e.get("when") == "false" and e["to"] == "done")]
        assert "unbounded_loop" in codes(raw)
        raw["nodes"][0]["max_visits"] = 3
        raw["nodes"][1]["max_visits"] = 3
        assert "unbounded_loop" not in codes(raw)

    def test_writes_outside_the_sandbox_need_a_human_on_every_path(self):
        raw = flow()
        raw["edges"] = [e for e in raw["edges"] if e["to"] != "gate"] + [{"from": "ok", "to": "post", "when": "true"}]
        assert "missing_approval" in codes(raw)
        assert "missing_approval" not in codes(raw, allow_unapproved_writes=True)  # an org may allow it explicitly

    def test_one_unguarded_path_is_enough_to_fail(self):
        raw = flow()
        raw["edges"].append({"from": "ok", "to": "post", "when": "false"})  # a second, ungated way in
        raw["edges"] = [e for e in raw["edges"] if not (e["from"] == "ok" and e["to"] == "done")]
        assert "missing_approval" in codes(raw)

    def test_read_only_actions_need_no_gate(self):
        raw = flow()
        raw["nodes"][3]["config"]["action"] = "log.note"
        raw["edges"] = [e for e in raw["edges"] if e["to"] != "gate"] + [{"from": "ok", "to": "post", "when": "true"}]
        assert "missing_approval" not in codes(raw)

    def test_unknown_actions_and_missing_config(self):
        raw = flow()
        raw["nodes"][3]["config"]["action"] = "teleport.cat"
        assert "unknown_action" in codes(raw)
        raw = flow()
        raw["nodes"][0]["config"].pop("task")
        assert "missing_config" in codes(raw)
        raw = flow()
        raw["nodes"][2]["config"]["timeout_s"] = -5
        assert "bad_config" in codes(raw)

    def test_literal_secrets_are_refused_anywhere(self):
        raw = flow()
        raw["nodes"][3]["config"]["params"] = {"token": "sk-" + "a" * 30}
        assert "literal_secret" in codes(raw)
        raw = flow()
        raw["variables"]["key"] = {"default": "ghp_" + "b" * 36}
        assert "literal_secret" in codes(raw)

    def test_references_must_resolve_to_something_that_exists_and_runs_earlier(self):
        raw = flow()
        raw["nodes"][3]["config"]["params"] = {"body": "{{nodes.done.output}}"}
        assert "bad_reference" in codes(raw)  # 'done' runs after 'post'
        raw = flow()
        raw["nodes"][3]["config"]["params"] = {"body": "{{nodes.nope.output}}"}
        assert "bad_reference" in codes(raw)
        raw = flow()
        raw["nodes"][3]["config"]["params"] = {"body": "{{env.HOME}}"}
        assert "bad_reference" in codes(raw)
        raw = flow()
        raw["nodes"][0]["config"]["repo"] = "{{vars.undeclared}}"
        assert "unknown_variable" in codes(raw)

    def test_timers_and_waits_and_ends(self):
        raw = flow()
        raw["nodes"].append({"id": "t", "type": "timer", "config": {"seconds": 0}})
        raw["edges"].append({"from": "post", "to": "t"})
        assert "bad_config" in codes(raw)
        raw = flow()
        raw["nodes"].append({"id": "w", "type": "wait_event", "config": {}})
        raw["edges"].append({"from": "post", "to": "w"})
        assert "missing_config" in codes(raw)
        raw = flow()
        raw["edges"].append({"from": "done", "to": "post"})
        assert "end_has_edges" in codes(raw)


class TestTemplates:
    CTX = {"trigger": {"payload": {"title": "Crash on save", "n": 3, "labels": ["bug", "p1"]}}, "vars": {"repo": "/w"},
           "nodes": {"fix": {"output": {"verdict": "passed", "files": ["a.py"]}}}}

    def test_substitution_keeps_types_for_whole_placeholders_and_text_otherwise(self):
        assert render("{{trigger.payload.n}}", self.CTX) == 3
        assert render("Fix: {{trigger.payload.title}} ({{trigger.payload.n}})", self.CTX) == "Fix: Crash on save (3)"
        assert render({"a": ["{{vars.repo}}", 1]}, self.CTX) == {"a": ["/w", 1]}
        assert render("{{ trigger.payload.labels.1 }}", self.CTX) == "p1"

    def test_missing_paths_are_errors_not_empty_strings(self):
        with pytest.raises(TemplateError, match=r"trigger\.payload\.nope"):
            render("{{trigger.payload.nope}}", self.CTX)
        with pytest.raises(TemplateError):
            render("x {{trigger.payload.labels.9}}", self.CTX)

    def test_there_is_no_expression_evaluation(self):
        assert render("{{__import__('os').system('id')}}", self.CTX) == "{{__import__('os').system('id')}}"  # not even a path: left alone
        with pytest.raises(TemplateError):
            render("{{trigger.__class__}}", self.CTX)

    def test_references_are_collected_from_nested_config(self):
        assert references({"a": ["{{vars.x}}", {"b": "t {{nodes.n.output}}"}]}) == {"vars.x", "nodes.n.output"}


class TestConditions:
    CTX = TestTemplates.CTX

    @pytest.mark.parametrize("cond,expected", [
        ({"left": "{{nodes.fix.output.verdict}}", "op": "eq", "right": "passed"}, True),
        ({"left": "{{nodes.fix.output.verdict}}", "op": "ne", "right": "passed"}, False),
        ({"left": "{{trigger.payload.n}}", "op": "gt", "right": 2}, True),
        ({"left": "{{trigger.payload.n}}", "op": "le", "right": 2}, False),
        ({"left": "bug", "op": "in", "right": "{{trigger.payload.labels}}"}, True),
        ({"left": "{{trigger.payload.title}}", "op": "contains", "right": "save"}, True),
        ({"left": "{{trigger.payload.title}}", "op": "exists"}, True),
        ({"left": "{{trigger.payload.nothing}}", "op": "exists"}, False),
        ({"all": [{"left": 1, "op": "eq", "right": 1}, {"left": 2, "op": "gt", "right": 1}]}, True),
        ({"any": [{"left": 1, "op": "eq", "right": 2}, {"left": 2, "op": "gt", "right": 1}]}, True),
        ({"not": {"left": 1, "op": "eq", "right": 1}}, False),
    ])
    def test_comparisons(self, cond, expected):
        assert evaluate(cond, self.CTX) is expected

    def test_type_mismatch_and_unknown_operator_are_errors(self):
        with pytest.raises(TemplateError, match="cannot compare"):
            evaluate({"left": "{{trigger.payload.title}}", "op": "gt", "right": 3}, self.CTX)
        with pytest.raises(TemplateError):
            evaluate({"left": 1, "op": "regex", "right": "x"}, self.CTX)


class TestTriggerFilters:
    EVENT = {"type": "issue.labeled", "payload": {"label": "agent-ready", "repo": "acme/app", "author": "ana"}}

    @pytest.mark.parametrize("flt,expected", [
        ({}, True), ({"payload.label": "agent-ready"}, True), ({"payload.label": "other"}, False),
        ({"payload.repo": {"in": ["acme/app", "acme/lib"]}}, True), ({"payload.repo": {"in": ["x/y"]}}, False),
        ({"payload.missing": "x"}, False), ({"payload.author": {"exists": True}}, True), ({"payload.nope": {"exists": True}}, False),
        ({"payload.label": {"regex": ".*"}}, False),  # unknown operators never match: no eval, no surprises
    ])
    def test_matching(self, flt, expected):
        assert matches(flt, self.EVENT) is expected


class TestLayout:
    def test_positions_round_trip_and_are_ignored_by_validation(self):
        raw = flow()
        raw["layout"] = {"fix": {"x": 10, "y": 20.5}, "gate": {"x": -40, "y": 300}}
        wf = parse(raw)
        assert wf.layout["fix"] == {"x": 10.0, "y": 20.5}
        assert to_dict(wf)["layout"] == {"fix": {"x": 10.0, "y": 20.5}, "gate": {"x": -40.0, "y": 300.0}}
        assert validate(wf, ValidationPolicy(known_actions=ACTIONS)) == []
        assert "layout" not in to_dict(parse(flow()))  # absent when there is none

    @pytest.mark.parametrize("layout", [
        {"ghost": {"x": 1, "y": 2}}, {"fix": {"x": 1}}, {"fix": {"x": "1", "y": 2}}, {"fix": {"x": 1, "y": 2, "z": 3}},
        {"fix": {"x": 10**9, "y": 0}}, {"fix": {"x": True, "y": 0}}, {"fix": [1, 2]}, [("fix", 1)]])
    def test_bad_layouts_are_rejected(self, layout):
        raw = flow()
        raw["layout"] = layout
        with pytest.raises(DefinitionError) as err:
            parse(raw)
        assert "layout" in {p.code for p in err.value.problems}
