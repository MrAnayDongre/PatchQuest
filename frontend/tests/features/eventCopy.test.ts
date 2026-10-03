import { describe, expect, it } from 'vitest'
import { describeEvent, friendlyStatusLine, outcomeLabel, sideEffectLabel, statusLabel, verdictLabel } from '../../src/lib/eventCopy'
import { ev } from './helpers'

describe('describeEvent', () => {
  it('writes readable one-liners for the main events', () => {
    expect(describeEvent(ev(1, 'tests_started', { phase: 'testing' })).title).toBe('Running tests…')
    expect(describeEvent(ev(1, 'approval_requested', { payload: { reason: 'runs code', command: 'pytest' } }))).toMatchObject({
      title: 'Waiting for your approval',
      detail: 'runs code',
      code: 'pytest',
      category: 'approval',
    })
    expect(describeEvent(ev(1, 'checkpoint_created', { phase: 'patching', payload: { seq: 4 } })).title).toBe('Checkpoint 4 saved after write patch')
    expect(describeEvent(ev(1, 'run_resume_requested', { attempt: 2 })).title).toBe('Resumed after interruption (attempt 2)')
  })

  it('classifies events into the filter categories', () => {
    const cat = (t: string) => describeEvent(ev(1, t)).category
    expect(cat('model_call')).toBe('model')
    expect(cat('command_executed')).toBe('command')
    expect(cat('patch_proposed')).toBe('patch')
    expect(cat('tests_completed')).toBe('validation')
    expect(cat('approval_decided')).toBe('approval')
    expect(cat('checkpoint_created')).toBe('system')
    expect(cat('plan_created')).toBe('agent')
  })

  it('says what the user decided', () => {
    expect(describeEvent(ev(1, 'approval_decided', { payload: { decision: 'DENY' } })).title).toBe('You denied it')
    expect(describeEvent(ev(1, 'approval_decided', { payload: { decision: 'APPROVE_FOR_RUN' } })).title).toBe('You approved it for this run')
  })

  it('marks failures and non-zero exits as problems', () => {
    expect(describeEvent(ev(1, 'command_executed', { payload: { returncode: 1, command: 'make' } })).tone).toBe('warning')
    expect(describeEvent(ev(1, 'command_executed', { payload: { returncode: 0, command: 'make' } })).tone).toBe('success')
    const failed = describeEvent(ev(1, 'run_failed', { payload: { failure: { message: 'The model timed out.' } } }))
    expect(failed).toMatchObject({ tone: 'danger', detail: 'The model timed out.' })
  })

  it('summarises a completed run', () => {
    expect(describeEvent(ev(1, 'run_completed', { payload: { outcome: 'applied', verdict: 'passed' } })).detail).toBe(
      'Patch applied to your repository · Tests passed',
    )
  })

  it('shows the model call cost without putting it in the collapse key', () => {
    const a = describeEvent(ev(1, 'model_call', { payload: { role: 'coder', duration_ms: 1500, provider: 'p', model: 'm' } }))
    const b = describeEvent(ev(2, 'model_call', { payload: { role: 'coder', duration_ms: 20, provider: 'p', model: 'm' } }))
    expect(a.detail).toBe('1.5 s · p/m')
    expect(a.collapseKey).toBe(b.collapseKey)
  })

  it('never hides unknown event types', () => {
    expect(describeEvent(ev(1, 'something_new_happened', { message: 'hi' }))).toMatchObject({ title: 'Something new happened', detail: 'hi' })
  })

  it('treats status transitions as meaningful sentences', () => {
    const t = (from: string, to: string) => describeEvent(ev(1, 'run_state_changed', { payload: { from, to } })).title
    expect(t('running', 'waiting_approval')).toBe('Waiting for your approval')
    expect(t('interrupted', 'running')).toBe('Resumed')
    expect(t('running', 'completed')).toBe('Marked completed')
  })
})

describe('label helpers', () => {
  it('labels statuses, outcomes, verdicts and side effects in plain words', () => {
    expect(statusLabel('waiting_approval')).toBe('Waiting for approval')
    expect(statusLabel('weird_state')).toBe('Weird state')
    expect(outcomeLabel('no_changes')).toBe('No changes needed')
    expect(verdictLabel('regression')).toBe('Tests regressed')
    expect(sideEffectLabel('REPOSITORY_WRITE')).toBe('Writes to your repository')
    expect(sideEffectLabel(undefined)).toMatch(/unknown/i)
  })

  it('describes a run in one status line', () => {
    expect(friendlyStatusLine({ status: 'failed', outcome: null, verdict: null, failure_kind: 'MODEL_TIMEOUT' })).toBe('Failed: model timeout')
    expect(friendlyStatusLine({ status: 'completed', outcome: 'applied', verdict: 'passed', failure_kind: null })).toBe(
      'Patch applied to your repository · Tests passed',
    )
    expect(friendlyStatusLine({ status: 'running', outcome: null, verdict: null, failure_kind: null })).toBe('Running')
  })
})
