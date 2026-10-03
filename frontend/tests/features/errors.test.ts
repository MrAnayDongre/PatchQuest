import { describe, expect, it } from 'vitest'
import { ApiError, friendlyError, normalizeError } from '../../src/api/errors'

describe('normalizeError', () => {
  it('reads structured details with a code', () => {
    const e = normalizeError(409, { detail: { code: 'approval_already_decided', message: 'already' } })
    expect(e).toMatchObject({ status: 409, code: 'approval_already_decided', message: 'already' })
  })
  it('reads plain string details', () => {
    expect(normalizeError(409, { detail: 'Run is not active' }).message).toBe('Run is not active')
  })
  it('flattens validation lists', () => {
    const e = normalizeError(422, { detail: [{ loc: ['body', 'repo_path'], msg: 'field required' }] })
    expect(e.code).toBe('validation')
    expect(e.message).toBe('repo_path: field required')
  })
  it('keeps the resume plan available on confirmation errors', () => {
    const e = normalizeError(409, { detail: { code: 'confirmation_required', message: 'drift', plan: { CATEGORY: 'HUMAN_CONFIRMATION_REQUIRED' } } })
    expect((e.data as { plan: { CATEGORY: string } }).plan.CATEGORY).toBe('HUMAN_CONFIRMATION_REQUIRED')
  })
  it('falls back to the status text for non-JSON bodies', () => {
    expect(normalizeError(502, null, 'Bad Gateway').message).toBe('Bad Gateway')
  })
})

describe('friendlyError', () => {
  it('explains what happened and what to do', () => {
    const f = friendlyError(new ApiError(409, 'x', 'approval_already_decided'))
    expect(f.title).toBe('Already answered')
    expect(f.hint).toBeTruthy()
  })
  it('handles the 401 and unreachable-server cases specially', () => {
    expect(friendlyError(new ApiError(401, 'Missing or invalid API token')).title).toBe('Access token needed')
    expect(friendlyError(new ApiError(0, 'Network error', 'network')).title).toBe("Can't reach the PatchQuest server")
  })
  it('turns "Run is not active" into plain language', () => {
    expect(friendlyError(new ApiError(409, 'Run is not active')).message).toMatch(/already finished/)
  })
  it('shows the server message for rejected requests', () => {
    expect(friendlyError(new ApiError(400, 'Unknown override: agent.foo')).message).toBe('Unknown override: agent.foo')
  })
  it('explains tenant-scoping 403s', () => {
    expect(friendlyError(new ApiError(403, 'x', 'not_tenant_scoped')).title).toMatch(/shared workspace/)
  })
})
