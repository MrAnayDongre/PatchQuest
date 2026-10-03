import { describe, expect, it } from 'vitest'
import { canConnect, labelOptions, nextLabel, needsLabelChoice } from '../../src/lib/workflow/edgeRules'
import { addEdge, addNode, duplicateNodes, emptyDefinition, removeEdge, removeNodes, renameNode, setConfigKey, setEdgeLabel, setMaxVisits, uniqueId } from '../../src/lib/workflow/model'
import { docsExample } from './wfHelpers'

describe('graph model', () => {
  it('adds nodes with unique readable ids and sensible default config', () => {
    let def = emptyDefinition('x')
    const a = addNode(def, 'agent')
    def = a.def
    const b = addNode(def, 'agent')
    expect([a.id, b.id]).toEqual(['agent', 'agent_2'])
    expect(b.def.nodes[1].config).toEqual({ task: '' })
    expect(addNode(emptyDefinition(), 'condition').def.nodes[0].config).toEqual({ if: { left: '', op: 'eq', right: '' } })
    expect(uniqueId(emptyDefinition(), '9 bad id!')).toBe('nbad_id_')
  })

  it('removes nodes together with their connections', () => {
    const def = removeNodes(docsExample(), ['review'])
    expect(def.nodes.map(n => n.id)).not.toContain('review')
    expect(def.edges.some(e => e.from === 'review' || e.to === 'review')).toBe(false)
    expect(def.edges).toHaveLength(3)
  })

  it('duplicates nodes under new ids, keeping connections between the copies only', () => {
    const { def, ids } = duplicateNodes(docsExample(), ['ok', 'review'])
    expect(ids).toEqual(['ok_copy', 'review_copy'])
    expect(def.nodes).toHaveLength(7)
    const copied = def.edges.filter(e => e.from.endsWith('_copy') || e.to.endsWith('_copy'))
    expect(copied).toEqual([{ from: 'ok_copy', to: 'review_copy', when: 'true' }])
    // the copy is a deep copy
    const original = def.nodes.find(n => n.id === 'review')!
    const copy = def.nodes.find(n => n.id === 'review_copy')!
    expect(copy.config).toEqual(original.config)
    expect(copy.config).not.toBe(original.config)
  })

  it('renames a node and follows it in connections and {{nodes.x.}} references', () => {
    const r = renameNode(docsExample(), 'fix', 'investigate')
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.def.edges[0]).toEqual({ from: 'investigate', to: 'ok' })
    expect(JSON.stringify(r.def.nodes[1].config)).toContain('{{nodes.investigate.output.verdict}}')
    expect(JSON.stringify(r.def)).not.toContain('nodes.fix.')
  })

  it('refuses bad or clashing names with a reason', () => {
    expect(renameNode(docsExample(), 'fix', 'ok')).toMatchObject({ ok: false })
    expect(renameNode(docsExample(), 'fix', '1bad')).toMatchObject({ ok: false })
  })

  it('edits config keys and drops optional ones when emptied', () => {
    let def = setConfigKey(docsExample(), 'fix', 'model', 'm1', true)
    expect(def.nodes[0].config?.model).toBe('m1')
    def = setConfigKey(def, 'fix', 'model', '', true)
    expect('model' in (def.nodes[0].config ?? {})).toBe(false)
    expect(setMaxVisits(setMaxVisits(docsExample(), 'fix', 3), 'fix', 1).nodes[0].max_visits).toBeUndefined()
    expect(setMaxVisits(docsExample(), 'fix', 3).nodes[0].max_visits).toBe(3)
  })

  it('does not mutate its input', () => {
    const def = docsExample()
    const snap = JSON.stringify(def)
    removeNodes(def, ['fix'])
    duplicateNodes(def, ['fix'])
    renameNode(def, 'fix', 'zz')
    setConfigKey(def, 'fix', 'task', 'changed')
    expect(JSON.stringify(def)).toBe(snap)
  })
})

describe('edges', () => {
  it('adds, removes and relabels connections', () => {
    let def = addNode(addNode(emptyDefinition(), 'agent', 'a').def, 'end', 'z').def
    const added = addEdge(def, 'a', 'z', null)
    expect(added.ok).toBe(true)
    if (!added.ok) return
    def = added.def
    expect(def.edges).toEqual([{ from: 'a', to: 'z' }])
    expect(removeEdge(def, 'a', 'z', null).edges).toEqual([])
  })

  it('offers the right labels for each node type', () => {
    const d = docsExample()
    const labels = (id: string) => labelOptions(d.nodes.find(n => n.id === id)!).map(o => o.value)
    expect(labels('ok')).toEqual(['true', 'false'])
    expect(labels('review')).toEqual([null, 'approved', 'denied', 'timeout'])
    expect(labels('fix')).toEqual([null])
    expect(labels('done')).toEqual([])
    const cont = setConfigKey(d, 'fix', 'on_failure', 'continue')
    expect(labelOptions(cont.nodes[0]).map(o => o.value)).toEqual([null, 'failed'])
  })

  it('explains why a connection is refused', () => {
    const d = docsExample()
    expect(canConnect(d, 'done', 'fix', null)).toMatchObject({ ok: false, reason: expect.stringMatching(/finishes the workflow/) })
    expect(canConnect(d, 'fix', 'done', 'true')).toMatchObject({ ok: false })
    expect(canConnect(d, 'fix', 'done', 'failed')).toMatchObject({ ok: false, reason: expect.stringMatching(/continue if this step fails|continue/) })
    expect(canConnect(d, 'ok', 'post', 'true')).toMatchObject({ ok: false, reason: expect.stringMatching(/already leads/) })
    expect(canConnect(d, 'fix', 'ok', null)).toMatchObject({ ok: false, reason: expect.stringMatching(/already connected/) })
    expect(canConnect(d, 'fix', 'nope', null)).toMatchObject({ ok: false })
    expect(canConnect(d, 'fix', 'post', null)).toEqual({ ok: true })
  })

  it('picks the next unused branch label automatically', () => {
    let def = addNode(addNode(addNode(emptyDefinition(), 'condition', 'c').def, 'end', 'e1').def, 'end', 'e2').def
    expect(nextLabel(def, 'c')).toBe('true')
    const r = addEdge(def, 'c', 'e1', 'true')
    if (!r.ok) throw new Error('unexpected')
    def = r.def
    expect(nextLabel(def, 'c')).toBe('false')
    expect(needsLabelChoice(def, 'c')).toBe(true)
    expect(needsLabelChoice(docsExample(), 'fix')).toBe(false)
  })

  it('relabelling keeps the connection in place', () => {
    const d = docsExample()
    const r = setEdgeLabel(d, 'review', 'post', 'approved', null)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.def.edges).toHaveLength(d.edges.length)
    expect(r.def.edges[3]).toEqual({ from: 'review', to: 'post' })
  })
})
