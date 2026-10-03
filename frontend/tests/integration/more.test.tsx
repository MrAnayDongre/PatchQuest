import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../../src/App'
import { ThemeProvider } from '../../src/theme/ThemeProvider'
import { FakeEventSource, mockFetch, type MockRoute } from '../components/testUtils'

const run = (id: string, status: string, created: string, extra: Record<string, unknown> = {}) => ({
  id, repo_path: '/r/app', task: `Task ${id}`, status, current_phase: null, provider: 'mock', model: null, runtime_mode: 'local',
  created_at: created, updated_at: created, completed_at: null, ...extra,
})
const common: MockRoute[] = [
  { match: /\/api\/health$/, body: { status: 'ok', version: '1' } },
  { match: /\/api\/providers/, body: [] },
]
const renderApp = (hash: string) => {
  window.location.hash = hash
  return render(<ThemeProvider><App /></ThemeProvider>)
}

beforeEach(() => {
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('queued runs in the UI', () => {
  it('shows a queued run as working, with a cancel action and an open stream', async () => {
    mockFetch([
      ...common,
      { match: /\/api\/runs(\?.*)?$/, body: [run('q1', 'queued', '2026-01-01T10:00:00Z')] },
      { match: /\/api\/runs\/q1$/, body: run('q1', 'queued', '2026-01-01T10:00:00Z') },
      { match: /\/api\/runs\/q1\/events/, body: [{ id: 1, run_id: 'q1', type: 'run_state_changed', phase: null, status: 'queued', message: 'queued for a worker', payload: { from: 'created', to: 'queued' }, created_at: '2026-01-01T10:00:00Z' }] },
      { match: /\/checkpoints$/, body: [] },
      { match: /\/lineage$/, body: { ancestry: [], children: [] } },
    ])
    renderApp('#/')
    await screen.findByText('1 run is working. Nothing needs you.')
    cleanup()
    renderApp('#/runs/q1')
    expect(await screen.findByRole('button', { name: /Cancel run/ })).toBeTruthy()
    expect(screen.getAllByText('Waiting for a worker').length).toBeGreaterThan(0)
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
    expect(FakeEventSource.last.closed).toBe(false)
  })
})

describe('runs paging', () => {
  it('requests the newest page and loads older runs with `before`', async () => {
    const page1 = Array.from({ length: 200 }, (_, i) => run(`n${i}`, 'completed', `2026-02-01T00:${String(59 - (i % 60)).padStart(2, '0')}:00Z`))
    const calls = mockFetch([
      ...common,
      { match: /\/api\/runs\?limit=200&before=/, body: [run('old1', 'completed', '2025-01-01T00:00:00Z', { task: 'Ancient task' })] },
      { match: /\/api\/runs\?limit=200$/, body: page1 },
    ])
    renderApp('#/runs')
    const more = await screen.findByRole('button', { name: 'Load older runs' })
    expect(calls.some(c => c.url === '/api/runs?limit=200')).toBe(true)
    fireEvent.click(more)
    await screen.findByText('Ancient task')
    const beforeCall = calls.find(c => c.url.includes('before='))!
    expect(decodeURIComponent(beforeCall.url)).toContain('before=')
    expect(screen.queryByRole('button', { name: 'Load older runs' })).toBeNull()
  })
})

describe('home approvals', () => {
  it('offers "Approve for this run" when the server says it is grantable', async () => {
    mockFetch([
      ...common,
      { match: /\/api\/runs(\?.*)?$/, body: [run('a', 'waiting_approval', '2026-01-01T10:00:00Z')] },
      { match: /\/api\/runs\/a\/approvals$/, body: [{ id: 'ap1', type: 'command', command: 'ls', reason: 'r', side_effect: 'READ_ONLY', risk: 'low', phase: null, expires_at: null, created_at: '2026-01-01T10:00:00Z', grantable: true }] },
    ])
    renderApp('#/')
    expect(await screen.findByRole('button', { name: /Approve for this run/ })).toBeTruthy()
  })
})

describe('metrics page', () => {
  const block = {
    runs: 4, finished: 3, by_status: {}, by_outcome: {}, task_success_rate: 2 / 3, validation_pass_rate: null, first_pass_success_rate: 0.5, resume_success_rate: null, runs_resumed: 0,
    failure_distribution: { MODEL_TIMEOUT: 1 }, approval_latency_s: { p50: null, p95: null, count: 0 }, time_to_completion_s: { p50: 120, p95: 400 },
    tokens: { total: 12000, prompt: 9000, completion: 3000, per_success: null }, cost: { total: null, per_success: null, currency: null }, human_interventions: 0, provider_failovers: 0,
  }
  it('shows real numbers, dashes for missing ones, and requests the chosen window and grouping', async () => {
    const calls = mockFetch([
      ...common,
      { match: /\/api\/runs(\?.*)?$/, body: [] },
      { match: /\/api\/metrics\/operations/, body: { context: { runs_with_context: null, mean_files_selected: null, mean_context_tokens: null, context_precision: null }, memory: {}, policy: {}, workers: { recoveries: null }, workflows: { actions: {}, inbound: {} } } },
      { match: /\/api\/metrics/, body: { window: { since: null, until: null }, totals: block, models: [{ provider: 'mock', model: 'm1', calls: 5, error_rate: 0, latency_ms: { p50: 800, p95: 2000 } }], groups: { m1: block } } },
    ])
    renderApp('#/metrics')
    await screen.findByText('Task success')
    expect(screen.getAllByText('67%').length).toBeGreaterThan(0)
    expect(screen.getByText('Model timeout')).toBeTruthy()
    expect(screen.getAllByText('—').length).toBeGreaterThan(2)
    expect(calls.find(c => c.url.startsWith('/api/metrics?'))?.url).toBe('/api/metrics?window=7d')
    fireEvent.change(screen.getByLabelText('Compare'), { target: { value: 'model' } })
    await waitFor(() => expect(calls.some(c => c.url === '/api/metrics?window=7d&group_by=model')).toBe(true))
    await screen.findByRole('heading', { name: 'Comparison' })
  })
  it('explains an empty window', async () => {
    mockFetch([
      ...common,
      { match: /\/api\/runs(\?.*)?$/, body: [] },
      { match: /\/api\/metrics\/operations/, body: { context: { runs_with_context: null, mean_files_selected: null, mean_context_tokens: null, context_precision: null }, memory: {}, policy: {}, workers: { recoveries: null }, workflows: { actions: {}, inbound: {} } } },
      { match: /\/api\/metrics/, body: { window: { since: null, until: null }, totals: { ...block, runs: 0, finished: 0 }, models: [] } },
    ])
    renderApp('#/metrics')
    expect(await screen.findByText('No runs in this window')).toBeTruthy()
  })
})
