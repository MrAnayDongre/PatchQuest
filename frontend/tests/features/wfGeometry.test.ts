import { describe, expect, it } from 'vitest'
import { alignGuides, autoLayout, boundsOf, edgePath, ensureLayout, GRID, NODE_H, NODE_W, placeNear, rectOf, snap } from '../../src/lib/workflow/layout'
import { fitToRect, marqueeHits, MAX_ZOOM, MIN_ZOOM, panBy, pinch, screenToWorld, wheelFactor, worldToScreen, zoomAt } from '../../src/lib/workflow/viewport'
import { docsExample } from './wfHelpers'

describe('layout', () => {
  it('lays nodes out left to right by distance from the start', () => {
    const pos = autoLayout(docsExample())
    const col = (id: string) => Math.round(pos[id].x / (NODE_W + 96))
    expect([col('fix'), col('ok'), col('review'), col('post'), col('done')]).toEqual([0, 1, 2, 3, 4])
  })
  it('does not loop forever on cycles and keeps every node', () => {
    const def = { nodes: [{ id: 'a', type: 'agent' }, { id: 'b', type: 'agent' }], edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }] }
    expect(Object.keys(autoLayout(def)).sort()).toEqual(['a', 'b'])
  })
  it('stacks siblings in one column without overlap', () => {
    const pos = autoLayout(docsExample())
    expect(pos.review.y).not.toBe(pos.done.y === pos.review.y ? pos.review.y + 1 : pos.done.y)
    const col2 = Object.entries(pos).filter(([, p]) => p.x === pos.review.x)
    const ys = col2.map(([, p]) => p.y)
    expect(new Set(ys).size).toBe(ys.length)
  })
  it('keeps stored positions, lays out only new nodes and drops stale ones', () => {
    const def = docsExample()
    const out = ensureLayout(def, { fix: { x: 500, y: 500 }, ghost: { x: 1, y: 1 } })
    expect(out.fix).toEqual({ x: 500, y: 500 })
    expect('ghost' in out).toBe(false)
    expect(Object.keys(out)).toHaveLength(def.nodes.length)
  })
  it('snaps to the grid', () => {
    expect(snap(29)).toBe(20)
    expect(snap(31)).toBe(40)
    expect(snap(-9)).toBe(0)
    expect(snap(7, 5)).toBe(5)
  })
  it('places a new node near a point without overlapping others', () => {
    const p = placeNear({ a: { x: 0, y: 0 } }, { x: NODE_W / 2, y: NODE_H / 2 })
    expect(p.x % GRID).toBe(0)
    expect(Math.abs(p.y)).toBeGreaterThanOrEqual(NODE_H)
  })
  it('bounds and path helpers', () => {
    expect(boundsOf([])).toBeNull()
    expect(boundsOf([{ x: 0, y: 0, w: 10, h: 10 }, { x: 20, y: 5, w: 10, h: 10 }])).toEqual({ x: 0, y: 0, w: 30, h: 15 })
    expect(edgePath({ x: 0, y: 0 }, { x: 100, y: 50 })).toMatch(/^M 0 0 C /)
  })
})

describe('alignment guides', () => {
  const other = rectOf({ x: 300, y: 100 })
  it('snaps edges and centres within the threshold and reports the guide lines', () => {
    const moving = { x: 303, y: 400, w: NODE_W, h: NODE_H }
    const r = alignGuides(moving, [other], 6)
    expect(r.dx).toBe(-3)
    expect(r.dy).toBe(0)
    expect(r.guides).toEqual([{ axis: 'x', at: 300 }])
  })
  it('aligns centres too', () => {
    const r = alignGuides({ x: 0, y: 100 + NODE_H / 2 + 4 - NODE_H / 2, w: NODE_W, h: NODE_H }, [other], 6)
    expect(r.dy).toBe(-4)
    expect(r.guides.some(g => g.axis === 'y')).toBe(true)
  })
  it('does nothing when far away', () => {
    expect(alignGuides({ x: 0, y: 0, w: 10, h: 10 }, [other], 6)).toEqual({ dx: 0, dy: 0, guides: [] })
  })
})

describe('viewport maths', () => {
  const vp = { x: 40, y: -20, zoom: 1.5 }
  it('screen and world coordinates round-trip', () => {
    const w = screenToWorld(vp, { x: 190, y: 130 })
    expect(w).toEqual({ x: 100, y: 100 })
    expect(worldToScreen(vp, w)).toEqual({ x: 190, y: 130 })
  })
  it('zooming keeps the point under the cursor fixed', () => {
    const anchor = { x: 300, y: 200 }
    const before = screenToWorld(vp, anchor)
    const next = zoomAt(vp, 1.2, anchor)
    const after = screenToWorld(next, anchor)
    expect(after.x).toBeCloseTo(before.x)
    expect(after.y).toBeCloseTo(before.y)
    expect(next.zoom).toBeCloseTo(1.8)
  })
  it('clamps zoom at both ends', () => {
    expect(zoomAt(vp, 100, { x: 0, y: 0 }).zoom).toBe(MAX_ZOOM)
    expect(zoomAt(vp, 0.0001, { x: 0, y: 0 }).zoom).toBe(MIN_ZOOM)
  })
  it('pans', () => {
    expect(panBy(vp, 10, -5)).toEqual({ x: 50, y: -25, zoom: 1.5 })
  })
  it('wheel direction: scrolling up zooms in', () => {
    expect(wheelFactor(-100)).toBeGreaterThan(1)
    expect(wheelFactor(100)).toBeLessThan(1)
    expect(wheelFactor(-1, 1)).toBeGreaterThan(wheelFactor(-1, 0))
  })
  it('fits a rectangle inside the view with padding and centres it', () => {
    const v = fitToRect({ x: 0, y: 0, w: 1000, h: 400 }, { w: 600, h: 400 }, 50)
    expect(v.zoom).toBeCloseTo(0.5)
    expect(v.x).toBeCloseTo(50)
    expect(v.y).toBeCloseTo((400 - 400 * 0.5) / 2)
    expect(fitToRect(null, { w: 600, h: 400 }).zoom).toBe(1)
    expect(fitToRect({ x: 0, y: 0, w: 10, h: 10 }, { w: 600, h: 400 }).zoom).toBe(1) // never zooms in past 100%
  })
  it('a pinch scales by the finger distance and follows the midpoint', () => {
    const next = pinch({ x: 0, y: 0, zoom: 1 }, [{ x: 100, y: 100 }, { x: 200, y: 100 }], [{ x: 50, y: 100 }, { x: 250, y: 100 }])
    expect(next.zoom).toBeCloseTo(2)
    expect(screenToWorld(next, { x: 150, y: 100 }).x).toBeCloseTo(150) // world point under the midpoint stays put
  })
  it('marquee selection intersects world rectangles', () => {
    const r = { x: 100, y: 100, w: 50, h: 50 }
    expect(marqueeHits({ x: 0, y: 0, zoom: 1 }, { x: 90, y: 90 }, { x: 110, y: 110 }, r)).toBe(true)
    expect(marqueeHits({ x: 0, y: 0, zoom: 1 }, { x: 0, y: 0 }, { x: 50, y: 50 }, r)).toBe(false)
    expect(marqueeHits({ x: 0, y: 0, zoom: 0.5 }, { x: 40, y: 40 }, { x: 60, y: 60 }, r)).toBe(true)
  })
})
