import { describe, expect, it } from 'vitest'
import { clientCheck, mergeProblems, problemsByNode } from '../../src/lib/workflow/clientCheck'
import { changeKind, addMember, removeMember, describeCondition, emptyComparison, filterToRows, rowsToFilter, setAt, condKind } from '../../src/lib/workflow/condition'
import { addEdge, addNode, emptyDefinition, setMaxVisits } from '../../src/lib/workflow/model'
import { parseImport, parseYaml, toJson, toYaml } from '../../src/lib/workflow/serialize'
import { clearDraft, loadDraft, loadLayout, loadVersions, rememberVersion, saveDraft, saveLayout } from '../../src/lib/workflow/storage'
import { ACTIONS, docsExample } from './wfHelpers'

const codes = (d: Parameters<typeof clientCheck>[0], actions = ACTIONS) => clientCheck(d, actions).map(p => p.code)

describe('client-side checks', () => {
  it('passes the documented example', () => {
    expect(clientCheck(docsExample(), ACTIONS)).toEqual([])
  })
  it('flags an empty workflow and a bad name', () => {
    expect(codes(emptyDefinition('x'))).toEqual(['nodes'])
    expect(codes({ ...emptyDefinition('!!'), nodes: [{ id: 'a', type: 'end' }] })).toContain('name')
  })
  it('flags missing configuration on the node that needs it', () => {
    let d = addNode(emptyDefinition(), 'agent', 'a').def
    const p = clientCheck(d)
    expect(p).toContainEqual(expect.objectContaining({ code: 'missing_config', node: 'a', source: 'local' }))
    d = addNode(d, 'timer', 't').def
    expect(problemsByNode(clientCheck(d)).get('a')).toHaveLength(1)
  })
  it('flags dangling edges, duplicates, unreachable nodes and missing branches', () => {
    const d = docsExample()
    expect(codes({ ...d, edges: [...d.edges, { from: 'fix', to: 'ghost' }] })).toContain('dangling_edge')
    expect(codes({ ...d, nodes: [...d.nodes, { id: 'fix', type: 'end' }] })).toContain('duplicate_node')
    const island = { ...d, nodes: [...d.nodes, { id: 'x', type: 'timer', config: { seconds: 5 }, max_visits: 2 }, { id: 'y', type: 'timer', config: { seconds: 5 }, max_visits: 2 }], edges: [...d.edges, { from: 'x', to: 'y' }, { from: 'y', to: 'x' }] }
    expect(codes(island)).toContain('unreachable')
    expect(codes({ ...d, edges: d.edges.filter(e => e.when !== 'false') })).toContain('condition_edges')
  })
  it('requires a repeat limit on loops', () => {
    let d = addNode(addNode(emptyDefinition(), 'timer', 'a').def, 'timer', 'b').def
    const e1 = addEdge(d, 'a', 'b', null)
    if (!e1.ok) throw new Error()
    const e2 = addEdge(e1.def, 'b', 'a', null)
    if (!e2.ok) throw new Error()
    expect(codes(e2.def)).toContain('unbounded_loop')
    expect(codes(setMaxVisits(setMaxVisits(e2.def, 'a', 3), 'b', 3))).not.toContain('unbounded_loop')
  })
  it('flags references to steps that do not run earlier or variables that are not declared', () => {
    const d = docsExample()
    const bad = { ...d, nodes: d.nodes.map(n => (n.id === 'fix' ? { ...n, config: { task: '{{nodes.post.output}} {{vars.nope}} {{foo.bar}}' } } : n)) }
    expect(codes(bad).filter(c => c === 'bad_reference' || c === 'unknown_variable').length).toBe(3)
  })
  it('flags an external write that is not behind an approval', () => {
    const d = docsExample()
    const open = { ...d, edges: d.edges.map(e => (e.from === 'ok' && e.when === 'true' ? { ...e, to: 'post' } : e)) }
    const problems = clientCheck(open, ACTIONS)
    expect(problems.find(p => p.code === 'missing_approval')?.message).toMatch(/person must approve/)
  })
  it('server verdict replaces local hints once it arrives', () => {
    const local = [{ code: 'x', message: 'local', source: 'local' as const }]
    expect(mergeProblems(null, local)).toBe(local)
    expect(mergeProblems([], local)).toEqual([])
    expect(mergeProblems([{ code: 'y', message: 'srv' }], local)[0].source).toBe('server')
  })
})

