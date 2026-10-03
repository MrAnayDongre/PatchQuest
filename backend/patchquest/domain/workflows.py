"""The workflow definition: a small, closed, versioned graph that is validated before it ever runs.

Deliberately not a programming language. Nodes do one of seven things, edges say what follows, conditions
compare values, and templates substitute ``{{dotted.paths}}`` - there is no expression evaluation anywhere,
so a workflow (including one a user drew in a UI, or imported) can only do what its node types allow, and
what policy permits is checked at validation time and again at runtime.

Node types
  agent       run a PatchQuest agent (a full run) and wait for it durably
  action      perform a connector action (comment, open a PR, notify ...)
  condition   branch on a structured comparison
  approval    stop for a person's go/no-go (optionally with a timeout that means "denied")
  wait_event  wait, durably, for an external event
  timer       wait, durably, for a duration
  end         finish the workflow
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from patchquest.domain.effects import SideEffect
from patchquest.tools.secret_guard import has_secrets

SCHEMA_VERSION = 1
MAX_NODES = 200
MAX_VISITS_LIMIT = 20
MAX_COORDINATE = 100_000.0


class NodeType(StrEnum):
    AGENT = "agent"
    ACTION = "action"
    CONDITION = "condition"
    APPROVAL = "approval"
    WAIT_EVENT = "wait_event"
    TIMER = "timer"
    END = "end"


# What each node type may carry in its ``config`` (anything else is rejected, so typos fail loudly).
ALLOWED_CONFIG: dict[NodeType, frozenset[str]] = {
    NodeType.AGENT: frozenset({"task", "repo", "provider", "model", "base_url", "overrides", "on_failure"}),
    NodeType.ACTION: frozenset({"action", "params", "on_failure"}),
    NodeType.CONDITION: frozenset({"if"}),
    NodeType.APPROVAL: frozenset({"message", "timeout_s"}),
    NodeType.WAIT_EVENT: frozenset({"event", "filter", "timeout_s"}),
    NodeType.TIMER: frozenset({"seconds"}),
    NodeType.END: frozenset({"result"}),
}
REQUIRES_APPROVAL_EFFECTS = frozenset({SideEffect.EXTERNAL_WRITE, SideEffect.DESTRUCTIVE, SideEffect.HOST_MUTATION,
                                       SideEffect.REPOSITORY_WRITE, SideEffect.UNKNOWN})
EDGE_LABELS = frozenset({"true", "false", "approved", "denied", "timeout", "failed"})
OPERATORS = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "in", "contains", "exists"})
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")


@dataclass(frozen=True)
class Node:
    id: str
    type: NodeType
    config: Mapping[str, Any] = field(default_factory=dict)
    max_visits: int = 1  # how many times a run may enter this node (retry loops raise it, explicitly)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    when: str | None = None  # None: always; else one of EDGE_LABELS


@dataclass(frozen=True)
class Trigger:
    type: str  # e.g. "manual", "github.issues.labeled"
    filter: Mapping[str, Any] = field(default_factory=dict)  # dotted path -> value | {"in": [...]}


@dataclass(frozen=True)
class Workflow:
    name: str
    trigger: Trigger
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    variables: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # name -> {"default": ..., "required": bool}
    description: str = ""
    schema: int = SCHEMA_VERSION
    # Editor-only: where each node sits on the canvas. Never read by the engine or the validator's graph rules.
    layout: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)

    def outgoing(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def incoming(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.target == node_id]

    def entries(self) -> list[Node]:
        targets = {e.target for e in self.edges}
        return [n for n in self.nodes if n.id not in targets]


@dataclass(frozen=True)
class Problem:
    code: str  # stable, machine-readable
    message: str  # for the person fixing the workflow
    node: str | None = None


class DefinitionError(ValueError):
    def __init__(self, problems: list[Problem]) -> None:
        super().__init__("; ".join(p.message for p in problems))
        self.problems = problems


# ------------------------------------------------------------------ parsing
def parse(raw: Mapping[str, Any]) -> Workflow:
    """Build a ``Workflow`` from plain data (JSON/YAML). Raises ``DefinitionError`` listing every structural problem."""
    problems: list[Problem] = []

    def bad(code: str, message: str, node: str | None = None) -> None:
        problems.append(Problem(code, message, node))

    unknown = set(raw) - {"schema", "name", "description", "trigger", "nodes", "edges", "variables", "layout"}
    if unknown:
        bad("unknown_field", f"unknown top-level field(s): {', '.join(sorted(unknown))}")
    if raw.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION:
        bad("schema_version", f"unsupported workflow schema {raw.get('schema')!r} (this PatchQuest reads {SCHEMA_VERSION})")
    name = raw.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}", name or ""):
        bad("name", "name must be 1-64 characters: letters, digits, spaces, '_', '.', '-'")

    trigger_raw = raw.get("trigger")
    trigger = Trigger("manual")
    if not isinstance(trigger_raw, Mapping) or not isinstance(trigger_raw.get("type"), str) or not trigger_raw.get("type"):
        bad("trigger", "a trigger with a type is required (use 'manual' to start it by hand)")
    else:
        extra = set(trigger_raw) - {"type", "filter"}
        if extra:
            bad("unknown_field", f"unknown trigger field(s): {', '.join(sorted(extra))}")
        flt = trigger_raw.get("filter") or {}
        if not isinstance(flt, Mapping) or not all(isinstance(k, str) for k in flt):
            bad("trigger_filter", "trigger filter must be a mapping of dotted paths to values")
            flt = {}
        trigger = Trigger(trigger_raw["type"], dict(flt))

    nodes: list[Node] = []
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        bad("nodes", "a workflow needs at least one node")
        nodes_raw = []
    if len(nodes_raw) > MAX_NODES:
        bad("too_many_nodes", f"at most {MAX_NODES} nodes")
    for item in nodes_raw[:MAX_NODES]:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", item["id"]):
            bad("node_id", "every node needs an id: a letter then letters, digits or '_' (max 41)")
            continue
        node_id = item["id"]
        try:
            node_type = NodeType(str(item.get("type")))
        except ValueError:
            bad("node_type", f"unknown node type {item.get('type')!r}; use one of {', '.join(t.value for t in NodeType)}", node_id)
            continue
        extra = set(item) - {"id", "type", "config", "max_visits"}
        if extra:
            bad("unknown_field", f"unknown field(s) on node {node_id}: {', '.join(sorted(extra))}", node_id)
        config = item.get("config") or {}
        if not isinstance(config, Mapping):
            bad("config", f"node {node_id}: config must be a mapping", node_id)
            config = {}
        stray = set(config) - ALLOWED_CONFIG[node_type]
        if stray:
            bad("unknown_config", f"node {node_id} ({node_type.value}) does not take: {', '.join(sorted(stray))}", node_id)
        visits = item.get("max_visits", 1)
        if not isinstance(visits, int) or isinstance(visits, bool) or not 1 <= visits <= MAX_VISITS_LIMIT:
            bad("max_visits", f"node {node_id}: max_visits must be 1-{MAX_VISITS_LIMIT}", node_id)
            visits = 1
        nodes.append(Node(node_id, node_type, dict(config), visits))

    edges: list[Edge] = []
    for item in raw.get("edges") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("from"), str) or not isinstance(item.get("to"), str):
            bad("edge", "every edge needs 'from' and 'to' node ids")
            continue
        when = item.get("when")
        if when is not None and when not in EDGE_LABELS:
            bad("edge_label", f"edge {item['from']}->{item['to']}: 'when' must be one of {', '.join(sorted(EDGE_LABELS))}")
            when = None
        if set(item) - {"from", "to", "when"}:
            bad("unknown_field", f"edge {item['from']}->{item['to']} has unknown field(s)")
        edges.append(Edge(item["from"], item["to"], when))

    variables = raw.get("variables") or {}
    if not isinstance(variables, Mapping) or not all(isinstance(k, str) and isinstance(v, Mapping) for k, v in variables.items()):
        bad("variables", "variables must map a name to {default, required}")
        variables = {}
    layout_raw = raw.get("layout") or {}
    layout: dict[str, dict[str, float]] = {}
    known_ids = {n.id for n in nodes}
    if not isinstance(layout_raw, Mapping):
        bad("layout", "layout must map a node id to {x, y}")
    else:
        for node_id, pos in layout_raw.items():
            ok = (isinstance(pos, Mapping) and set(pos) <= {"x", "y"} and set(pos) == {"x", "y"}
                  and all(isinstance(pos[k], int | float) and not isinstance(pos[k], bool) and abs(pos[k]) <= MAX_COORDINATE for k in ("x", "y")))
            if node_id not in known_ids or not ok:
                bad("layout", f"layout entry '{node_id}' must be for an existing node with numeric x and y within +/-{MAX_COORDINATE:g}")
            else:
                layout[node_id] = {"x": float(pos["x"]), "y": float(pos["y"])}
    if problems:
        raise DefinitionError(problems)
    return Workflow(name=name, trigger=trigger, nodes=tuple(nodes), edges=tuple(edges),  # type: ignore[arg-type]
                    variables={k: dict(v) for k, v in variables.items()}, description=str(raw.get("description") or ""), layout=layout)


def to_dict(wf: Workflow) -> dict[str, Any]:
    """Canonical plain-data form (what is stored, exported and shown). The inverse of ``parse``."""
    return {
        "schema": wf.schema, "name": wf.name, "description": wf.description,
        "trigger": {"type": wf.trigger.type, "filter": dict(wf.trigger.filter)},
        "nodes": [{"id": n.id, "type": n.type.value, "config": dict(n.config),
                   **({"max_visits": n.max_visits} if n.max_visits != 1 else {})} for n in wf.nodes],
        "edges": [{"from": e.source, "to": e.target, **({"when": e.when} if e.when else {})} for e in wf.edges],
        "variables": {k: dict(v) for k, v in wf.variables.items()},
        **({"layout": {k: dict(v) for k, v in wf.layout.items()}} if wf.layout else {}),
    }


# ------------------------------------------------------------------ templates and conditions
class TemplateError(ValueError):
    pass


def lookup(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise TemplateError(f"'{path}' is not available here")
    return current


def references(value: Any) -> set[str]:
    """Every ``{{path}}`` mentioned anywhere inside ``value`` (strings, lists, mappings)."""
    if isinstance(value, str):
        return set(_TEMPLATE.findall(value))
    if isinstance(value, Mapping):
        return set().union(*(references(v) for v in value.values())) if value else set()
    if isinstance(value, list | tuple):
        return set().union(*(references(v) for v in value)) if value else set()
    return set()


def render(value: Any, context: Mapping[str, Any]) -> Any:
    """Substitute ``{{paths}}``. A string that is exactly one placeholder keeps the value's type; otherwise text."""
    if isinstance(value, str):
        whole = _TEMPLATE.fullmatch(value.strip())
        if whole:
            return lookup(context, whole.group(1))
        return _TEMPLATE.sub(lambda m: str(lookup(context, m.group(1))), value)
    if isinstance(value, Mapping):
        return {k: render(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, context) for v in value]
    return value


