import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { Connections, NodeConfig, WorkflowSettings } from '../../src/features/workflows/ConfigPanel'
import { NodeListView, Palette, ProblemsList } from '../../src/features/workflows/BuilderParts'
import { Canvas } from '../../src/features/workflows/Canvas'
import { ensureLayout } from '../../src/lib/workflow/layout'
import type { WfDef } from '../../src/lib/workflow/types'
import { ACTIONS, docsExample, lastArg } from '../features/wfHelpers'

afterEach(cleanup)

/** Hosts a definition so forms behave as in the app: every change flows back in. */
function Host({ initial, nodeId, onChange }: { initial: WfDef; nodeId: string; onChange?: (d: WfDef) => void }) {
  const [def, setDef] = useState(initial)
  return <NodeConfig def={def} nodeId={nodeId} actions={ACTIONS} problems={[]} onDef={d => { setDef(d); onChange?.(d) }} onRenamed={() => {}} />
}

describe('node settings forms', () => {
  it('an agent step only offers the settings an agent accepts', () => {
    render(<Host initial={docsExample()} nodeId="fix" />)
    for (const label of ['Task', 'Step name']) expect(screen.getByLabelText(label)).toBeTruthy()
    for (const label of [/^Repository folder/, /^Provider/, /^Model/, /^Base URL/]) expect(screen.getByLabelText(label)).toBeTruthy()
    expect(screen.queryByLabelText('Question for the approver')).toBeNull()
    expect(screen.getByRole('switch', { name: /Continue if this step fails/ })).toBeTruthy()
  })

  it('typing in a field updates the definition and preserves unknown config keys', () => {
    const def = docsExample()
    def.nodes[0] = { ...def.nodes[0], config: { ...def.nodes[0].config, future_key: 7 } }
    const seen = vi.fn()
    render(<Host initial={def} nodeId="fix" onChange={seen} />)
    fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Do the thing' } })
    const latest = lastArg<WfDef>(seen)
    expect(latest.nodes[0].config).toMatchObject({ task: 'Do the thing', future_key: 7, provider: 'sglang' })
  })

  it('clearing an optional field removes the key instead of saving an empty string', () => {
    const seen = vi.fn()
    render(<Host initial={docsExample()} nodeId="fix" onChange={seen} />)
    fireEvent.change(screen.getByLabelText(/^Provider/), { target: { value: '' } })
    expect('provider' in (lastArg<WfDef>(seen).nodes[0].config ?? {})).toBe(false)
  })

  it('the value inserter offers only what is valid for this step and inserts a token', () => {
    const seen = vi.fn()
    render(<Host initial={docsExample()} nodeId="post" onChange={seen} />)
    const task = screen.getByLabelText('Insert a value into Details value 2') as HTMLSelectElement
    const options = within(task).getAllByRole('option').map(o => (o as HTMLOptionElement).value)
    expect(options).toContain('nodes.fix.output.verdict')
    expect(options).toContain('vars.repo')
    expect(options).not.toContain('nodes.post.output')
    fireEvent.change(task, { target: { value: 'vars.repo' } })
    const params = lastArg<WfDef>(seen).nodes[3].config?.params as Record<string, string>
    expect(params.body).toBe('...{{vars.repo}}')
  })

  it('an action shows its side effect and the human-gate warning in plain words', () => {
    render(<Host initial={docsExample()} nodeId="post" />)
    expect(screen.getByText('Changes something outside PatchQuest')).toBeTruthy()
    expect(screen.getByText('Needs GitHub')).toBeTruthy()
    expect(screen.getByText(/This step writes to GitHub, so a person must approve first\./)).toBeTruthy()
    const select = screen.getByLabelText('Action') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'notify.log' } })
    expect(screen.queryByText(/a person must approve first/)).toBeNull()
  })

  it('a condition is edited with a structured builder, including all/any groups', () => {
    const seen = vi.fn()
    render(<Host initial={docsExample()} nodeId="ok" onChange={seen} />)
    fireEvent.change(screen.getByLabelText('Comparison'), { target: { value: 'ne' } })
    expect((lastArg<WfDef>(seen).nodes[1].config?.if as Record<string, unknown>).op).toBe('ne')
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'all' } })
    const cond = lastArg<WfDef>(seen).nodes[1].config?.if as { all: unknown[] }
    expect(cond.all).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Add a comparison' }))
    expect((lastArg<WfDef>(seen).nodes[1].config?.if as { all: unknown[] }).all).toHaveLength(2)
  })

  it('the "exists" comparison drops the right-hand side', () => {
    const seen = vi.fn()
    render(<Host initial={docsExample()} nodeId="ok" onChange={seen} />)
    fireEvent.change(screen.getByLabelText('Comparison'), { target: { value: 'exists' } })
    const cond = lastArg<WfDef>(seen).nodes[1].config?.if as Record<string, unknown>
    expect(cond).toEqual({ left: '{{nodes.fix.output.verdict}}', op: 'exists' })
    expect(screen.queryByLabelText('Compare with')).toBeNull()
  })

  it('an approval timeout is entered in friendly units and stored as seconds', () => {
    const seen = vi.fn()
    render(<Host initial={docsExample()} nodeId="review" onChange={seen} />)
    expect((screen.getByLabelText('Give up after unit') as HTMLSelectElement).value).toBe('days')
    fireEvent.change(screen.getByLabelText('Give up after unit'), { target: { value: 'hours' } })
    expect(lastArg<WfDef>(seen).nodes[2].config?.timeout_s).toBe(3600)
    fireEvent.change(screen.getByLabelText(/^Give up after( optional)?$/), { target: { value: '2' } })
    expect(lastArg<WfDef>(seen).nodes[2].config?.timeout_s).toBe(7200)
  })

  it('overrides only accept agent.* keys', () => {
    render(<Host initial={{ ...docsExample(), nodes: [{ id: 'a', type: 'agent', config: { task: 't', overrides: { 'safety.x': 1 } } }], edges: [] }} nodeId="a" />)
    expect(screen.getByText('Only agent.* settings can be changed.')).toBeTruthy()
  })

  it('renaming a step updates connections and is refused when the name is taken', () => {
    const seen = vi.fn()
    render(<Host initial={docsExample()} nodeId="fix" onChange={seen} />)
    const input = screen.getByLabelText('Step name')
    fireEvent.change(input, { target: { value: 'ok' } })
    fireEvent.blur(input)
    expect(screen.getByText(/already called "ok"/)).toBeTruthy()
    expect(seen).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: 'investigate' } })
    fireEvent.blur(input)
    expect(lastArg<WfDef>(seen).edges[0].from).toBe('investigate')
  })
})

