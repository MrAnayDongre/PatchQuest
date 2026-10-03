import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from '../../src/App'
import { ThemeProvider } from '../../src/theme/ThemeProvider'
import { FakeEventSource, mockFetch, type MockRoute } from '../components/testUtils'
import { ACTIONS, docsExample } from '../features/wfHelpers'
import { loadDraft } from '../../src/lib/workflow/storage'
import type { WfDef } from '../../src/lib/workflow/types'

const validateByRule = (_u: string, init?: RequestInit) => {
  const def = JSON.parse(String(init?.body)).definition as WfDef
  const problems = def.nodes.filter(n => n.type === 'agent' && !(n.config as { task?: string }).task).map(n => ({ code: 'missing_config', message: `node '${n.id}': an agent node needs a 'task'`, node: n.id }))
  if (!def.nodes.length) problems.push({ code: 'nodes', message: 'a workflow needs at least one node', node: null as unknown as string })
  return { ok: problems.length === 0, problems }
}

const base: MockRoute[] = [
  { match: /\/api\/health$/, body: { status: 'ok', version: '1' } },
  { match: /\/api\/runs(\?.*)?$/, body: [] },
  { match: /\/api\/providers/, body: [] },
  { match: /\/api\/workflows\/actions$/, body: ACTIONS },
  { match: /\/api\/workflows\/validate$/, method: 'POST', body: validateByRule },
  { match: /\/api\/workflows$/, body: [] },
]

function renderApp(hash: string) {
  window.location.hash = hash
  return render(<ThemeProvider><App /></ThemeProvider>)
}

const nodeEl = (id: string) => document.querySelector(`[data-node-id="${id}"]`) as HTMLElement
const pal = (name: string) => within(screen.getByRole('navigation', { name: 'Add a step' })).getByRole('button', { name: new RegExp(`^${name}`) })
const canvas = () => document.querySelector('.wf-canvas') as HTMLElement

