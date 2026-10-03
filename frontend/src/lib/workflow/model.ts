import { canConnect, type Verdict } from './edgeRules'
import { asRecord, clone, NODE_TYPES, type NodeType, type WfDef, type WfEdge, type WfNode, type WfVariable } from './types'

export const NODE_ID_RE = /^[A-Za-z][A-Za-z0-9_]{0,40}$/

export function emptyDefinition(name = 'new-workflow'): WfDef {
  return { schema: 1, name, description: '', trigger: { type: 'manual', filter: {} }, variables: {}, nodes: [], edges: [] }
}

/** Fill the arrays a partially-formed import may lack, without touching anything else. */
export function normalize(raw: unknown): WfDef {
  const o = asRecord(raw)
  return {
    ...o,
    nodes: Array.isArray(o.nodes) ? (o.nodes as WfNode[]) : [],
    edges: Array.isArray(o.edges) ? (o.edges as WfEdge[]) : [],
  }
}

export function defaultConfig(type: NodeType): Record<string, unknown> {
  switch (type) {
    case 'agent': return { task: '' }
    case 'action': return { action: '', params: {} }
    case 'condition': return { if: { left: '', op: 'eq', right: '' } }
    case 'approval': return { message: '' }
    case 'wait_event': return { event: '' }
    case 'timer': return { seconds: 60 }
    case 'end': return { result: 'success' }
  }
}

export const TYPE_ID_BASE: Record<NodeType, string> = {
  agent: 'agent', action: 'action', condition: 'check', approval: 'approve', wait_event: 'wait', timer: 'timer', end: 'done',
}

export function uniqueId(def: WfDef, base: string): string {
  const clean = base.replace(/[^A-Za-z0-9_]/g, '_').replace(/^[^A-Za-z]+/, 'n') || 'node'
  const taken = new Set(def.nodes.map(n => n.id))
  if (!taken.has(clean)) return clean.slice(0, 41)
  for (let i = 2; ; i++) {
    const id = `${clean.slice(0, 38)}_${i}`
    if (!taken.has(id)) return id
  }
}

export function addNode(def: WfDef, type: NodeType, id?: string): { def: WfDef; id: string } {
  const nid = id && NODE_ID_RE.test(id) && !def.nodes.some(n => n.id === id) ? id : uniqueId(def, TYPE_ID_BASE[type])
  const node: WfNode = { id: nid, type, config: defaultConfig(type) }
  return { def: { ...def, nodes: [...def.nodes, node] }, id: nid }
}

export function removeNodes(def: WfDef, ids: readonly string[]): WfDef {
  const gone = new Set(ids)
  return { ...def, nodes: def.nodes.filter(n => !gone.has(n.id)), edges: def.edges.filter(e => !gone.has(e.from) && !gone.has(e.to)) }
}

/** Copy nodes (and the connections between them) under fresh ids. Returns the new ids in the same order. */
export function duplicateNodes(def: WfDef, ids: readonly string[]): { def: WfDef; ids: string[] } {
  let working = def
  const map = new Map<string, string>()
  const copies: WfNode[] = []
  for (const id of ids) {
    const src = def.nodes.find(n => n.id === id)
    if (!src) continue
    const nid = uniqueId(working, `${id}_copy`)
    map.set(id, nid)
    const copy = { ...clone(src), id: nid }
    copies.push(copy)
    working = { ...working, nodes: [...working.nodes, copy] }
  }
  const edges = def.edges
    .filter(e => map.has(e.from) && map.has(e.to))
    .map(e => ({ ...clone(e), from: map.get(e.from)!, to: map.get(e.to)! }))
  return { def: { ...working, edges: [...working.edges, ...edges] }, ids: [...map.values()] }
}

function rewriteRefs(value: unknown, from: string, to: string): unknown {
  if (typeof value === 'string') return value.split(`{{nodes.${from}.`).join(`{{nodes.${to}.`).split(`{{ nodes.${from}.`).join(`{{ nodes.${to}.`)
  if (Array.isArray(value)) return value.map(v => rewriteRefs(v, from, to))
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, rewriteRefs(v, from, to)]))
  return value
}

export type RenameResult = { ok: true; def: WfDef } | { ok: false; reason: string }