describe('import and export', () => {
  it('JSON round-trips exactly, including fields the editor does not know about', () => {
    const def = { ...docsExample(), x_future: { keep: ['me'] } }
    def.nodes[0] = { ...def.nodes[0], x_note: 'hello', config: { ...def.nodes[0].config, future_key: 1 } }
    def.edges[0] = { ...def.edges[0], x_style: 'dashed' }
    const back = parseImport(toJson(def))
    expect(back.ok && back.def).toEqual(def)
  })
  it('editing one node leaves every unknown field untouched', () => {
    const def = { ...docsExample(), x_future: 1 }
    def.nodes[2] = { ...def.nodes[2], x_note: 'n' }
    const parsed = parseImport(toJson(def))
    if (!parsed.ok) throw new Error()
    const edited = { ...parsed.def, nodes: parsed.def.nodes.map(n => (n.id === 'fix' ? { ...n, config: { ...n.config, task: 'new' } } : n)) }
    expect((edited as Record<string, unknown>).x_future).toBe(1)
    expect(edited.nodes[2]).toEqual(def.nodes[2])
    expect(edited.edges).toEqual(def.edges)
  })
  it('YAML export is readable and parses back to the same definition', () => {
    const def = docsExample()
    const yaml = toYaml(def)
    expect(yaml).toContain('name: issue-to-proposal')
    expect(yaml).toContain('- id: fix')
    expect(parseYaml(yaml)).toEqual(def)
    const via = parseImport(yaml)
    expect(via.ok && via.def).toEqual(def)
  })
  it('YAML survives awkward strings', () => {
    const def = { ...emptyDefinition('x'), description: 'line: with colon # and hash', nodes: [{ id: 'a', type: 'agent', config: { task: 'say "hi" {{vars.x}}', repo: 'true', on_failure: '' } }] }
    expect(parseYaml(toYaml(def))).toEqual(def)
  })
  it('reads the documented hand-written YAML (flow maps, plain scalars, comments)', () => {
    const text = `name: issue-to-proposal   # a name
trigger: {type: github.issues.labeled, filter: {payload.label: agent-ready}}
variables: {repo: {default: /work/app}}
nodes:
  - {id: fix, type: agent, config: {task: "Resolve: {{trigger.payload.title}}", repo: "{{vars.repo}}", provider: sglang}}
  - {id: ok, type: condition, config: {if: {left: "{{nodes.fix.output.verdict}}", op: eq, right: passed}}}
  - {id: done, type: end}
edges: [{from: fix, to: ok}, {from: ok, to: done, when: "true"}]
`
    const r = parseImport(text)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.def.trigger).toEqual({ type: 'github.issues.labeled', filter: { 'payload.label': 'agent-ready' } })
    expect(r.def.nodes[1].config).toEqual({ if: { left: '{{nodes.fix.output.verdict}}', op: 'eq', right: 'passed' } })
    expect(r.def.edges[1]).toEqual({ from: 'ok', to: 'done', when: 'true' })
    expect(r.def.variables).toEqual({ repo: { default: '/work/app' } })
  })
  it('refuses junk with a plain message', () => {
    expect(parseImport('')).toMatchObject({ ok: false })
    expect(parseImport('[1,2]')).toMatchObject({ ok: false })
    expect(parseImport('{"a": ')).toMatchObject({ ok: false, error: expect.stringMatching(/valid JSON/) })
    expect(parseImport('just text')).toMatchObject({ ok: false })
    expect(parseImport('a:\n\tb: 1')).toMatchObject({ ok: false, error: expect.stringMatching(/tabs/) })
  })
  it('fills the arrays a partial file lacks, keeping the rest', () => {
    const r = parseImport('{"name":"x","extra":1}')
    expect(r.ok && r.def).toEqual({ name: 'x', extra: 1, nodes: [], edges: [] })
  })
})

