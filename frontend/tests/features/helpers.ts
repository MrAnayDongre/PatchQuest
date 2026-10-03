import type { RunEvent } from '../../src/api/types'

let counter = 0
export function ev(id: number, type: string, extra: Partial<RunEvent> = {}): RunEvent {
  counter++
  const minute = String(id % 60).padStart(2, '0')
  return {
    id,
    run_id: 'run-1',
    type,
    phase: null,
    status: null,
    message: null,
    payload: null,
    created_at: `2026-01-01T10:${minute}:00Z`,
    ...extra,
  }
}

/** A realistic run: starts, patches, asks for approval, is approved, tests, applies, completes. */
export function happyRun(): RunEvent[] {
  return [
    ev(1, 'run_created', { message: 'Run created: fix bug' }),
    ev(2, 'run_state_changed', { status: 'running', message: 'execution started', payload: { from: 'created', to: 'running' } }),
    ev(3, 'phase_started', { phase: 'intake', status: 'running' }),
    ev(4, 'phase_completed', { phase: 'intake', status: 'complete' }),
    ev(5, 'checkpoint_created', {
      phase: 'intake',
      payload: { seq: 1, event_cursor: 4, budget: [{ kind: 'model_calls', used: 1, limit: 10, exhausted: false }] },
    }),
    ev(6, 'phase_started', { phase: 'patching', status: 'running' }),
    ev(7, 'model_call', { phase: 'patching', payload: { role: 'coder', provider: 'mock', model: 'm', duration_ms: 1200 } }),
    ev(8, 'patch_proposed', { phase: 'patching', payload: { files: [{ path: 'a.py', additions: 2, deletions: 1 }] } }),
    ev(9, 'phase_completed', { phase: 'patching', status: 'complete' }),
    ev(10, 'phase_started', { phase: 'testing', status: 'running' }),
    ev(11, 'approval_requested', {
      phase: 'testing',
      message: 'needs approval',
      created_at: '2026-01-01T10:11:00Z',
      payload: { approval_id: 'ap1', type: 'command', command: 'pytest', reason: 'runs code', side_effect: 'WORKSPACE_WRITE', risk: 'medium', expires_in_s: 120, grantable: true },
    }),
    ev(12, 'run_state_changed', { status: 'waiting_approval', message: 'awaiting approval', payload: { from: 'running', to: 'waiting_approval' } }),
    ev(13, 'approval_decided', { phase: 'testing', payload: { approval_id: 'ap1', decision: 'APPROVE_ONCE' } }),
    ev(14, 'run_state_changed', { status: 'running', payload: { from: 'waiting_approval', to: 'running' } }),
    ev(15, 'command_started', { payload: { command: 'pytest', side_effect: 'WORKSPACE_WRITE' } }),
    ev(16, 'command_executed', { payload: { command: 'pytest', returncode: 0, duration_s: 3.2, side_effect: 'WORKSPACE_WRITE' } }),
    ev(17, 'tests_completed', { phase: 'testing', payload: { verdict: 'passed' } }),
    ev(18, 'phase_completed', { phase: 'testing', status: 'complete' }),
    ev(19, 'patch_applied', { phase: 'final_report', payload: { files: ['a.py'] } }),
    ev(20, 'run_state_changed', { status: 'completed', payload: { from: 'running', to: 'completed' } }),
    ev(21, 'run_completed', { payload: { outcome: 'applied', verdict: 'passed' } }),
  ]
}
