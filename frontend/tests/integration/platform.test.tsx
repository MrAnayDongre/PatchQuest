import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from '../../src/App'
import { ThemeProvider } from '../../src/theme/ThemeProvider'
import { FakeEventSource, mockFetch, type MockRoute } from '../components/testUtils'

const common: MockRoute[] = [
  { match: /\/api\/health$/, body: { status: 'ok', version: '1' } },
  { match: /\/api\/runs(\?.*)?$/, body: [] },
  { match: /\/api\/workflows$/, body: [] },
  { match: /\/api\/me$/, body: { workspaces: [{ id: 'w1', name: 'Team', role: 'owner', permissions: ['connector.manage', 'run.create', 'repository.manage'] }] } },
]
const kinds = { stored_secrets_available: false, kinds: [{ kind: 'github', title: 'GitHub', inbound: true, triggers: [], actions: [{ name: 'comment', side_effect: 'EXTERNAL_WRITE', requires_approval: true }], config: [{ name: 'repo', type: 'string', required: true, description: 'owner/name' }], secrets: [{ name: 'token', required: true }], notes: 'Point the webhook at /hooks/<id>.', test: 'reads the repo' }] }
const integ = { id: 'i1', kind: 'github', name: 'GitHub (simulator)', config: { repo: 'a/b' }, status: 'connected', secrets: { token: { env: 'GH_TOKEN' } }, created_at: '', updated_at: '', last_checked_at: null, last_error: null, webhook_path: '/hooks/i1' }
const renderApp = (hash: string) => { window.location.hash = hash; return render(<ThemeProvider><App /></ThemeProvider>) }

beforeEach(() => { FakeEventSource.reset(); vi.stubGlobal('EventSource', FakeEventSource) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Integrations page', () => {
  it('lists integrations with Simulated badge, secret reference and full webhook URL', async () => {
    mockFetch([...common, { match: /\/integrations\/kinds$/, body: kinds }, { match: /\/integrations\?workspace_id=w1$/, body: [integ] }])
    renderApp('#/integrations')
    await screen.findByText('GitHub (simulator)')
    expect(screen.getByText('Simulated')).toBeTruthy()
    expect(screen.getByText('{env: GH_TOKEN}')).toBeTruthy()
    expect(screen.getByText(`${window.location.origin}/hooks/i1`)).toBeTruthy()
  })

  it('connects with an env secret, disables stored secrets, and shows server 422 messages', async () => {
    const calls = mockFetch([...common, { match: /\/integrations\/kinds$/, body: kinds }, { match: /\/integrations\?workspace_id=w1$/, body: [] },
      { match: /\/api\/integrations$/, method: 'POST', status: 422, body: { detail: { code: 'refused', message: 'repo must look like owner/name' } } }])
    renderApp('#/integrations')
    fireEvent.click((await screen.findAllByRole('button', { name: /Connect/ }))[0])
    await screen.findByText('Point the webhook at /hooks/<id>.')
    expect((screen.getByRole('option', { name: /Store encrypted/ }) as HTMLOptionElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Mine' } })
    fireEvent.change(screen.getByLabelText('Repo'), { target: { value: 'bad' } })
    fireEvent.change(screen.getByLabelText(/Token: variable name/), { target: { value: 'GH_TOKEN' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Connect' }).pop()!)
    await screen.findByText('repo must look like owner/name')
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ workspace_id: 'w1', kind: 'github', name: 'Mine', config: { repo: 'bad' }, secrets: { token: { env: 'GH_TOKEN' } } })
  })

  it('removes only after confirmation', async () => {
    const calls = mockFetch([...common, { match: /\/integrations\/kinds$/, body: kinds }, { match: /\/integrations\?workspace_id=w1$/, body: [integ] }, { match: /\/integrations\/i1\?workspace_id=w1$/, method: 'DELETE', body: { deleted: true } }])
    renderApp('#/integrations')
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))
    expect(calls.some(c => c.method === 'DELETE')).toBe(false)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Remove' })).pop()!)
    await waitFor(() => expect(calls.some(c => c.method === 'DELETE')).toBe(true))
  })
})

describe('Repositories page', () => {
  it('explains the local case and shows the profile with plain source labels', async () => {
    mockFetch([...common, { match: /\/repositories\?workspace_id=w1$/, body: [] },
      { match: /\/repositories\/profile\?/, body: { path: '/r/app', profile: { test_commands: { value: ['pytest -q'], source: 'user_explicit', reason: '', confidence: 1, last_verified: null, memory_id: 'm', evidence: {} } } } }])
    renderApp('#/repositories?path=%2Fr%2Fapp')
    await screen.findByText(/needs no registration/)
    await screen.findAllByText('You set this')
    expect(screen.getByText('pytest -q')).toBeTruthy()
  })
})

describe('Why tab', () => {
  it('appears with plain-language reasons when the run has explanations', async () => {
    const run = { id: 'r1', repo_path: '/r/app', task: 'Fix it', status: 'completed', current_phase: null, provider: 'mock', model: null, runtime_mode: 'local', created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:00:00Z', completed_at: null }
    mockFetch([...common, { match: /\/api\/runs\/r1$/, body: run }, { match: /\/api\/runs\/r1\/events/, body: [] }, { match: /\/checkpoints$/, body: [] }, { match: /\/lineage$/, body: { ancestry: [], children: [] } },
      { match: /\/explanations$/, body: [{ id: 1, type: 'memory_selected', phase: 'repo_scan', message: '', created_at: '', payload: { considered: 5, selected: 2, tokens: 40, items: [] } }] }])
    renderApp('#/runs/r1?tab=why')
    await screen.findByText('Remembered 2 of 5 facts (about 40 tokens)')
  })
})

describe('Budget panel', () => {
  it('labels wall time without a stray unit', async () => {
    const { BudgetPanel } = await import('../../src/features/run/BudgetPanel')
    render(<BudgetPanel budget={[{ kind: 'wall_time_s', used: 1, limit: 10, remaining: 9, exhausted: false }]} />)
    expect(screen.getByText('Wall time')).toBeTruthy()
  })
})