describe('condition builder', () => {
  it('switches between comparison and groups keeping members', () => {
    const c = { left: 'a', op: 'eq', right: 'b' }
    const all = changeKind(c, 'all')
    expect(all).toEqual({ all: [c] })
    expect(changeKind(all, 'any')).toEqual({ any: [c] })
    expect(changeKind(all, 'compare')).toEqual(c)
    expect(changeKind(c, 'not')).toEqual({ not: c })
    expect(condKind(changeKind(c, 'compare'))).toBe('compare')
  })
  it('edits nested members by path, adds and removes', () => {
    let c: Record<string, unknown> = { all: [emptyComparison(), { any: [emptyComparison()] }] }
    c = setAt(c, [1, 0], { left: 'x', op: 'ne', right: 'y' })
    expect(JSON.stringify(c)).toContain('"left":"x"')
    c = addMember(c, [1])
    expect(((c.all as Record<string, unknown>[])[1].any as unknown[]).length).toBe(2)
    c = removeMember(c, [1, 1])
    expect(((c.all as Record<string, unknown>[])[1].any as unknown[]).length).toBe(1)
    c = removeMember(c, [0])
    expect((c.all as unknown[]).length).toBe(1)
    const only = removeMember({ all: [emptyComparison()] }, [0])
    expect((only.all as unknown[]).length).toBe(1) // never leaves an empty group
  })
  it('describes conditions in plain words', () => {
    expect(describeCondition({ all: [{ left: '{{nodes.fix.output.verdict}}', op: 'eq', right: 'passed' }, { left: '{{vars.x}}', op: 'exists' }] })).toBe(
      '{{nodes.fix.output.verdict}} is equal to passed and {{vars.x}} exists',
    )
  })
  it('turns trigger filter rows into the backend filter shape and back', () => {
    const filter = { 'payload.label': 'agent-ready', 'payload.n': 3, 'payload.kind': { in: ['a', 'b'] } }
    const rows = filterToRows(filter)
    expect(rows).toEqual([
      { path: 'payload.label', mode: 'equals', value: 'agent-ready' },
      { path: 'payload.n', mode: 'equals', value: '3' },
      { path: 'payload.kind', mode: 'in', value: 'a, b' },
    ])
    expect(rowsToFilter(rows)).toEqual(filter)
    expect(rowsToFilter([{ path: ' ', mode: 'equals', value: 'x' }])).toEqual({})
  })
})

describe('browser storage helpers never throw', () => {
  it('saves and loads drafts, layout and remembered versions', () => {
    saveDraft('k', { def: docsExample(), savedAt: 1, baseId: null })
    expect(loadDraft('k')?.def.name).toBe('issue-to-proposal')
    clearDraft('k')
    expect(loadDraft('k')).toBeNull()
    saveLayout('k', { a: { x: 1, y: 2 } })
    expect(loadLayout('k')).toEqual({ a: { x: 1, y: 2 } })
    rememberVersion('wf', { id: 'a', version: 1, savedAt: 1 })
    rememberVersion('wf', { id: 'b', version: 2, savedAt: 2 })
    expect(loadVersions('wf').map(v => v.version)).toEqual([2, 1])
  })
  it('survives unreadable storage', () => {
    const real = globalThis.localStorage
    Object.defineProperty(globalThis, 'localStorage', { value: { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') }, removeItem() { throw new Error('blocked') } }, configurable: true })
    expect(loadDraft('k')).toBeNull()
    expect(saveDraft('k', { def: docsExample(), savedAt: 1, baseId: null })).toBe(false)
    expect(loadVersions('wf')).toEqual([])
    Object.defineProperty(globalThis, 'localStorage', { value: real, configurable: true })
  })
})
