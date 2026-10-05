import { labelOptions } from './edgeRules'
import { NODE_ID_RE } from './model'
import { ancestorsOf, referencesIn } from './refs'
import { asRecord, isNodeType, type Problem, type WfDef } from './types'

export interface ActionMeta {
  name: string
  side_effect: string
  idempotent: boolean
  requires_approval: boolean
  connector: string | null
}

/**
 * Cheap structural checks that run on every edit for instant feedback. The server's verdict (/validate) is
 * authoritative; these only cover what can be decided without it and never claim a workflow is valid.
 */
export function clientCheck(def: WfDef, actions?: ActionMeta[]): Problem[] {
  const out: Problem[] = []
  const add = (code: string, message: string, node?: string) => out.push({ code, message, node: node ?? null, source: 'local' })

  if (typeof def.name !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}$/.test(def.name)) {
    add('name', 'Give the workflow a name of 1 to 64 letters, digits, spaces, "_", "." or "-".')
  }
  if (!def.trigger || typeof def.trigger.type !== 'string' || !def.trigger.type) add('trigger', 'Choose how this workflow starts.')
  if (!def.nodes.length) {
    add('nodes', 'Add at least one step.')
    return out
  }

  const ids = def.nodes.map(n => n.id)
  const known = new Set(ids)
  for (const dup of new Set(ids.filter((i, k) => ids.indexOf(i) !== k))) add('duplicate_node', `Two steps are called "${dup}".`, dup)
  for (const n of def.nodes) {
    if (!NODE_ID_RE.test(String(n.id))) add('node_id', `"${n.id}" isn't a valid step name.`, n.id)
    if (!isNodeType(n.type)) add('node_type', `Step "${n.id}" has an unknown type "${n.type}".`, n.id)
  }
  for (const e of def.edges) {
    for (const end of [e.from, e.to]) if (!known.has(end)) add('dangling_edge', `A connection points at "${end}", which doesn't exist.`, end)
  }
  if (out.some(p => p.code === 'dangling_edge' || p.code === 'duplicate_node')) return out

  const incoming = new Set(def.edges.map(e => e.to))
  const entries = def.nodes.filter(n => !incoming.has(n.id))
  if (!entries.length) add('no_entry', 'Every step has something leading into it, so nothing starts the workflow.')
  const reach = new Set<string>()
  const stack = entries.map(n => n.id)
  while (stack.length) {
    const cur = stack.pop()!
    if (reach.has(cur)) continue
    reach.add(cur)
    for (const e of def.edges) if (e.from === cur) stack.push(e.to)
  }
  for (const n of def.nodes) {
    if (!reach.has(n.id)) add('unreachable', `"${n.id}" can never run because nothing reaches it.`, n.id)
  }

  const actionMap = new Map((actions ?? []).map(a => [a.name, a]))
  for (const n of def.nodes) {
    const cfg = asRecord(n.config)
    const outgoing = def.edges.filter(e => e.from === n.id)
    const allowed = labelOptions(n).map(o => o.value)
    for (const e of outgoing) {
      if (n.type !== 'end' && !allowed.includes(e.when ?? null)) add('edge_label', `"${n.id}" can't have a "${e.when}" connection.`, n.id)
    }
    if (n.type === 'agent' && !cfg.task) add('missing_config', `"${n.id}" needs a task for the agent.`, n.id)
    if (n.type === 'action') {
      if (!cfg.action) add('missing_config', `"${n.id}" needs an action.`, n.id)
      else if (actions && !actionMap.has(String(cfg.action))) add('unknown_action', `"${cfg.action}" isn't an available action.`, n.id)
    }
    if (n.type === 'condition') {
      if (!cfg.if) add('missing_config', `"${n.id}" needs a condition.`, n.id)
      const labels = new Set(outgoing.map(e => e.when))
      if (!labels.has('true') || !labels.has('false')) add('condition_edges', `"${n.id}" needs both an "If true" and an "If false" connection.`, n.id)
    }
    if (n.type === 'wait_event' && !cfg.event) add('missing_config', `"${n.id}" needs the event to wait for.`, n.id)
    if (n.type === 'timer') {
      const s = cfg.seconds
      if (typeof s !== 'number' || !(s > 0 && s <= 90 * 86400)) add('bad_config', `"${n.id}" needs a wait of 1 second to 90 days.`, n.id)
    }
    if (n.type === 'end' && outgoing.length) add('end_has_edges', `"${n.id}" finishes the workflow, so nothing can follow it.`, n.id)
    if (n.max_visits !== undefined && (!Number.isInteger(n.max_visits) || n.max_visits < 1 || n.max_visits > 20)) {
      add('max_visits', `"${n.id}": repeat limit must be between 1 and 20.`, n.id)
    }
    const ancestors = ancestorsOf(def, n.id)
    for (const ref of new Set(referencesIn(cfg))) {
      const [root, second] = ref.split('.')
      if (root === 'vars' && !(second in asRecord(def.variables))) add('unknown_variable', `"${n.id}" uses variable "${second}", which isn't declared.`, n.id)
      else if (root === 'nodes' && !ancestors.has(second)) add('bad_reference', `"${n.id}" uses "${second}", which doesn't run before it.`, n.id)
      else if (root !== 'vars' && root !== 'nodes' && root !== 'trigger') add('bad_reference', `"{{${ref}}}" must start with trigger., vars. or nodes.`, n.id)
    }
  }

  // cycles need a repeat limit
  for (const n of def.nodes) {
    const seen = new Set<string>()
    const st = def.edges.filter(e => e.from === n.id).map(e => e.to)
    let onCycle = false
    while (st.length) {
      const cur = st.pop()!
      if (cur === n.id) {
        onCycle = true
        break
      }
      if (seen.has(cur)) continue
      seen.add(cur)
      for (const e of def.edges) if (e.from === cur) st.push(e.to)
    }
    if (onCycle && (n.max_visits ?? 1) < 2) add('unbounded_loop', `"${n.id}" is part of a loop. Raise its repeat limit so the loop ends.`, n.id)
  }

  if (actions) {
    for (const n of def.nodes) {
      if (n.type !== 'action') continue
      const meta = actionMap.get(String(asRecord(n.config).action))
      if (meta?.requires_approval && reachesWithoutApproval(def, n.id, entries.map(e => e.id))) {
        add('missing_approval', `"${n.id}" ${describeEffect(meta)}, so a person must approve before it. Put an approval step on every path to it.`, n.id)
      }
    }
  }
  return out
}

function describeEffect(meta: ActionMeta): string {
  return meta.connector ? `acts on ${meta.connector}` : 'changes something outside PatchQuest'
}

function reachesWithoutApproval(def: WfDef, target: string, starts: string[]): boolean {
  const seen = new Set<string>()
  const stack = [...starts]
  while (stack.length) {
    const cur = stack.pop()!
    if (seen.has(cur)) continue
    seen.add(cur)
    if (cur === target) return true
    if (def.nodes.find(n => n.id === cur)?.type === 'approval') continue
    for (const e of def.edges) if (e.from === cur) stack.push(e.to)
  }
  return false
}

export function problemsByNode(problems: Problem[]): Map<string, Problem[]> {
  const m = new Map<string, Problem[]>()
  for (const p of problems) {
    if (!p.node) continue
    m.set(p.node, [...(m.get(p.node) ?? []), p])
  }
  return m
}

/** Merge: if the server answered use only its verdict (it is authoritative); otherwise show the local hints. */
export function mergeProblems(server: Problem[] | null, local: Problem[]): Problem[] {
  return server !== null ? server.map(p => ({ ...p, source: 'server' as const })) : local
}