def evaluate(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """``{"left": x, "op": "eq", "right": y}`` or ``{"all": [...]}`` / ``{"any": [...]}`` / ``{"not": c}``."""
    if "all" in condition:
        return all(evaluate(c, context) for c in condition["all"])
    if "any" in condition:
        return any(evaluate(c, context) for c in condition["any"])
    if "not" in condition:
        return not evaluate(condition["not"], context)
    op = condition.get("op")
    if op not in OPERATORS:
        raise TemplateError(f"unknown comparison '{op}'")
    if op == "exists":
        try:
            lookup(context, str(condition.get("left", "")).strip("{} "))
            return True
        except TemplateError:
            return False
    left, right = render(condition.get("left"), context), render(condition.get("right"), context)
    try:
        if op == "eq":
            return bool(left == right)
        if op == "ne":
            return bool(left != right)
        if op == "in":
            return left in right
        if op == "contains":
            return right in left
        return {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[op]
    except TypeError as exc:
        raise TemplateError(f"cannot compare {left!r} and {right!r}: {exc}") from None


def condition_references(condition: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("all", "any"):
        for c in condition.get(key, []):
            refs |= condition_references(c)
    if "not" in condition:
        refs |= condition_references(condition["not"])
    refs |= references(condition.get("left")) | references(condition.get("right"))
    return refs


def matches(filter_: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    """Structured trigger filter: every dotted path must equal its value, or be in ``{"in": [...]}``. No eval."""
    for path, expected in filter_.items():
        try:
            actual = lookup(event, path)
        except TemplateError:
            return False
        if isinstance(expected, Mapping):
            if "in" in expected:
                if actual not in expected["in"]:
                    return False
            elif "exists" in expected:
                pass  # reaching here means the path exists
            else:
                return False
        elif actual != expected:
            return False
    return True


# ------------------------------------------------------------------ validation
@dataclass(frozen=True)
class ActionInfo:
    side_effect: SideEffect
    idempotent: bool = True


@dataclass(frozen=True)
class ValidationPolicy:
    allow_unapproved_writes: bool = False  # an org may let trusted workflows act without a human gate
    known_actions: Mapping[str, ActionInfo] | None = None  # None: do not check action names (e.g. while drafting)
    secret_exists: Callable[[str], bool] | None = None


def validate(wf: Workflow, policy: ValidationPolicy | None = None) -> list[Problem]:
    """Everything that can be wrong with a workflow *before* it runs. Empty list means it may be saved and run."""
    policy = policy or ValidationPolicy()
    problems: list[Problem] = []
    ids = [n.id for n in wf.nodes]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(Problem("duplicate_node", f"node id '{dup}' is used more than once", dup))
    known = set(ids)
    for e in wf.edges:
        for end in (e.source, e.target):
            if end not in known:
                problems.append(Problem("dangling_edge", f"edge {e.source}->{e.target} refers to missing node '{end}'", end))
    if problems:
        return problems  # the structural checks below assume a well-formed graph

    entries = wf.entries()
    if not entries:
        problems.append(Problem("no_entry", "every node has an incoming edge, so nothing starts the workflow"))
    reachable = _reachable(wf, [n.id for n in entries])
    for n in wf.nodes:
        if n.id not in reachable:
            problems.append(Problem("unreachable", f"node '{n.id}' can never run", n.id))
    if not any(n.type is NodeType.END for n in wf.nodes) and not _has_terminal(wf):
        problems.append(Problem("no_end", "the workflow has no end node and no node without outgoing edges"))

    for cycle_node in _nodes_on_cycles(wf):
        if wf.node(cycle_node).max_visits < 2:
            problems.append(Problem("unbounded_loop", f"node '{cycle_node}' is on a loop; raise its max_visits so the loop is bounded", cycle_node))

    upstream_ok = {n.id: _ancestors(wf, n.id) for n in wf.nodes}
    for n in wf.nodes:
        problems += _check_node(wf, n, policy, upstream_ok[n.id])
    problems += _check_approval_gates(wf, policy)
    for name, spec in wf.variables.items():
        if wf.trigger.type != "manual" and spec.get("required") and "default" not in spec:
            problems.append(Problem("unbound_variable", f"variable '{name}' is required but an event cannot supply it; "
                                    "give it a default (only manually started workflows may require values at start)"))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            problems.append(Problem("variable_name", f"variable name '{name}' is not a plain identifier"))
        if "default" in spec and has_secrets(str(spec["default"])):
            problems.append(Problem("literal_secret", f"variable '{name}' holds what looks like a secret; reference a secret by id instead"))
    return problems


def _reachable(wf: Workflow, starts: list[str]) -> set[str]:
    seen, stack = set(), list(starts)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(e.target for e in wf.outgoing(cur))
    return seen


def _ancestors(wf: Workflow, node_id: str) -> set[str]:
    seen, stack = set(), [e.source for e in wf.incoming(node_id)]
    while stack:
        cur = stack.pop()
        if cur not in seen:
            seen.add(cur)
            stack.extend(e.source for e in wf.incoming(cur))
    return seen


def _has_terminal(wf: Workflow) -> bool:
    return any(not wf.outgoing(n.id) for n in wf.nodes)


def _nodes_on_cycles(wf: Workflow) -> set[str]:
    on_cycle = set()
    for n in wf.nodes:
        stack, seen = [e.target for e in wf.outgoing(n.id)], set()
        while stack:
            cur = stack.pop()
            if cur == n.id:
                on_cycle.add(n.id)
                break
            if cur not in seen:
                seen.add(cur)
                stack.extend(e.target for e in wf.outgoing(cur))
    return on_cycle


def _check_node(wf: Workflow, n: Node, policy: ValidationPolicy, ancestors: set[str]) -> list[Problem]:
    out: list[Problem] = []
    cfg = n.config

    def bad(code: str, msg: str) -> None:
        out.append(Problem(code, f"node '{n.id}': {msg}", n.id))

    if has_secrets(repr(dict(cfg))):
        bad("literal_secret", "contains what looks like a secret; reference a stored secret by id instead of writing it in")
    outgoing = wf.outgoing(n.id)
    if n.type is NodeType.AGENT and not cfg.get("task"):
        bad("missing_config", "an agent node needs a 'task'")
    if n.type is NodeType.ACTION:
        name = cfg.get("action")
        if not name:
            bad("missing_config", "an action node needs an 'action'")
        elif policy.known_actions is not None and name not in policy.known_actions:
            bad("unknown_action", f"unknown action '{name}'")
    if n.type is NodeType.CONDITION:
        if not isinstance(cfg.get("if"), Mapping):
            bad("missing_config", "a condition node needs an 'if' comparison")
        else:
            try:
                _validate_condition(cfg["if"])
            except ValueError as exc:
                bad("bad_condition", str(exc))
        labels = {e.when for e in outgoing}
        if not labels & {"true"} or not labels & {"false"}:
            bad("condition_edges", "a condition needs both a 'true' and a 'false' edge")
    elif any(e.when in ("true", "false") for e in outgoing):
        bad("edge_label", "'true'/'false' edges only make sense after a condition")
    if n.type is NodeType.APPROVAL:
        labels = {e.when for e in outgoing}
        if labels - {None, "approved", "denied", "timeout"}:
            bad("edge_label", "an approval's edges may be unlabelled, 'approved', 'denied' or 'timeout'")
        timeout = cfg.get("timeout_s")
        if timeout is not None and (not isinstance(timeout, int | float) or timeout <= 0):
            bad("bad_config", "timeout_s must be a positive number")
    if n.type is NodeType.TIMER:
        seconds = cfg.get("seconds")
        if not isinstance(seconds, int | float) or isinstance(seconds, bool) or not 0 < seconds <= 90 * 86400:
            bad("bad_config", "a timer needs 'seconds' between 1 and 90 days")
    if n.type is NodeType.WAIT_EVENT and not cfg.get("event"):
        bad("missing_config", "a wait_event node needs the 'event' type to wait for")
    if n.type is NodeType.END:
        if outgoing:
            bad("end_has_edges", "an end node cannot have outgoing edges")
        if cfg.get("result", "success") not in ("success", "failure"):
            bad("bad_config", "end result must be 'success' or 'failure'")

    allowed_roots = {"trigger", "vars", "nodes"}
    for ref in references(dict(cfg)) | (condition_references(cfg["if"]) if isinstance(cfg.get("if"), Mapping) else set()):
        root, _, rest = ref.partition(".")
        if root not in allowed_roots:
            bad("bad_reference", f"'{{{{{ref}}}}}' must start with trigger., vars. or nodes.")
        elif root == "vars" and rest.split(".")[0] not in wf.variables:
            bad("unknown_variable", f"'{{{{{ref}}}}}' uses a variable that is not declared")
        elif root == "nodes":
            target = rest.split(".")[0]
            if target not in {x.id for x in wf.nodes}:
                bad("bad_reference", f"'{{{{{ref}}}}}' refers to a node that does not exist")
            elif target not in ancestors:
                bad("bad_reference", f"'{{{{{ref}}}}}' refers to '{target}', which does not run before this node")
    return out


def _validate_condition(cond: Mapping[str, Any]) -> None:
    for key in ("all", "any"):
        if key in cond:
            if not isinstance(cond[key], list) or not cond[key]:
                raise ValueError(f"'{key}' needs a non-empty list of comparisons")
            for sub in cond[key]:
                _validate_condition(sub)
            return
    if "not" in cond:
        _validate_condition(cond["not"])
        return
    if cond.get("op") not in OPERATORS:
        raise ValueError(f"comparison needs 'op' in {', '.join(sorted(OPERATORS))}")
    if "left" not in cond or (cond["op"] != "exists" and "right" not in cond):
        raise ValueError("comparison needs 'left' and 'right'")


def _check_approval_gates(wf: Workflow, policy: ValidationPolicy) -> list[Problem]:
    """Any action that writes outside the sandbox must sit behind an approval node on EVERY path to it."""
    if policy.allow_unapproved_writes or policy.known_actions is None:
        return []
    out: list[Problem] = []
    for n in wf.nodes:
        if n.type is not NodeType.ACTION:
            continue
        info = policy.known_actions.get(str(n.config.get("action")))
        if info is None or info.side_effect not in REQUIRES_APPROVAL_EFFECTS:
            continue
        if _reachable_avoiding_approval(wf, n.id):
            out.append(Problem("missing_approval", f"node '{n.id}' performs {info.side_effect.value} "
                               f"('{n.config.get('action')}') but a path reaches it without a human approval", n.id))
    return out


def _reachable_avoiding_approval(wf: Workflow, target: str) -> bool:
    """True if some path from an entry reaches ``target`` without passing through an approval node."""
    seen: set[str] = set()
    stack = [n.id for n in wf.entries()]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if cur == target:
            return True
        if wf.node(cur).type is NodeType.APPROVAL:
            continue  # the gate: nothing past it counts as unapproved
        stack.extend(e.target for e in wf.outgoing(cur))
    return False


def cycle_nodes(wf: Workflow) -> set[str]:
    """Nodes that lie on a loop (used at runtime to tell a loop that ran out of retries from a harmless re-arrival)."""
    return _nodes_on_cycles(wf)
