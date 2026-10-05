import { describe, expect, it } from 'vitest'
import { applyEvents, initialRunState, reduceEvents } from '../../src/lib/runState'
import { ev, happyRun } from './helpers'

function shuffle<T>(arr: T[], seed = 7): T[] {
  const out = [...arr]
  let s = seed
  for (let i = out.length - 1; i > 0; i--) {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    const j = s % (i + 1)
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

describe('reduceEvents', () => {
  it('derives status, phases, verdict and outcome from a complete run', () => {
    const s = reduceEvents(happyRun())
    expect(s.status).toBe('completed')
    expect(s.ended).toBe(true)
    expect(s.outcome).toBe('applied')
    expect(s.verdict).toBe('passed')
    expect(s.phases.find(p => p.phase === 'intake')?.status).toBe('complete')
    expect(s.phases.find(p => p.phase === 'research')?.status).toBe('pending')
    expect(s.lastId).toBe(21)
    expect(s.statusTrail.map(t => t.to)).toEqual(['running', 'waiting_approval', 'running', 'completed'])
  })

  it('tracks a pending approval with its expiry and clears it when decided', () => {
    const upToRequest = happyRun().slice(0, 12)
    const waiting = reduceEvents(upToRequest)
    expect(waiting.status).toBe('waiting_approval')
    expect(waiting.pendingApprovals).toHaveLength(1)
    const a = waiting.pendingApprovals[0]
    expect(a).toMatchObject({ id: 'ap1', command: 'pytest', sideEffect: 'WORKSPACE_WRITE', grantable: true })
    expect(a.expiresAt).toBe('2026-01-01T10:13:00.000Z')

    const decided = applyEvents(waiting, happyRun().slice(12, 14))
    expect(decided.pendingApprovals).toHaveLength(0)
    expect(decided.decidedApprovals).toBe(1)
    expect(decided.status).toBe('running')
  })

  it('removes an approval on expiry', () => {
    const s = reduceEvents([
      ev(1, 'approval_requested', { payload: { approval_id: 'x', type: 'command', command: 'rm -rf build' } }),
      ev(2, 'approval_expired', { payload: { approval_id: 'x' } }),
    ])
    expect(s.pendingApprovals).toEqual([])
  })

  it('records checkpoints and the budget carried by the latest checkpoint', () => {
    const s = reduceEvents([
      ev(1, 'checkpoint_created', { phase: 'intake', payload: { seq: 1, event_cursor: 1, budget: [{ kind: 'model_calls', used: 1, limit: 5, exhausted: false }] } }),
      ev(2, 'checkpoint_created', { phase: 'planning', payload: { seq: 2, event_cursor: 2, budget: [{ kind: 'model_calls', used: 5, limit: 5, exhausted: true }] } }),
    ])
    expect(s.checkpoints.map(c => c.seq)).toEqual([1, 2])
    expect(s.budget).toEqual([{ kind: 'model_calls', used: 5, limit: 5, remaining: 0, exhausted: true }])
  })

  it('captures the typed failure payload for humans', () => {
    const s = reduceEvents([
      ev(1, 'phase_started', { phase: 'planning' }),
      ev(2, 'phase_failed', {
        phase: 'planning',
        message: 'boom',
        payload: { failure: { kind: 'MODEL_TIMEOUT', retryable: true, severity: 'error', origin: 'model', message: 'The model timed out.', recovery: ['Resume from the last checkpoint'], detail: 'x' } },
      }),
      ev(3, 'run_failed', { payload: { failure: { kind: 'MODEL_TIMEOUT', retryable: true, severity: 'error', origin: 'model', message: 'The model timed out.', recovery: ['Resume from the last checkpoint'], detail: 'x' } } }),
    ])
    expect(s.status).toBe('failed')
    expect(s.failure).toMatchObject({ kind: 'MODEL_TIMEOUT', retryable: true, message: 'The model timed out.', recovery: ['Resume from the last checkpoint'] })
    expect(s.phases.find(p => p.phase === 'planning')?.status).toBe('failed')
  })

  it('falls back to the event message when a failure has no typed payload', () => {
    const s = reduceEvents([ev(1, 'run_failed', { message: 'Run failed: disk full' })])
    expect(s.failure?.message).toBe('Run failed: disk full')
  })

  it('pairs command_started with command_executed', () => {
    const s = reduceEvents([
      ev(1, 'command_started', { payload: { command: 'ls' } }),
      ev(2, 'command_started', { payload: { command: 'pytest' } }),
      ev(3, 'command_executed', { payload: { command: 'ls', returncode: 0, duration_s: 0.1 } }),
    ])
    expect(s.commands.map(c => [c.command, c.status])).toEqual([
      ['ls', 'finished'],
      ['pytest', 'running'],
    ])
  })

  it('counts attempts and reopens an interrupted run on resume', () => {
    const s = reduceEvents([
      ev(1, 'run_state_changed', { payload: { from: 'created', to: 'running' } }),
      ev(2, 'run_interrupted', { message: 'process stopped' }),
      ev(3, 'run_resume_requested'),
      ev(4, 'run_state_changed', { payload: { from: 'interrupted', to: 'running' } }),
    ])
    expect(s.attempt).toBe(2)
    expect(s.status).toBe('running')
    expect(s.ended).toBe(false)
  })

  it('prefers the attempt number the server provides', () => {
    const s = reduceEvents([ev(1, 'run_resume_requested', { attempt: 3 })])
    expect(s.attempt).toBe(3)
  })

  it('records proposed and promoted files', () => {
    const s = reduceEvents(happyRun())
    expect(s.proposedFiles.map(f => f.path)).toEqual(['a.py'])
    expect(s.promotedFiles).toEqual(['a.py'])
  })

  it('keeps the reason a patch was not applied', () => {
    const s = reduceEvents([ev(1, 'patch_rejected', { message: "Patch not applied (validation verdict 'regression')" })])
    expect(s.rejectionReason).toContain('regression')
  })
})

describe('applyEvents equivalence', () => {
  const all = happyRun()
  const reference = reduceEvents(all)

  it('streaming one event at a time equals loading the ledger at once', () => {
    let s = initialRunState()
    for (const e of all) s = applyEvents(s, [e])
    expect(s).toEqual(reference)
  })

  it('duplicates are ignored', () => {
    let s = applyEvents(initialRunState(), all)
    s = applyEvents(s, all.slice(5, 12))
    s = applyEvents(s, [all[0], all[0]])
    expect(s).toEqual(reference)
  })

  it('returns the same object when nothing is new', () => {
    const s = applyEvents(initialRunState(), all)
    expect(applyEvents(s, all)).toBe(s)
    expect(applyEvents(s, [{ type: 'ping' }, null, 'x'])).toBe(s)
  })

  it('out-of-order delivery converges on the same state', () => {
    for (const seed of [1, 2, 3, 4, 5]) {
      let s = initialRunState()
      const shuffled = shuffle(all, seed)
      for (let i = 0; i < shuffled.length; i += 3) s = applyEvents(s, shuffled.slice(i, i + 3))
      expect(s).toEqual(reference)
    }
  })

  it('a reconnect that replays an overlapping tail changes nothing', () => {
    let s = applyEvents(initialRunState(), all.slice(0, 10))
    s = applyEvents(s, all.slice(6)) // server replays from an older cursor
    expect(s).toEqual(reference)
  })

  it('ignores heartbeats and frames without an id', () => {
    const s = applyEvents(initialRunState(), [{ type: 'ping', run_id: 'x' }, { type: 'run_created' }])
    expect(s.events).toHaveLength(0)
  })

  it('does not mutate the previous state', () => {
    const before = applyEvents(initialRunState(), all.slice(0, 5))
    const snapshot = JSON.stringify(before)
    applyEvents(before, all.slice(5))
    expect(JSON.stringify(before)).toBe(snapshot)
  })
})