describe('connections (the keyboard way to wire steps)', () => {
  it('connects to a chosen step with the next free branch label and explains refusals', () => {
    const def: WfDef = { ...docsExample(), edges: [] }
    const seen = vi.fn()
    function H() {
      const [d, setD] = useState(def)
      return <Connections def={d} node={d.nodes[1]} onDef={x => { setD(x); seen(x) }} />
    }
    render(<H />)
    fireEvent.change(screen.getByLabelText('Connect to'), { target: { value: 'review' } })
    expect((screen.getByLabelText('When') as HTMLSelectElement).value).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(lastArg<WfDef>(seen).edges).toEqual([{ from: 'ok', to: 'review', when: 'true' }])
    // the same branch again is refused with a reason
    fireEvent.change(screen.getByLabelText('Connect to'), { target: { value: 'done' } })
    fireEvent.change(screen.getByLabelText('When'), { target: { value: 'true' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(screen.getByRole('alert').textContent).toMatch(/already leads/)
  })

  it('removes a connection', () => {
    const seen = vi.fn()
    render(<Connections def={docsExample()} node={docsExample().nodes[2]} onDef={seen} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove connection to post' }))
    expect(lastArg<WfDef>(seen).edges.some(e => e.from === 'review' && e.to === 'post')).toBe(false)
  })
})

describe('workflow settings', () => {
  it('edits the trigger filter as structured rows', () => {
    const seen = vi.fn()
    function H() {
      const [d, setD] = useState(docsExample())
      return <WorkflowSettings def={d} onDef={x => { setD(x); seen(x) }} />
    }
    render(<H />)
    expect((screen.getByLabelText('Event field 1') as HTMLInputElement).value).toBe('payload.label')
    fireEvent.change(screen.getByLabelText('Value 1'), { target: { value: 'ready' } })
    expect(lastArg<WfDef>(seen).trigger?.filter).toEqual({ 'payload.label': 'ready' })
    fireEvent.click(screen.getByRole('button', { name: 'Add a match' }))
    expect(screen.getByLabelText('Event field 2')).toBeTruthy() // the blank row is shown
    expect(seen).toHaveBeenCalledTimes(1) // but not written until it has a path
    fireEvent.change(screen.getByLabelText('Event field 2'), { target: { value: 'payload.n' } })
    fireEvent.change(screen.getByLabelText('Value 2'), { target: { value: '3' } })
    expect(lastArg<WfDef>(seen).trigger?.filter).toMatchObject({ 'payload.n': 3 })
  })

  it('hides the filter for manual triggers and edits variables', () => {
    const seen = vi.fn()
    const def = { ...docsExample(), trigger: { type: 'manual' } }
    render(<WorkflowSettings def={def} onDef={seen} />)
    expect(screen.queryByText('Only when the event matches')).toBeNull()
    fireEvent.change(screen.getByLabelText(/^Default for repo/), { target: { value: '/x' } })
    expect(lastArg<WfDef>(seen).variables).toEqual({ repo: { default: '/x' } })
    fireEvent.click(screen.getByRole('switch', { name: 'Required' }))
    expect(lastArg<WfDef>(seen).variables?.repo.required).toBe(true)
  })
})

describe('palette, problems and list view', () => {
  it('lists every node type and adds one on click', () => {
    const onAdd = vi.fn()
    render(<Palette onAdd={onAdd} />)
    expect(screen.getAllByRole('button').map(b => b.querySelector('.wf-palette__label')?.textContent)).toEqual(['Agent', 'Action', 'Condition', 'Approval', 'Wait for event', 'Timer', 'End'])
    fireEvent.click(screen.getByRole('button', { name: /Approval/ }))
    expect(onAdd).toHaveBeenCalledWith('approval')
  })

  it('palette items are draggable and carry the node type', () => {
    render(<Palette onAdd={() => {}} />)
    const item = screen.getByRole('button', { name: /Timer/ })
    expect(item.getAttribute('draggable')).toBe('true')
    const data: Record<string, string> = {}
    fireEvent.dragStart(item, { dataTransfer: { setData: (k: string, v: string) => (data[k] = v), effectAllowed: '' } })
    expect(data['application/x-patchquest-node']).toBe('timer')
  })

  it('a disabled palette cannot add steps', () => {
    const onAdd = vi.fn()
    render(<Palette onAdd={onAdd} disabled />)
    fireEvent.click(screen.getByRole('button', { name: /Agent/ }))
    expect(onAdd).not.toHaveBeenCalled()
  })

  it('problems are clickable and select the offending step', () => {
    const onSelect = vi.fn()
    render(<ProblemsList checking={false} ok={false} onSelect={onSelect} problems={[{ code: 'missing_config', message: "node 'a': an agent node needs a 'task'", node: 'a' }, { code: 'name', message: 'bad name', node: null }]} />)
    expect(screen.getByRole('status').textContent).toMatch(/2 problems to fix/)
    fireEvent.click(screen.getByRole('button', { name: /needs a 'task'/ }))
    expect(onSelect).toHaveBeenCalledWith('a')
    expect(screen.getByText('bad name').closest('button')).toBeNull()
  })

  it('says so when everything is fine and when the server is still checking', () => {
    const { rerender } = render(<ProblemsList checking ok={false} onSelect={() => {}} problems={[]} />)
    expect(screen.getByRole('status').textContent).toMatch(/Checking/)
    rerender(<ProblemsList checking={false} ok onSelect={() => {}} problems={[]} />)
    expect(screen.getByRole('status').textContent).toMatch(/server accepts/)
  })

  it('the list view describes every step and where it leads, and selects on click', () => {
    const onSelect = vi.fn()
    render(<NodeListView def={docsExample()} selection={new Set(['ok'])} problems={new Map()} actions={new Map()} onSelect={onSelect} onOpen={() => {}} />)
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(5)
    expect(items[1].textContent).toContain('Leads to review (if true), done (if false)')
    expect(items[4].textContent).toContain('Finishes the workflow')
    fireEvent.click(within(items[3]).getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('post')
    expect(within(items[1]).getByRole('button').getAttribute('aria-pressed')).toBe('true')
  })
})

const lastViewport = (fn: unknown) => lastArg<{ zoom: number }>(fn)

describe('canvas keyboard and accessibility', () => {
  function setup(extra: Partial<React.ComponentProps<typeof Canvas>> = {}) {
    const def = docsExample()
    const props = {
      def, positions: ensureLayout(def, undefined), selection: new Set<string>(), viewport: { x: 0, y: 0, zoom: 1 }, onViewport: vi.fn(), onSelect: vi.fn(), onOpenConfig: vi.fn(), onKeyDown: vi.fn(), ...extra,
    }
    render(<Canvas {...props} />)
    return props
  }

  it('every step is a focusable button in definition order, labelled with what it does', () => {
    setup()
    const nodes = screen.getAllByRole('button', { name: /step/ })
    expect(nodes.map(n => n.getAttribute('data-node-id'))).toEqual(['fix', 'ok', 'review', 'post', 'done'])
    expect(nodes.every(n => n.getAttribute('tabindex') === '0')).toBe(true)
    expect(nodes[2].getAttribute('aria-label')).toMatch(/^Approval step review\. Post it\?\./)
  })

  it('Enter opens the settings of the focused step; Space selects it', () => {
    const props = setup()
    const node = document.querySelector('[data-node-id="ok"]') as HTMLElement
    fireEvent.keyDown(node, { key: 'Enter' })
    expect(props.onOpenConfig).toHaveBeenCalledWith('ok')
    fireEvent.keyDown(node, { key: ' ' })
    expect(props.onSelect).toHaveBeenCalledWith(['ok'], false)
  })

  it('focusing a step selects it, and key presses reach the editor handler', () => {
    const props = setup()
    const node = document.querySelector('[data-node-id="post"]') as HTMLElement
    node.focus()
    expect(props.onSelect).toHaveBeenCalledWith(['post'], false)
    fireEvent.keyDown(node, { key: 'ArrowRight' })
    expect(props.onKeyDown).toHaveBeenCalled()
  })

  it('zoom buttons and fit report new viewports', () => {
    const props = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(lastViewport(props.onViewport).zoom).toBeGreaterThan(1)
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }))
    expect(lastViewport(props.onViewport).zoom).toBeLessThan(1)
    fireEvent.click(screen.getByRole('button', { name: 'Fit to screen' }))
    expect(lastViewport(props.onViewport).zoom).toBeLessThanOrEqual(1)
  })

  it('read-only mode marks the canvas so ports are hidden and nothing is draggable', () => {
    setup({ readOnly: true })
    expect(document.querySelector('.wf-canvas--readonly')).toBeTruthy()
    expect(document.querySelectorAll('[data-port="out"]').length).toBe(4) // end has none; CSS hides them in read-only
  })

  it('shows problem badges on the offending step and an action step flags its approval gate', () => {
    const def = docsExample()
    setup({ problems: new Map([['post', [{ code: 'missing_approval', message: 'needs gate', node: 'post' }]]]), actions: new Map(ACTIONS.map(a => [a.name, a])) })
    const post = document.querySelector('[data-node-id="post"]') as HTMLElement
    expect(post.className).toContain('wf-node--problem')
    expect(post.getAttribute('aria-label')).toContain('1 problem.')
    expect(post.textContent).toContain('needs approval gate')
    void def
  })
})
