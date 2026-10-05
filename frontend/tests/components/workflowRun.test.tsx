import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import WorkflowRunPage from '../../src/features/workflows/WorkflowRunPage'
import { ToastProvider } from '../../src/design/overlay'
import { mockFetch, type MockRoute } from './testUtils'
import { docsExample } from '../features/wfHelpers'

const step = (node_id: string, status: string, extra: Record<string, unknown> = {}) => ({ node_id, visit: 1, status, output: null, error: null, wait_kind: null, wake_at: null, child_run_id: null, decision: null, decided_by: null, ...extra })
const run = (status: string, steps: unknown[], extra: Record<string, unknown> = {}) => ({
  id: 'wr1', workflow_id: 'wf1', workspace_id: 'ws', status, error: null, created_by: 'anay', created_at: '2026-01-01T10:00:00Z', completed_at: null,
  trigger: { type: 'manual' }, vars: {}, steps, events: [{ id: 1, type: 'approval_requested', node_id: 'review', actor: 'engine', message: 'Post the fix on the issue?', payload: null, created_at: '2026-01-01T10:00:00Z' }], ...extra,
})
const wf: MockRoute = { match: /\/api\/workflows\/wf1$/, body: { id: 'wf1', name: 'issue-to-proposal', version: 3, workspace_id: 'ws', definition: docsExample() } }
const renderPage = () => render(<ToastProvider><WorkflowRunPage id="wr1" /></ToastProvider>)

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('workflow run page', () => {
  const waiting = run('waiting', [step('fix', 'succeeded'), step('ok', 'succeeded'), step('review', 'waiting', { wait_kind: 'approval' })])

  it('shows the graph coloured by progress and the pending approval with its question', async () => {
    mockFetch([wf, { match: /\/api\/workflows\/runs\/wr1$/, body: waiting }])
    renderPage()
    expect((await screen.findAllByText('Post the fix on the issue?', { exact: false })).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('issue-to-proposal · version 3')
    const nodes = Object.fromEntries([...document.querySelectorAll('[data-node-id]')].map(n => [n.getAttribute('data-node-id'), n.className]))
    expect(nodes.fix).toContain('wf-node--done')
    expect(nodes.review).toContain('wf-node--waiting')
    expect(nodes.review).toContain('wf-node--attention')
    expect(nodes.post).not.toContain('wf-node--done')
    expect(screen.getByRole('button', { name: 'Approve' })).toBeTruthy()
  })

  it('approves and denies with the right request', async () => {
    const calls = mockFetch([wf, { match: /\/runs\/wr1$/, body: waiting }, { match: /\/steps\/review\/decision$/, method: 'POST', body: { status: 'ok' } }])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    expect(calls.find(c => c.method === 'POST')).toMatchObject({ url: '/api/workflows/runs/wr1/steps/review/decision', body: { decision: 'approve' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Deny' }))
    await waitFor(() => expect(calls.filter(c => c.method === 'POST')).toHaveLength(2))
    expect(calls.filter(c => c.method === 'POST')[1].body).toEqual({ decision: 'deny' })
  })

  it('explains a refused decision in plain words', async () => {
    mockFetch([wf, { match: /\/runs\/wr1$/, body: waiting }, { match: /\/decision$/, method: 'POST', status: 409, body: { detail: { code: 'not_pending', message: 'review is not waiting for a decision' } } }])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    expect(await screen.findByText('Already settled')).toBeTruthy()
  })

  it('shows the permission explanation when the role cannot decide', async () => {
    mockFetch([wf, { match: /\/runs\/wr1$/, body: waiting }, { match: /\/decision$/, method: 'POST', status: 403, body: { detail: { code: 'forbidden', message: 'approval.decide is not permitted' } } }])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))
    expect(await screen.findByText("Your role can't answer approvals")).toBeTruthy()
    expect(screen.getByText(/Service accounts can start workflows but never approve/)).toBeTruthy()
  })

  it('an uncertain action asks a person, explains the risk, and sends the chosen outcome', async () => {
    const r = run('waiting', [step('fix', 'succeeded'), step('post', 'uncertain')], { events: [] })
    const calls = mockFetch([wf, { match: /\/runs\/wr1$/, body: r }, { match: /\/steps\/post\/resolve$/, method: 'POST', body: { status: 'ok' } }])
    renderPage()
    await screen.findByText('Check "post" before continuing')
    expect(screen.getByText(/could do it twice/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'It already happened' }))
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    expect(calls.find(c => c.method === 'POST')).toMatchObject({ url: '/api/workflows/runs/wr1/steps/post/resolve', body: { outcome: 'happened' } })
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(calls.filter(c => c.method === 'POST')).toHaveLength(2))
    expect(calls.filter(c => c.method === 'POST')[1].body).toEqual({ outcome: 'retry' })
  })

  it('cancelling needs a confirmation', async () => {
    const calls = mockFetch([wf, { match: /\/runs\/wr1$/, body: waiting }, { match: /\/runs\/wr1\/cancel$/, method: 'POST', body: { status: 'cancelled' } }])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel run' }))
    expect(calls.some(c => c.method === 'POST')).toBe(false)
    const dialog = screen.getByRole('dialog')
    fireEvent.click(dialog.querySelector('.ui-btn--danger') as HTMLElement)
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/cancel'))).toBe(true))
  })

  it('polls every 2 seconds while live and stops once the run is finished', async () => {
    let n = 0
    const calls = mockFetch([wf, { match: /\/runs\/wr1$/, body: () => (++n < 3 ? waiting : run('completed', [step('fix', 'succeeded')], { completed_at: '2026-01-01T10:05:00Z', events: [] })) }])
    renderPage()
    await screen.findByText('Updating every 2 seconds')
    const runCalls = () => calls.filter(c => /\/runs\/wr1$/.test(c.url)).length
    expect(runCalls()).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    expect(runCalls()).toBe(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    await waitFor(() => expect(screen.queryByText('Updating every 2 seconds')).toBeNull())
    const settled = runCalls()
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(runCalls()).toBe(settled)
  })

  it('does not poll while the tab is hidden', async () => {
    const calls = mockFetch([wf, { match: /\/runs\/wr1$/, body: waiting }])
    renderPage()
    await screen.findByText('Updating every 2 seconds')
    const before = calls.length
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(calls.length).toBe(before)
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  })

  it('shows a failed run with its reason, and a forbidden read as a permission message', async () => {
    mockFetch([wf, { match: /\/runs\/wr1$/, body: run('failed', [step('post', 'failed', { error: 'no github connector is connected for github.comment' })], { error: 'post failed', events: [] }) }])
    renderPage()
    await screen.findByText('This run failed')
    expect(screen.getByText('no github connector is connected for github.comment')).toBeTruthy()
    cleanup()
    mockFetch([{ match: /\/runs\/wr1$/, status: 403, body: { detail: { code: 'forbidden', message: 'no' } } }])
    renderPage()
    expect(await screen.findByText("Your role can't see workflows here")).toBeTruthy()
  })
})
