import { describe, expect, it } from 'vitest'
import { ensureLayout, layoutFrom, LAYOUT_LIMIT, withLayout } from '../../src/lib/workflow/layout'
import { removeNodes } from '../../src/lib/workflow/model'
import { parseImport, toJson, toYaml } from '../../src/lib/workflow/serialize'
import { docsExample } from './wfHelpers'

describe('layout stored in the definition', () => {
  const positions = { fix: { x: 0, y: 0 }, ok: { x: 300, y: 40 }, review: { x: 600, y: 0 }, post: { x: 900, y: 0 }, done: { x: 1200, y: 0 } }

  it('writes one position per node and leaves every other field alone', () => {
    const def = { ...docsExample(), x_future: { keep: 1 } }
    const full = withLayout(def, { ...positions, ghost: { x: 5, y: 5 } })
    expect(Object.keys(full.layout as object).sort()).toEqual(['done', 'fix', 'ok', 'post', 'review'])
    expect(full.x_future).toEqual({ keep: 1 })
    expect(full.nodes).toBe(def.nodes)
    expect(full.edges).toBe(def.edges)
  })
  it('prunes entries for deleted nodes', () => {
    const def = removeNodes(withLayout(docsExample(), positions), ['review'])
    expect(Object.keys(withLayout(def, positions).layout as object)).not.toContain('review')
  })
  it('keeps positions inside the limits the server accepts and rounds them', () => {
    const full = withLayout({ nodes: [{ id: 'a', type: 'end' }], edges: [] }, { a: { x: 1e9, y: -1e9 } })
    expect(full.layout).toEqual({ a: { x: LAYOUT_LIMIT, y: -LAYOUT_LIMIT } })
    expect(withLayout({ nodes: [{ id: 'a', type: 'end' }], edges: [] }, { a: { x: 1.6, y: 2.4 } }).layout).toEqual({ a: { x: 2, y: 2 } })
    expect(withLayout({ nodes: [{ id: 'a', type: 'end' }], edges: [] }, { a: { x: NaN, y: 0 } }).layout).toBeUndefined()
  })
  it('reads stored positions back, ignoring malformed ones, and auto-lays-out the rest', () => {
    const def = { ...docsExample(), layout: { fix: { x: 10, y: 20 }, ok: { x: 'a', y: 1 }, bad: 5, review: { x: Infinity, y: 0 } } }
    expect(layoutFrom(def as never)).toEqual({ fix: { x: 10, y: 20 } })
    const pos = ensureLayout(def as never, layoutFrom(def as never))
    expect(pos.fix).toEqual({ x: 10, y: 20 })
    expect(Object.keys(pos)).toHaveLength(5)
    expect(layoutFrom(docsExample())).toBeUndefined()
  })
  it('survives JSON and YAML export and import, with unknown fields', () => {
    const full = { ...withLayout(docsExample(), positions), x_future: [1, 2] }
    const j = parseImport(toJson(full))
    const y = parseImport(toYaml(full))
    expect(j.ok && j.def).toEqual(full)
    expect(y.ok && y.def).toEqual(full)
    expect(toYaml(full)).toContain('layout:')
    expect(layoutFrom((j.ok && j.def) as never)).toEqual(positions)
  })
})