beforeEach(() => {
  localStorage.clear()
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('workflows list', () => {
  it('shows an empty state with both ways to start', async () => {
    mockFetch([...base, { match: /\/api\/workflows\/runs$/, body: [] }])
    renderApp('#/workflows')
    expect(await screen.findByText('No workflows yet')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Build one from scratch' }).getAttribute('href')).toBe('#/workflows/new')
  })

  it('lists workflows with trigger, version and last run, and opens the template gallery with honest connector notes', async () => {
    mockFetch([
      ...base.filter(r => !/workflows\$/.test(String(r.match))),
      { match: /\/api\/workflows\/runs$/, body: [{ id: 'wr1', workflow_id: 'wf1', workspace_id: 'ws', status: 'waiting', created_by: 'a', created_at: new Date().toISOString(), completed_at: null, error: null }] },
      { match: /\/api\/workflows\/templates$/, body: [{ name: 'issue-to-proposal', description: 'Turn a labelled issue into a proposal comment', trigger: 'github.issues.labeled', variables: {}, requires: ['github'] }] },
      { match: /\/api\/workflows$/, body: [{ id: 'wf1', workspace_id: 'ws', name: 'nightly-fix', version: 4, trigger_type: 'manual', created_at: '2026-01-01T00:00:00Z', description: 'Fix flaky tests' }] },
    ])
    renderApp('#/workflows')
    const row = (await within(await screen.findByRole('list', { name: 'Workflows' })).findAllByRole('link'))[0]
    expect(row.textContent).toContain('Version 4')
    expect(row.textContent).toContain('Started by hand')
    expect(row.textContent).toContain('Waiting')
    fireEvent.click(screen.getByRole('button', { name: 'From template' }))
    expect(await screen.findByText('needs: github')).toBeTruthy()
    expect(screen.getByText(/can't yet show whether a connection such as GitHub is set up/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Use this template' }).getAttribute('href')).toBe('#/workflows/new?template=issue-to-proposal')
  })

  it('explains a permission problem instead of showing a blank page', async () => {
    mockFetch([...base.filter(r => !/workflows\$/.test(String(r.match))), { match: /\/api\/workflows\/runs$/, status: 403, body: { detail: { code: 'forbidden', message: 'no' } } }, { match: /\/api\/workflows$/, status: 403, body: { detail: { code: 'forbidden', message: 'no' } } }])
    renderApp('#/workflows')
    expect(await screen.findByText("Your role can't see workflows here")).toBeTruthy()
  })
})

describe('builder', () => {
  it('builds a workflow with the keyboard-driven commands, validates it live and saves a version', async () => {
    const calls = mockFetch([
      ...base.filter(r => !/workflows\$/.test(String(r.match))),
      { match: /\/api\/workflows$/, method: 'POST', body: { id: 'wf_new', name: 'my-flow', version: 1, workspace_id: 'ws' } },
      { match: /\/api\/workflows\/wf_new$/, body: () => ({ id: 'wf_new', name: 'my-flow', version: 1, workspace_id: 'ws', created_at: '2026-01-01T00:00:00Z', definition: JSON.parse(JSON.stringify(savedBody)) }) },
      { match: /\/api\/workflows$/, body: [] },
    ])
    let savedBody: WfDef = { nodes: [], edges: [] }
    renderApp('#/workflows/new')
    await screen.findByRole('application')

    // name the workflow
    fireEvent.click(screen.getByRole('tab', { name: 'Workflow' }))
    fireEvent.change(screen.getByLabelText('Workflow name'), { target: { value: 'my-flow' } })

    // add steps from the palette (works with the keyboard: they are buttons)
    fireEvent.click(pal('Agent'))
    expect(nodeEl('agent')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'Fix {{trigger.type}}' } })
    fireEvent.click(pal('Approval'))
    fireEvent.click(pal('End'))
    expect(['agent', 'approve', 'done'].every(id => nodeEl(id))).toBe(true)

    // the server says there is still a problem until it is fixed: nothing to save yet
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(false), { timeout: 4000 })

    // connect agent -> approval with the "connect from selected to…" command
    nodeEl('agent').focus()
    fireEvent.keyDown(nodeEl('agent'), { key: 'c' })
    let dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Connect to'), { target: { value: 'approve' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))

    // approval -> end, as "Approved"
    nodeEl('approve').focus()
    fireEvent.keyDown(nodeEl('approve'), { key: 'c' })
    dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Connect to'), { target: { value: 'done' } })
    expect((within(dialog).getByLabelText('When') as HTMLSelectElement).value).toBe('approved')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))

    // saved only when the server's verdict for THIS definition is in
    const save = screen.getByRole('button', { name: 'Save' })
    await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false), { timeout: 4000 })
    const validations = calls.filter(c => c.url.endsWith('/validate')).length
    expect(validations).toBeGreaterThan(0)
    fireEvent.click(save)
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && /\/api\/workflows$/.test(c.url))).toBe(true))
    const body = calls.find(c => c.method === 'POST' && /\/api\/workflows$/.test(c.url))!.body as { definition: WfDef }
    savedBody = body.definition
    expect(body.definition.name).toBe('my-flow')
    expect(body.definition.nodes.map(n => n.id)).toEqual(['agent', 'approve', 'done'])
    expect(body.definition.nodes[0].config).toMatchObject({ task: 'Fix {{trigger.type}}' })
    expect(body.definition.edges).toEqual([{ from: 'agent', to: 'approve' }, { from: 'approve', to: 'done', when: 'approved' }])
    // node positions travel only in the server-side `layout` map, one {x, y} per node, never on the nodes
    expect(Object.keys(body.definition.layout ?? {}).sort()).toEqual(['agent', 'approve', 'done'])
    expect(JSON.stringify(body.definition.nodes)).not.toMatch(/"(x|y|position)"/)
    await waitFor(() => expect(window.location.hash).toBe('#/workflows/wf_new'))
  })

  it('shows server problems on the offending step and in the Problems list, and Save stays disabled', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Agent'))
    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true)
    await waitFor(() => expect(nodeEl('agent').className).toContain('wf-node--problem'), { timeout: 4000 })
    fireEvent.click(screen.getByRole('tab', { name: /Problems/ }))
    await waitFor(() => expect(screen.getByText(/an agent node needs a 'task'/)).toBeTruthy(), { timeout: 4000 })
    expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true)
    // clicking a problem selects the step and shows its settings
    fireEvent.click(screen.getByRole('button', { name: /an agent node needs a 'task'/ }))
    expect(screen.getByLabelText('Task')).toBeTruthy()
    // drafting is never blocked
    fireEvent.change(screen.getByLabelText('Task'), { target: { value: 'ok now' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(false), { timeout: 4000 })
  })

  it('undo and redo work from the keyboard and the toolbar', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Agent'))
    fireEvent.click(pal('Timer'))
    expect(nodeEl('timer')).toBeTruthy()
    fireEvent.keyDown(document.body, { key: 'z', ctrlKey: true })
    expect(nodeEl('timer')).toBeNull()
    expect(nodeEl('agent')).toBeTruthy()
    fireEvent.keyDown(document.body, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(nodeEl('timer')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(nodeEl('timer')).toBeNull()
  })

  it('keyboard: arrows move the selection, Delete removes it, Ctrl+D duplicates it, Enter opens its settings', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Timer'))
    const left = () => parseInt(nodeEl('timer').style.left, 10)
    nodeEl('timer').focus()
    const start = left()
    fireEvent.keyDown(nodeEl('timer'), { key: 'ArrowRight' })
    expect(left()).toBe(start + 20)
    fireEvent.keyDown(nodeEl('timer'), { key: 'ArrowLeft', shiftKey: true })
    expect(left()).toBe(start - 80)
    fireEvent.keyDown(nodeEl('timer'), { key: 'd', ctrlKey: true })
    expect(nodeEl('timer_copy')).toBeTruthy()
    fireEvent.keyDown(nodeEl('timer_copy'), { key: 'Delete' })
    expect(nodeEl('timer_copy')).toBeNull()
    expect(nodeEl('timer')).toBeTruthy()
    nodeEl('timer').focus()
    fireEvent.keyDown(nodeEl('timer'), { key: 'Enter' })
    await waitFor(() => expect(screen.getByLabelText('Wait for')).toBeTruthy())
  })

  it('the list view is a complete alternative to the canvas', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Agent'))
    fireEvent.click(screen.getByRole('button', { name: 'List' }))
    expect(screen.queryByRole('application')).toBeNull()
    const list = screen.getByRole('list', { name: 'Steps in this workflow' })
    expect(within(list).getByText('agent')).toBeTruthy()
    fireEvent.click(within(list).getByRole('button', { name: /agent/ }))
    fireEvent.click(screen.getByRole('tab', { name: 'Step' }))
    expect(screen.getByLabelText('Task')).toBeTruthy()
  })

  it('imports JSON and YAML, keeps unknown fields, and exports what the server will store', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    const def = { ...docsExample(), x_future: { keep: true } }
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    fireEvent.change(screen.getByLabelText('Workflow file'), { target: { value: JSON.stringify(def) } })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(nodeEl('review')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Export' }))
    const code = await screen.findByLabelText('issue-to-proposal.json')
    const { layout, ...exported } = JSON.parse(code.textContent ?? '')
    expect(exported).toEqual(def)
    expect(Object.keys(layout).sort()).toEqual(def.nodes.map(n => n.id).sort()) // one position per node
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }))
    expect((await screen.findByLabelText('issue-to-proposal.yaml')).textContent).toContain('- id: review')
  })

  it('refuses a broken import with a plain message and leaves the workflow alone', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Agent'))
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    fireEvent.change(screen.getByLabelText('Workflow file'), { target: { value: '{"name": ' } })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Import' }))
    expect((await within(screen.getByRole('dialog')).findByRole('alert')).textContent).toMatch(/valid JSON/)
    expect(nodeEl('agent')).toBeTruthy()
  })

  it('keeps an unsaved draft in the browser and warns before leaving', async () => {
    mockFetch(base)
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Agent'))
    await waitFor(() => expect(loadDraft('new')?.def.nodes).toHaveLength(1), { timeout: 3000 })
    // leaving the page is intercepted and the hash is restored until the user decides
    window.location.hash = '#/runs'
    expect(await screen.findByText('Leave without saving?')).toBeTruthy()
    expect(screen.getByRole('application')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(window.location.hash).toBe('#/workflows/new')
    window.location.hash = '#/runs'
    await screen.findByText('Leave without saving?')
    fireEvent.click(screen.getByRole('button', { name: 'Leave' }))
    await waitFor(() => expect(window.location.hash).toBe('#/runs'))
  })

  it('starts from a template and says what connection it needs', async () => {
    mockFetch([...base, { match: /\/api\/workflows\/templates\/issue-to-proposal$/, body: { definition: docsExample(), requires: ['github'] } }])
    renderApp('#/workflows/new?template=issue-to-proposal')
    expect(await screen.findByText('This template needs: github')).toBeTruthy()
    expect(nodeEl('post')).toBeTruthy()
    // the document is an unsaved draft until saved
    expect(screen.getByText('Unsaved changes')).toBeTruthy()
  })

  it('a saved version opens, can be test-run with its variables, and an older version is read-only until restored', async () => {
    const calls = mockFetch([
      ...base.filter(r => !/workflows\$/.test(String(r.match))),
      { match: /\/api\/workflows$/, body: [{ id: 'wf2', workspace_id: 'ws', name: 'issue-to-proposal', version: 2, trigger_type: 'manual', created_at: '', description: '' }] },
      { match: /\/api\/workflows\/wf1$/, body: { id: 'wf1', name: 'issue-to-proposal', version: 1, workspace_id: 'ws', created_at: '2026-01-01T00:00:00Z', definition: { ...docsExample(), trigger: { type: 'manual' }, variables: { repo: { required: true } } } } },
      { match: /\/api\/workflows\/wf1\/runs$/, method: 'POST', body: { run_id: 'wr9' } },
      { match: /\/api\/workflows\/wf1\/versions$/, body: [{ id: 'wf2', version: 2, status: 'active', created_by: 'a', created_at: '' }, { id: 'wf1', version: 1, status: 'active', created_by: 'a', created_at: '' }] },
    ])
    renderApp('#/workflows/wf1')
    await screen.findByText('You are looking at an older version')
    expect(pal('Agent').hasAttribute('disabled')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Test run' }))
    const dialog = await screen.findByRole('dialog')
    const start = within(dialog).getByRole('button', { name: 'Start run' })
    expect(start.hasAttribute('disabled')).toBe(true) // required variable is empty
    fireEvent.change(within(dialog).getByLabelText('repo'), { target: { value: '/work/app' } })
    fireEvent.click(start)
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/wf1/runs'))).toBe(true))
    expect(calls.find(c => c.url.endsWith('/wf1/runs'))!.body).toEqual({ variables: { repo: '/work/app' }, payload: {} })
    await waitFor(() => expect(window.location.hash).toBe('#/workflows/runs/wr9'))
  })

  it('a role that cannot define workflows gets an explanation when saving', async () => {
    mockFetch([
      ...base.filter(r => !/workflows\$/.test(String(r.match))),
      { match: /\/api\/workflows$/, method: 'POST', status: 403, body: { detail: { code: 'forbidden', message: 'workflow.manage is not permitted in this workspace' } } },
      { match: /\/api\/workflows$/, body: [] },
    ])
    renderApp('#/workflows/new')
    await screen.findByRole('application')
    fireEvent.click(pal('Timer'))
    fireEvent.click(screen.getByRole('tab', { name: 'Workflow' }))
    fireEvent.change(screen.getByLabelText('Workflow name'), { target: { value: 'timer-flow' } })
    const save = screen.getByRole('button', { name: 'Save' })
    await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false), { timeout: 4000 })
    fireEvent.click(save)
    expect(await screen.findByText("Your role can't change workflows")).toBeTruthy()
  })

  it('on a phone-width screen the builder is read-only with a clear message', async () => {
    const original = window.matchMedia
    window.matchMedia = ((q: string) => ({ matches: q.includes('max-width: 639px'), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false, onchange: null })) as unknown as typeof window.matchMedia
    try {
      mockFetch([...base, { match: /\/api\/workflows\/templates\/issue-to-proposal$/, body: { definition: docsExample(), requires: [] } }])
      renderApp('#/workflows/new?template=issue-to-proposal')
      expect(await screen.findByText('Editing needs a larger screen')).toBeTruthy()
      expect(pal('Agent').hasAttribute('disabled')).toBe(true)
      expect(screen.getByRole('button', { name: 'Save' }).hasAttribute('disabled')).toBe(true)
      expect(nodeEl('review')).toBeTruthy()
    } finally {
      window.matchMedia = original
    }
  })
})
