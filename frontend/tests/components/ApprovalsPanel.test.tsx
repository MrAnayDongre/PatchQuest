import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApprovalCard } from '../../src/features/run/ApprovalsPanel'
import type { ApprovalInfo } from '../../src/lib/runState'
import { mockFetch } from './testUtils'

const base: ApprovalInfo = {
  id: 'ap1',
  type: 'command',
  command: 'pytest -x',
  reason: 'Runs project code',
  sideEffect: 'WORKSPACE_WRITE',
  risk: 'medium',
  phase: 'testing',
  requestedAt: new Date().toISOString(),
  expiresAt: new Date(Date.now() + 90_000).toISOString(),
  grantable: true,
  eventId: 1,
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ApprovalCard', () => {
  it('explains the request in plain words', () => {
    mockFetch([])
    render(<ApprovalCard runId="r1" approval={base} />)
    expect(screen.getByText('Writes files in the isolated workspace')).toBeTruthy()
    expect(screen.getByText('medium risk')).toBeTruthy()
    expect(screen.getByText('pytest -x')).toBeTruthy()
    expect(screen.getByRole('timer').textContent).toMatch(/Expires in/)
  })

  it('sends APPROVE_ONCE and confirms', async () => {
    const calls = mockFetch([{ match: /\/approvals\/ap1$/, method: 'POST', body: { status: 'approved', decision: 'APPROVE_ONCE' } }])
    const onDecided = vi.fn()
    render(<ApprovalCard runId="r1" approval={base} onDecided={onDecided} />)
    fireEvent.click(screen.getByRole('button', { name: /Approve once/ }))
    await screen.findByText('Decision sent')
    expect(calls[0]).toMatchObject({ method: 'POST', url: '/api/runs/r1/approvals/ap1', body: { decision: 'APPROVE_ONCE' } })
    expect(onDecided).toHaveBeenCalledWith('ap1', 'APPROVE_ONCE')
  })

  it('offers "Approve for this run" only when the request is grantable', () => {
    mockFetch([])
    const { rerender } = render(<ApprovalCard runId="r1" approval={base} />)
    expect(screen.queryByRole('button', { name: /Approve for this run/ })).toBeTruthy()
    rerender(<ApprovalCard runId="r1" approval={{ ...base, grantable: false, sideEffect: 'EXTERNAL_WRITE' }} />)
    expect(screen.queryByRole('button', { name: /Approve for this run/ })).toBeNull()
  })

  it('offers Modify only for commands, and sends the edited command', async () => {
    const calls = mockFetch([{ match: /\/approvals\/ap1$/, method: 'POST', body: { status: 'approved', decision: 'MODIFY' } }])
    render(<ApprovalCard runId="r1" approval={{ ...base, type: 'patch', command: null }} />)
    expect(screen.queryByRole('button', { name: /Modify/ })).toBeNull()
    cleanup()
    render(<ApprovalCard runId="r1" approval={base} />)
    fireEvent.click(screen.getByRole('button', { name: /Modify/ }))
    const box = screen.getByLabelText('Edit the command') as HTMLTextAreaElement
    expect(box.value).toBe('pytest -x')
    fireEvent.change(box, { target: { value: 'pytest -x tests/unit' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run edited command' }))
    await screen.findByText('Decision sent')
    expect(calls[0].body).toEqual({ decision: 'MODIFY', modified_command: 'pytest -x tests/unit' })
  })

  it('sends DENY and CANCEL_RUN', async () => {
    const calls = mockFetch([{ match: /\/approvals\/ap1$/, method: 'POST', body: { status: 'denied', decision: 'DENY' } }])
    render(<ApprovalCard runId="r1" approval={base} />)
    fireEvent.click(screen.getByRole('button', { name: /^Deny$/ }))
    await screen.findByText('Decision sent')
    expect((calls[0].body as { decision: string }).decision).toBe('DENY')
    cleanup()
    const calls2 = mockFetch([{ match: /\/approvals\/ap1$/, method: 'POST', body: { status: 'denied', decision: 'CANCEL_RUN' } }])
    render(<ApprovalCard runId="r1" approval={base} />)
    fireEvent.click(screen.getByRole('button', { name: /Deny and cancel run/ }))
    await screen.findByText('Decision sent')
    expect((calls2[0].body as { decision: string }).decision).toBe('CANCEL_RUN')
  })

  it.each([
    [409, 'approval_already_decided', 'Already answered'],
    [404, 'approval_not_found', 'Approval no longer exists'],
    [422, 'decision_not_allowed', "That choice isn't available here"],
  ])('explains a %i %s error and keeps the card usable', async (status, code, title) => {
    mockFetch([{ match: /\/approvals\/ap1$/, method: 'POST', status, body: { detail: { code, message: 'server says no' } } }])
    const onStale = vi.fn()
    render(<ApprovalCard runId="r1" approval={base} onStale={onStale} />)
    fireEvent.click(screen.getByRole('button', { name: /Approve once/ }))
    await waitFor(() => expect(screen.getByText(title)).toBeTruthy())
    expect(screen.queryByText('Decision sent')).toBeNull()
    expect(screen.getByRole('button', { name: /Approve once/ })).toBeTruthy()
    if (status !== 422) expect(onStale).toHaveBeenCalled()
    else expect(onStale).not.toHaveBeenCalled()
  })

  it('shows an expired request as denied', () => {
    mockFetch([])
    render(<ApprovalCard runId="r1" approval={{ ...base, expiresAt: new Date(Date.now() - 1000).toISOString() }} />)
    expect(screen.getByRole('timer').textContent).toMatch(/Expired/)
  })
})
