import { OPERATOR_COPY } from './copy'
import { asRecord } from './types'

export type Cond = Record<string, unknown>
export type CondKind = 'compare' | 'all' | 'any' | 'not'

export const emptyComparison = (): Cond => ({ left: '', op: 'eq', right: '' })

export function condKind(c: Cond): CondKind {
  if ('all' in c) return 'all'
  if ('any' in c) return 'any'
  if ('not' in c) return 'not'
  return 'compare'
}

export function children(c: Cond): Cond[] {
  const kind = condKind(c)
  if (kind === 'all' || kind === 'any') return (Array.isArray(c[kind]) ? (c[kind] as unknown[]) : []).map(asRecord)
  if (kind === 'not') return [asRecord(c.not)]
  return []
}

/** Change the shape of one condition, keeping what can be kept (a group's members become the new group's members). */
export function changeKind(c: Cond, kind: CondKind): Cond {
  if (condKind(c) === kind) return c
  const kept = condKind(c) === 'compare' ? [c] : children(c)
  switch (kind) {
    case 'compare': return kept[0] && condKind(kept[0]) === 'compare' ? kept[0] : emptyComparison()
    case 'all': return { all: kept.length ? kept : [emptyComparison()] }
    case 'any': return { any: kept.length ? kept : [emptyComparison()] }
    case 'not': return { not: kept[0] ?? emptyComparison() }
  }
}

export type CondPath = number[]

export function getAt(c: Cond, path: CondPath): Cond {
  let cur = c
  for (const i of path) cur = children(cur)[i] ?? cur
  return cur
}

/** Replace the condition at `path` (indices into group members; `not` has one member at 0). */
export function setAt(c: Cond, path: CondPath, next: Cond): Cond {
  if (!path.length) return next
  const [head, ...rest] = path
  const kind = condKind(c)
  if (kind === 'not') return { ...c, not: setAt(asRecord(c.not), rest, next) }
  if (kind === 'all' || kind === 'any') {
    const list = children(c).slice()
    list[head] = setAt(list[head] ?? emptyComparison(), rest, next)
    return { ...c, [kind]: list }
  }
  return c
}

export function addMember(c: Cond, path: CondPath): Cond {
  const target = getAt(c, path)
  const kind = condKind(target)
  if (kind !== 'all' && kind !== 'any') return c
  return setAt(c, path, { ...target, [kind]: [...children(target), emptyComparison()] })
}

export function removeMember(c: Cond, path: CondPath): Cond {
  if (!path.length) return c
  const parentPath = path.slice(0, -1)
  const parent = getAt(c, parentPath)
  const kind = condKind(parent)
  if (kind !== 'all' && kind !== 'any') return c
  const list = children(parent).filter((_, i) => i !== path[path.length - 1])
  return setAt(c, parentPath, { ...parent, [kind]: list.length ? list : [emptyComparison()] })
}

export function describeCondition(c: Cond): string {
  switch (condKind(c)) {
    case 'all': return children(c).map(describeCondition).join(' and ') || 'nothing'
    case 'any': return children(c).map(describeCondition).join(' or ') || 'nothing'
    case 'not': return `not (${describeCondition(asRecord(c.not))})`
    default: {
      const op = OPERATOR_COPY.find(o => o.id === c.op)
      const left = String(c.left ?? '') || '…'
      return op?.unary ? `${left} ${op.label}` : `${left} ${op?.label ?? String(c.op)} ${String(c.right ?? '') || '…'}`
    }
  }
}

// ---- trigger filter rows
export interface FilterRow {
  path: string
  mode: 'equals' | 'in'
  value: string
}

function scalarText(v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v)
}

function parseScalar(s: string): unknown {
  const t = s.trim()
  if (t === 'true') return true
  if (t === 'false') return false
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t)
  return t
}

export function filterToRows(filter: Record<string, unknown> | undefined): FilterRow[] {
  return Object.entries(filter ?? {}).map(([path, v]) => {
    const rec = asRecord(v)
    if (Array.isArray(rec.in)) return { path, mode: 'in' as const, value: (rec.in as unknown[]).map(scalarText).join(', ') }
    return { path, mode: 'equals' as const, value: scalarText(v) }
  })
}

export function rowsToFilter(rows: FilterRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const r of rows) {
    const path = r.path.trim()
    if (!path) continue
    out[path] = r.mode === 'in' ? { in: r.value.split(',').map(s => parseScalar(s)).filter(v => v !== '') } : parseScalar(r.value)
  }
  return out
}