export function renameNode(def: WfDef, oldId: string, newId: string): RenameResult {
  if (oldId === newId) return { ok: true, def }
  if (!NODE_ID_RE.test(newId)) return { ok: false, reason: 'Use a letter first, then letters, digits or underscores (up to 41 characters).' }
  if (def.nodes.some(n => n.id === newId)) return { ok: false, reason: `Another step is already called "${newId}".` }
  return {
    ok: true,
    def: {
      ...def,
      nodes: def.nodes.map(n => {
        const renamed = n.id === oldId ? { ...n, id: newId } : n
        return renamed.config ? { ...renamed, config: rewriteRefs(renamed.config, oldId, newId) as Record<string, unknown> } : renamed
      }),
      edges: def.edges.map(e => ({ ...e, from: e.from === oldId ? newId : e.from, to: e.to === oldId ? newId : e.to })),
    },
  }
}

export function updateNode(def: WfDef, id: string, patch: (n: WfNode) => WfNode): WfDef {
  return { ...def, nodes: def.nodes.map(n => (n.id === id ? patch(n) : n)) }
}

export function setConfig(def: WfDef, id: string, config: Record<string, unknown>): WfDef {
  return updateNode(def, id, n => ({ ...n, config }))
}

/** Set or clear one config key. Empty strings and undefined remove optional keys when `drop` is true. */
export function setConfigKey(def: WfDef, id: string, key: string, value: unknown, drop = false): WfDef {
  return updateNode(def, id, n => {
    const config = { ...asRecord(n.config) }
    if (drop && (value === undefined || value === '')) delete config[key]
    else config[key] = value
    return { ...n, config }
  })
}

export function setMaxVisits(def: WfDef, id: string, visits: number | undefined): WfDef {
  return updateNode(def, id, n => {
    const next = { ...n }
    if (visits === undefined || visits === 1) delete next.max_visits
    else next.max_visits = visits
    return next
  })
}

export type EdgeResult = { ok: true; def: WfDef } | { ok: false; reason: string }

export function addEdge(def: WfDef, from: string, to: string, when: string | null): EdgeResult {
  const verdict: Verdict = canConnect(def, from, to, when)
  if (!verdict.ok) return verdict
  const edge: WfEdge = when ? { from, to, when } : { from, to }
  return { ok: true, def: { ...def, edges: [...def.edges, edge] } }
}

export function removeEdge(def: WfDef, from: string, to: string, when: string | null): WfDef {
  let removed = false
  return {
    ...def,
    edges: def.edges.filter(e => {
      if (!removed && e.from === from && e.to === to && (e.when ?? null) === when) {
        removed = true
        return false
      }
      return true
    }),
  }
}

export function setEdgeLabel(def: WfDef, from: string, to: string, oldWhen: string | null, newWhen: string | null): EdgeResult {
  const without = removeEdge(def, from, to, oldWhen)
  const res = addEdge(without, from, to, newWhen)
  if (!res.ok) return res
  // keep the edge in its original position so exports stay stable
  return { ok: true, def: { ...res.def, edges: orderLike(def.edges, res.def.edges, from, to, oldWhen, newWhen) } }
}

function orderLike(before: WfEdge[], after: WfEdge[], from: string, to: string, oldWhen: string | null, newWhen: string | null): WfEdge[] {
  const idx = before.findIndex(e => e.from === from && e.to === to && (e.when ?? null) === oldWhen)
  const changed = after[after.length - 1]
  const rest = after.slice(0, -1)
  if (idx < 0) return after
  return [...rest.slice(0, idx), changed, ...rest.slice(idx)]
}

export function outgoingOf(def: WfDef, id: string): WfEdge[] {
  return def.edges.filter(e => e.from === id)
}

export function setTrigger(def: WfDef, patch: { type?: string; filter?: Record<string, unknown> }): WfDef {
  return { ...def, trigger: { ...asRecord(def.trigger), ...patch } }
}

export function setVariables(def: WfDef, variables: Record<string, WfVariable>): WfDef {
  return { ...def, variables }
}

export function setMeta(def: WfDef, patch: { name?: string; description?: string }): WfDef {
  return { ...def, ...patch }
}

export function isNodeTypeName(t: string): t is NodeType {
  return (NODE_TYPES as string[]).includes(t)
}
