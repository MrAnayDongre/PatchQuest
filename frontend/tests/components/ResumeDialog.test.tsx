import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ResumeDialog } from '../../src/features/run/RecoveryDialogs'
import { mockFetch } from './testUtils'

const plan = (category: string, drift: string) => ({
  LAST_CHECKPOINT: '#4 after patching (2026-01-01)',
  INTERRUPTED_OPERATION: "phase 'testing'",
  REPO_DRIFT: drift,
  SIDE_EFFECT_CERTAINTY: 'NONE',
  RECOVERY_ACTION: "Continue from phase 'static_checks'",
  APPROVAL_REQUIRED: category !== 'SAFE_RESUME',
  CATEGORY: category,
  REASONS: ['A file the run modifies changed'],
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ResumeDialog', () => {
  it('shows the plan first and resumes with no extra confirmation when it is safe', async () => {
    const calls = mockFetch([
      { match: /resume-plan$/, body: plan('SAFE_RESUME', 'NO_DRIFT') },
      { match: /\/resume$/, method: 'POST', body: plan('SAFE_RESUME', 'NO_DRIFT') },
    ])
    const onResumed = vi.fn()
    render(<ResumeDialog runId="r1" open onClose={() => {}} onResumed={onResumed} />)
    await screen.findByText('Safe to continue from the last checkpoint')
    expect(screen.getByText("Your repository hasn't changed since the checkpoint.")).toBeTruthy()
    expect(calls.some(c => c.method === 'POST')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /Resume run/ }))
    await waitFor(() => expect(onResumed).toHaveBeenCalled())
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ accept_drift: false, rollback: false })
  })

  it('requires an explicit confirmation before resuming over drift', async () => {
    const calls = mockFetch([
      { match: /resume-plan$/, body: plan('HUMAN_CONFIRMATION_REQUIRED', 'CONFLICTING_DRIFT') },
      { match: /\/resume$/, method: 'POST', body: plan('SAFE_RESUME', 'CONFLICTING_DRIFT') },
    ])
    const onResumed = vi.fn()
    render(<ResumeDialog runId="r1" open onClose={() => {}} onResumed={onResumed} />)
    await screen.findByText('Needs your confirmation first')
    const resume = screen.getByRole('button', { name: /Resume run/ }) as HTMLButtonElement
    expect(resume.disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(resume.disabled).toBe(false)
    fireEvent.click(resume)
    await waitFor(() => expect(onResumed).toHaveBeenCalled())
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ accept_drift: true, rollback: false })
  })

  it('asks for a rollback confirmation when promotion was partial', async () => {
    const calls = mockFetch([
      { match: /resume-plan$/, body: plan('ROLLBACK_REQUIRED', 'NO_DRIFT') },
      { match: /\/resume$/, method: 'POST', body: plan('SAFE_RESUME', 'NO_DRIFT') },
    ])
    render(<ResumeDialog runId="r1" open onClose={() => {}} onResumed={() => {}} />)
    await screen.findByText('Partly written files must be restored first')
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: /Resume run/ }))
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ accept_drift: false, rollback: true })
  })

  it('does not offer resume for a run that cannot be recovered', async () => {
    mockFetch([{ match: /resume-plan$/, body: plan('NON_RECOVERABLE', 'NOT_CHECKED') }])
    render(<ResumeDialog runId="r1" open onClose={() => {}} onResumed={() => {}} />)
    await screen.findByText("This run can't be resumed")
    expect((screen.getByRole('button', { name: /Resume run/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the server message when resume is refused', async () => {
    mockFetch([
      { match: /resume-plan$/, body: plan('SAFE_RESUME', 'NO_DRIFT') },
      { match: /\/resume$/, method: 'POST', status: 409, body: { detail: { code: 'not_resumable', message: 'Run is still active', plan: plan('NON_RECOVERABLE', 'NO_DRIFT') } } },
    ])
    render(<ResumeDialog runId="r1" open onClose={() => {}} onResumed={() => {}} />)
    await screen.findByText('Safe to continue from the last checkpoint')
    fireEvent.click(screen.getByRole('button', { name: /Resume run/ }))
    await screen.findByText(/Run is still active/)
    expect(screen.getAllByText("This run can't be resumed").length).toBeGreaterThan(0)
  })
})
