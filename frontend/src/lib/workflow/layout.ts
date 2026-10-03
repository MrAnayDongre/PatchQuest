import type { Pos, Rect, WfDef } from './types'

export const NODE_W = 208
export const NODE_H = 76
export const GRID = 20

export const snap = (v: number, grid = GRID): number => Math.round(v / grid) * grid || 0

export function snapPos(p: Pos, grid = GRID): Pos {
  return { x: snap(p.x, grid), y: snap(p.y, grid) }
}

/**
 * Layered left-to-right layout: a node's column is its longest distance from an entry (back edges of loops are
 * ignored), rows keep definition order within a column. Used when no positions are stored for the definition.
 */
export function autoLayout(def: WfDef): Record<string, Pos> {
  const ids = def.nodes.map(n => n.id)
  const idSet = new Set(ids)
  const edges = def.edges.filter(e => idSet.has(e.from) && idSet.has(e.to))
  const hasIncoming = new Set(edges.map(e => e.to))
  const entries = ids.filter(id => !hasIncoming.has(id))
  const depth = new Map<string, number>()
  const visiting = new Set<string>()
  const visit = (id: string, d: number) => {
    if (visiting.has(id)) return // loop: do not follow the back edge
    if ((depth.get(id) ?? -1) >= d) return
    depth.set(id, d)
    visiting.add(id)
    for (const e of edges) if (e.from === id) visit(e.to, d + 1)
    visiting.delete(id)
  }
  for (const id of entries.length ? entries : ids.slice(0, 1)) visit(id, 0)
  let extra = 0
  for (const id of ids) if (!depth.has(id)) depth.set(id, extra++ % 1)
  const rows = new Map<number, number>()
  const out: Record<string, Pos> = {}
  for (const id of ids) {
    const col = depth.get(id) ?? 0
    const row = rows.get(col) ?? 0
    rows.set(col, row + 1)
    out[id] = { x: col * (NODE_W + 96), y: row * (NODE_H + 48) }
  }
  return out
}

/** Keep stored positions for nodes that still exist and lay out any that have none (new nodes, imports). */
export function ensureLayout(def: WfDef, stored: Record<string, Pos> | undefined): Record<string, Pos> {
  const auto = autoLayout(def)
  const out: Record<string, Pos> = {}
  for (const n of def.nodes) out[n.id] = stored?.[n.id] ?? auto[n.id]
  return out
}

export const rectOf = (p: Pos): Rect => ({ x: p.x, y: p.y, w: NODE_W, h: NODE_H })

export function boundsOf(rects: Rect[]): Rect | null {
  if (!rects.length) return null
  const x1 = Math.min(...rects.map(r => r.x))
  const y1 = Math.min(...rects.map(r => r.y))
  const x2 = Math.max(...rects.map(r => r.x + r.w))
  const y2 = Math.max(...rects.map(r => r.y + r.h))
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 }
}

export interface Guide {
  axis: 'x' | 'y'
  /** world coordinate of the guide line */
  at: number
}

/**
 * Snap a dragged rectangle to the edges and centres of other rectangles when within `threshold` world units.
 * Returns the correction to apply plus the guide lines to draw.
 */
export function alignGuides(moving: Rect, others: Rect[], threshold = 6): { dx: number; dy: number; guides: Guide[] } {
  const mx = [moving.x, moving.x + moving.w / 2, moving.x + moving.w]
  const my = [moving.y, moving.y + moving.h / 2, moving.y + moving.h]
  let bestX: { d: number; at: number } | null = null
  let bestY: { d: number; at: number } | null = null
  for (const o of others) {
    for (const ox of [o.x, o.x + o.w / 2, o.x + o.w]) {
      for (const x of mx) {
        const d = ox - x
        if (Math.abs(d) <= threshold && (!bestX || Math.abs(d) < Math.abs(bestX.d))) bestX = { d, at: ox }
      }
    }
    for (const oy of [o.y, o.y + o.h / 2, o.y + o.h]) {
      for (const y of my) {
        const d = oy - y
        if (Math.abs(d) <= threshold && (!bestY || Math.abs(d) < Math.abs(bestY.d))) bestY = { d, at: oy }
      }
    }
  }
  const guides: Guide[] = []
  if (bestX) guides.push({ axis: 'x', at: bestX.at })
  if (bestY) guides.push({ axis: 'y', at: bestY.at })
  return { dx: bestX?.d ?? 0, dy: bestY?.d ?? 0, guides }
}

/** A free spot near `center` (world coordinates), snapped to the grid and nudged down until nothing overlaps. */
export function placeNear(positions: Record<string, Pos>, center: Pos): Pos {
  let p = snapPos({ x: center.x - NODE_W / 2, y: center.y - NODE_H / 2 })
  const overlaps = (q: Pos) => Object.values(positions).some(o => Math.abs(o.x - q.x) < NODE_W && Math.abs(o.y - q.y) < NODE_H)
  let guard = 0
  while (overlaps(p) && guard++ < 200) p = { x: p.x, y: p.y + NODE_H + 24 - ((NODE_H + 24) % GRID) + GRID }
  return p
}

/** Output port (right middle) and input port (left middle) of a node at `p`. */
export const outPort = (p: Pos): Pos => ({ x: p.x + NODE_W, y: p.y + NODE_H / 2 })
export const inPort = (p: Pos): Pos => ({ x: p.x, y: p.y + NODE_H / 2 })

/** SVG path for a connection between two ports: a smooth horizontal curve that also reads for backwards loops. */
export function edgePath(a: Pos, b: Pos): string {
  const dx = Math.max(48, Math.abs(b.x - a.x) / 2)
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`
}

/** Midpoint of the curve, where the label sits. */
export function edgeMid(a: Pos, b: Pos): Pos {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
}
