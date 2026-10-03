import { asRecord, type WfDef } from './types'

export interface RefOption {
  /** dotted path without braces, e.g. "nodes.fix.output.verdict" */
  path: string
  group: 'Trigger' | 'Variables' | 'Earlier steps'
  label: string
}

const AGENT_OUTPUT = ['run_id', 'status', 'outcome', 'verdict', 'failure_kind']

/** Ids of every node that can run before `nodeId` (anything with a path leading to it). */
export function ancestorsOf(def: WfDef, nodeId: string): Set<string> {
  const seen = new Set<string>()
  const stack = def.edges.filter(e => e.to === nodeId).map(e => e.from)
  while (stack.length) {
    const cur = stack.pop()!
    if (seen.has(cur)) continue
    seen.add(cur)
    for (const e of def.edges) if (e.to === cur) stack.push(e.from)
  }
  seen.delete(nodeId)
  return seen
}

/** Only references the server will accept for this node: trigger fields, declared variables, earlier steps. */
export function availableRefs(def: WfDef, nodeId: string): RefOption[] {
  const out: RefOption[] = [{ path: 'trigger.type', group: 'Trigger', label: 'Trigger type' }]
  for (const key of Object.keys(asRecord(def.trigger?.filter))) {
    out.push({ path: `trigger.${key}`, group: 'Trigger', label: `Trigger ${key}` })
  }
  for (const name of Object.keys(asRecord(def.variables))) out.push({ path: `vars.${name}`, group: 'Variables', label: name })
  const before = ancestorsOf(def, nodeId)
  for (const n of def.nodes) {
    if (!before.has(n.id) || n.type === 'end') continue
    if (n.type === 'agent') {
      for (const k of AGENT_OUTPUT) out.push({ path: `nodes.${n.id}.output.${k}`, group: 'Earlier steps', label: `${n.id}: ${k.replace('_', ' ')}` })
    } else {
      out.push({ path: `nodes.${n.id}.output`, group: 'Earlier steps', label: `${n.id}: output` })
    }
  }
  return out
}

export function insertAtCursor(value: string, start: number, end: number, path: string): { value: string; caret: number } {
  const token = `{{${path}}}`
  const s = Math.max(0, Math.min(start, value.length))
  const e = Math.max(s, Math.min(end, value.length))
  return { value: value.slice(0, s) + token + value.slice(e), caret: s + token.length }
}

const TEMPLATE = /\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}/g

export function referencesIn(value: unknown): string[] {
  if (typeof value === 'string') return [...value.matchAll(TEMPLATE)].map(m => m[1])
  if (Array.isArray(value)) return value.flatMap(referencesIn)
  if (value && typeof value === 'object') return Object.values(value).flatMap(referencesIn)
  return []
}
