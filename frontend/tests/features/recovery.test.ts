import { describe, expect, it } from 'vitest'
import { appliedExplanation } from '../../src/lib/changes'
import { parseOverrides, resumeBody, resumeRequirements } from '../../src/lib/recoveryCopy'
import { buildCreateRequest, validateNewRun, type NewRunForm } from '../../src/lib/newRun'
import { buildCommands } from '../../src/app/commands'

describe('resume requirements', () => {
  it('maps the plan category to what the user must confirm', () => {
    expect(resumeRequirements({ CATEGORY: 'SAFE_RESUME' })).toEqual({ canResume: true, confirm: null })
    expect(resumeRequirements({ CATEGORY: 'SAFE_RETRY' })).toEqual({ canResume: true, confirm: null })
    expect(resumeRequirements({ CATEGORY: 'HUMAN_CONFIRMATION_REQUIRED' })).toEqual({ canResume: true, confirm: 'drift' })
    expect(resumeRequirements({ CATEGORY: 'ROLLBACK_REQUIRED' })).toEqual({ canResume: true, confirm: 'rollback' })
    expect(resumeRequirements({ CATEGORY: 'NON_RECOVERABLE' }).canResume).toBe(false)
  })
  it('only sends a flag the user explicitly confirmed', () => {
    expect(resumeBody('drift', false)).toEqual({ accept_drift: false, rollback: false })
    expect(resumeBody('drift', true)).toEqual({ accept_drift: true, rollback: false })
    expect(resumeBody('rollback', true)).toEqual({ accept_drift: false, rollback: true })
    expect(resumeBody(null, true)).toEqual({ accept_drift: false, rollback: false })
  })
})

describe('parseOverrides', () => {
  it('parses numbers, booleans and strings for agent settings', () => {
    expect(parseOverrides('agent.max_model_calls=80\nagent.record_model_io = false\nagent.name=fast').overrides).toEqual({
      'agent.max_model_calls': 80,
      'agent.record_model_io': false,
      'agent.name': 'fast',
    })
  })
  it('refuses safety settings and malformed lines', () => {
    const r = parseOverrides('safety.approval_timeout_seconds=9999\nnonsense\nagent.x=')
    expect(r.overrides).toEqual({})
    expect(r.errors).toHaveLength(3)
    expect(r.errors[0]).toMatch(/only agent/)
  })
  it('ignores blank lines', () => {
    expect(parseOverrides('\n\n')).toEqual({ overrides: {}, errors: [] })
  })
})

describe('appliedExplanation', () => {
  it('explains why nothing was applied', () => {
    expect(appliedExplanation({ status: 'completed', outcome: 'rejected', verdict: 'regression', rejectionReason: "Patch not applied (validation verdict 'regression')" }).body).toContain('regression')
    expect(appliedExplanation({ status: 'completed', outcome: 'applied', verdict: 'passed', rejectionReason: null }).tone).toBe('success')
    expect(appliedExplanation({ status: 'running', outcome: null, verdict: null, rejectionReason: null }).title).toBe('Nothing applied yet')
    expect(appliedExplanation({ status: 'failed', outcome: null, verdict: null, rejectionReason: null }).body).toMatch(/stopped before/)
    expect(appliedExplanation({ status: 'completed', outcome: 'no_patch', verdict: null, rejectionReason: null }).tone).toBe('warning')
  })
})

describe('new run form', () => {
  const form: NewRunForm = { repoPath: '/home/me/app', task: 'Fix the login test', provider: 'openai', model: ' gpt-x ', runtime: 'docker', dryRun: true, baseUrl: '', workspaceId: '', overrides: '' }
  it('accepts a valid form', () => {
    expect(validateNewRun(form)).toEqual({})
  })
  it('explains each problem', () => {
    const e = validateNewRun({ ...form, repoPath: 'relative/path', task: 'hi', baseUrl: 'localhost' })
    expect(Object.keys(e).sort()).toEqual(['baseUrl', 'repoPath', 'task'])
    expect(validateNewRun({ ...form, repoPath: '' }).repoPath).toMatch(/Enter/)
  })
  it('validates and sends run settings overrides (agent.* only)', () => {
    expect(validateNewRun({ ...form, overrides: 'safety.x=1' }).overrides).toMatch(/only agent/)
    expect(validateNewRun({ ...form, overrides: 'agent.max_model_calls=80' })).toEqual({})
    expect(buildCreateRequest({ ...form, overrides: 'agent.max_model_calls=80' }).overrides).toEqual({ 'agent.max_model_calls': 80 })
    expect(buildCreateRequest(form).overrides).toBeUndefined()
  })
  it('builds the API request, dropping the model for the mock provider', () => {
    expect(buildCreateRequest(form)).toEqual({ repo_path: '/home/me/app', task: 'Fix the login test', provider: 'openai', runtime_mode: 'docker', dry_run: true, model: 'gpt-x' })
    expect(buildCreateRequest({ ...form, provider: 'mock', baseUrl: 'http://x', workspaceId: 'ws1' })).toEqual({
      repo_path: '/home/me/app', task: 'Fix the login test', provider: 'mock', runtime_mode: 'docker', dry_run: true, base_url: 'http://x', workspace_id: 'ws1',
    })
  })
})

describe('buildCommands', () => {
  const noop = () => {}
  const run = { id: 'abc', repo_path: '/r/app', task: 'Fix flaky test', status: 'completed' as const, current_phase: null, provider: 'mock', model: null, runtime_mode: 'local', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T01:00:00Z', completed_at: null, outcome: 'applied' as const, verdict: 'passed' as const }
  it('includes actions, navigation, current-run commands and recent runs', () => {
    const cmds = buildCommands({ runs: [run], openNewRun: noop, toggleTheme: noop, openHelp: noop, currentRunCommands: [{ id: 'run:cancel', title: 'Cancel this run', group: 'This run', run: noop }], now: Date.parse('2026-01-01T02:00:00Z') })
    const ids = cmds.map(c => c.id)
    expect(ids).toContain('new-run')
    expect(ids).toContain('run:cancel')
    expect(ids).toContain('toggle-theme')
    expect(ids).toContain('go:#/runs')
    const open = cmds.find(c => c.id === 'run:abc')
    expect(open?.group).toBe('Open run')
    expect(open?.hint).toContain('Patch applied to your repository · Tests passed')
    expect(open?.hint).toContain('1 h ago')
  })
})
