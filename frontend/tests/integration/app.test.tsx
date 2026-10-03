import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import App from '../../src/App'
import { ThemeProvider } from '../../src/theme/ThemeProvider'
import { happyRun } from '../features/helpers'
import { FakeEventSource, mockFetch, type MockRoute } from '../components/testUtils'

const run = (id: string, status: string, extra: Record<string, unknown> = {}) => ({
  id, repo_path: '/home/me/webapp', task: `Task for ${id}`, status, current_phase: null, provider: 'mock', model: null, runtime_mode: 'local',
  created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:05:00Z', completed_at: null, ...extra,
})

const common: MockRoute[] = [
  { match: /\/api\/health$/, body: { status: 'ok', version: '1' } },
  { match: /\/api\/providers\/engines/, body: [] },
  { match: /\/api\/providers\/status/, body: [] },
  { match: /\/api\/providers$/, body: [] },
]

function renderApp(hash: string) {
  window.location.hash = hash
  return render(
    <ThemeProvider>
      <App />
    </ThemeProvider>,
  )
}

beforeEach(() => {
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource)
  window.matchMedia = window.matchMedia ?? ((() => ({ matches: false, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('app', () => {
  it('home shows an honest empty state and onboarding when there are no runs', async () => {
    mockFetch([...common, { match: /\/api\/runs(\?.*)?$/, body: [] }])
    renderApp('#/')
    await screen.findByText('Try your first run')
    expect(screen.getByRole('button', { name: 'Run the sample task' })).toBeTruthy()
    expect(screen.queryByText('Success rate')).toBeNull()
  })

  it('home surfaces runs that need the user, with computed metrics', async () => {
    mockFetch([
      ...common,
      {
        match: /\/api\/runs(\?.*)?$/,
        body: [
          run('a', 'waiting_approval'),
          run('b', 'completed', { completed_at: '2026-01-01T10:02:00Z', outcome: 'applied', verdict: 'passed' }),
          run('c', 'failed', { completed_at: '2026-01-01T10:03:00Z', failure_kind: 'MODEL_TIMEOUT' }),
        ],
      },
      { match: /\/api\/runs\/a\/approvals$/, body: [{ id: 'ap1', type: 'command', command: 'pytest', reason: 'Runs code', side_effect: 'WORKSPACE_WRITE', risk: 'low', phase: 'testing', expires_at: null, created_at: '2026-01-01T10:04:00Z' }] },
      { match: /\/api\/runs\/c\/events/, body: [{ id: 1, run_id: 'c', type: 'run_failed', phase: null, status: null, message: 'x', payload: { failure: { kind: 'MODEL_TIMEOUT', retryable: true, severity: 'error', origin: 'model', message: 'The model took too long to answer.', recovery: ['Resume from the last checkpoint'], detail: '' } }, created_at: '2026-01-01T10:03:00Z' }] },
    ])
    renderApp('#/')
    await screen.findByText('1 run needs you.')
    await screen.findByText('pytest')
    expect(screen.getByRole('button', { name: /Approve once/ })).toBeTruthy()
    expect(await screen.findByText('The model took too long to answer.')).toBeTruthy()
    expect(screen.getByText('50%')).toBeTruthy() // 1 completed of 2 decided
    expect(screen.getByText('Resume from the last checkpoint')).toBeTruthy()
  })

  it('runs list filters by search text', async () => {
    mockFetch([...common, { match: /\/api\/runs(\?.*)?$/, body: [run('a', 'completed', { task: 'Fix login' }), run('b', 'completed', { task: 'Add docs' })] }])
    renderApp('#/runs')
    await screen.findByText('Fix login')
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'docs' } })
    expect(screen.queryByText('Fix login')).toBeNull()
    expect(screen.getByText('Add docs')).toBeTruthy()
  })

  it('runs list supports j/k and Enter', async () => {
    mockFetch([...common, { match: /\/api\/runs(\?.*)?$/, body: [run('a', 'completed', { task: 'First', updated_at: '2026-01-02T00:00:00Z' }), run('b', 'completed', { task: 'Second' })] }])
    renderApp('#/runs')
    await screen.findByText('First')
    fireEvent.keyDown(document.body, { key: 'j' })
    fireEvent.keyDown(document.body, { key: 'j' })
    fireEvent.keyDown(document.body, { key: 'Enter' })
    await waitFor(() => expect(window.location.hash).toBe('#/runs/b'))
  })

  it('run view reconstructs approvals, phases and the timeline from the ledger', async () => {
    const events = happyRun().slice(0, 12)
    mockFetch([
      ...common,
      { match: /\/api\/runs(\?.*)?$/, body: [run('r1', 'waiting_approval')] },
      { match: /\/api\/runs\/r1$/, body: run('r1', 'waiting_approval') },
      { match: /\/api\/runs\/r1\/events/, body: events },
      { match: /\/api\/runs\/r1\/approvals\/ap1$/, method: 'POST', body: { status: 'approved', decision: 'APPROVE_ONCE' } },
      { match: /\/checkpoints$/, body: [] },
      { match: /\/lineage$/, body: { ancestry: [], children: [] } },
    ])
    renderApp('#/runs/r1')
    expect(await screen.findByRole('heading', { level: 1, name: 'Task for r1' })).toBeTruthy()
    const panel = await screen.findByRole('region', { name: 'Waiting for your approval' })
    expect(within(panel).getByText('pytest')).toBeTruthy()
    expect(within(panel).getByRole('button', { name: /Approve for this run/ })).toBeTruthy()
    expect(screen.getByRole('list', { name: 'Run phases' })).toBeTruthy()
    expect(screen.getByText(/approval is waiting for you/)).toBeTruthy()
    expect(screen.getByText('Budget')).toBeTruthy()
    expect(FakeEventSource.instances.length).toBe(1)
  })

  it('command palette opens with Ctrl+K and navigates', async () => {
    mockFetch([...common, { match: /\/api\/runs(\?.*)?$/, body: [run('a', 'completed', { task: 'Fix login' })] }])
    renderApp('#/')
    await screen.findByText('Fix login')
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const input = await screen.findByRole('combobox')
    fireEvent.change(input, { target: { value: 'engines' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(window.location.hash).toBe('#/engines'))
  })

  it('shows a friendly page for unknown routes', async () => {
    mockFetch([...common, { match: /\/api\/runs(\?.*)?$/, body: [] }])
    renderApp('#/nope')
    expect(await screen.findByText('Page not found')).toBeTruthy()
  })

  it('shows the 404 for a missing run in plain words', async () => {
    mockFetch([...common, { match: /\/api\/runs(\?.*)?$/, body: [] }, { match: /\/api\/runs\/zzz/, status: 404, body: { detail: 'Run not found' } }])
    renderApp('#/runs/zzz')
    expect(await screen.findByText('Not found')).toBeTruthy()
    expect(screen.getByText('Back to all runs')).toBeTruthy()
  })
})
