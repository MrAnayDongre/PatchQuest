import { describe, expect, it } from 'vitest'
import { deriveMissionState } from './phaseState'
import type { Run, RunEvent } from '../api/types'

function evt(partial: Partial<RunEvent> & Pick<RunEvent, 'type'>): RunEvent {
  return {
    id: partial.id ?? 1,
    run_id: 'run-1',
    phase: partial.phase ?? null,
    status: partial.status ?? null,
    message: partial.message ?? null,
    payload: partial.payload ?? null,
    created_at: partial.created_at ?? '2026-01-01T00:00:00Z',
    ...partial,
  }
}

const completedReadOnlyEvents: RunEvent[] = [
  evt({ id: 1, type: 'phase_started', phase: 'intake' }),
  evt({ id: 2, type: 'phase_completed', phase: 'intake' }),
  evt({ id: 3, type: 'phase_started', phase: 'repo_scan' }),
  evt({ id: 4, type: 'repo_scan_completed', phase: 'repo_scan' }),
  evt({ id: 5, type: 'phase_started', phase: 'planning' }),
  evt({ id: 6, type: 'phase_completed', phase: 'planning' }),
  evt({ id: 7, type: 'phase_started', phase: 'research' }),
  evt({ id: 8, type: 'phase_skipped', phase: 'research', status: 'skipped' }),
  evt({ id: 9, type: 'phase_started', phase: 'context_building' }),
  evt({ id: 10, type: 'phase_completed', phase: 'context_building' }),
  evt({ id: 11, type: 'phase_started', phase: 'analysis' }),
  evt({ id: 12, type: 'analysis_generated', phase: 'analysis' }),
  evt({ id: 13, type: 'phase_completed', phase: 'analysis' }),
  evt({ id: 14, type: 'phase_started', phase: 'patching' }),
  evt({ id: 15, type: 'phase_skipped', phase: 'patching', status: 'skipped' }),
  evt({ id: 16, type: 'phase_started', phase: 'static_checks' }),
  evt({ id: 17, type: 'phase_skipped', phase: 'static_checks', status: 'skipped' }),
  evt({ id: 18, type: 'phase_started', phase: 'testing' }),
  evt({ id: 19, type: 'phase_skipped', phase: 'testing', status: 'skipped' }),
  evt({ id: 20, type: 'phase_started', phase: 'review' }),
  evt({ id: 21, type: 'phase_skipped', phase: 'review', status: 'skipped' }),
  evt({ id: 22, type: 'phase_started', phase: 'security_scan' }),
  evt({ id: 23, type: 'security_scan_completed', phase: 'security_scan' }),
  evt({ id: 24, type: 'phase_completed', phase: 'security_scan' }),
  evt({ id: 25, type: 'phase_started', phase: 'final_report' }),
  evt({ id: 26, type: 'report_generated', phase: 'final_report' }),
  evt({ id: 27, type: 'phase_completed', phase: 'final_report' }),
  evt({ id: 28, type: 'run_completed' }),
]

describe('deriveMissionState', () => {
  it('maps completed read-only run to 100% progress and COMPLETED status', () => {
    const run: Run = {
      id: 'run-1',
      repo_path: '/tmp',
      task: 'Summarize',
      status: 'completed',
      current_phase: 'final_report',
      provider: 'nvidia',
      model: 'openai/gpt-oss-20b',
      runtime_mode: 'local',
      model_profile: null,
      memory_mode: 'repo',
      allow_network: false,
      dry_run: false,
      created_at: '',
      updated_at: '',
      completed_at: '',
    }
    const state = deriveMissionState(completedReadOnlyEvents, run)
    expect(state.status).toBe('complete')
    expect(state.progress).toBe(1)
    expect(state.phases.find(p => p.phase === 'research')?.status).toBe('skipped')
    expect(state.phases.find(p => p.phase === 'patching')?.status).toBe('skipped')
    expect(state.phases.every(p => p.status !== 'pending')).toBe(true)
  })

  it('counts skipped phases toward progress', () => {
    const events: RunEvent[] = [
      evt({ id: 1, type: 'phase_started', phase: 'intake' }),
      evt({ id: 2, type: 'phase_completed', phase: 'intake' }),
      evt({ id: 3, type: 'phase_started', phase: 'research' }),
      evt({ id: 4, type: 'phase_skipped', phase: 'research', status: 'skipped' }),
    ]
    const state = deriveMissionState(events, null)
    expect(state.phases.find(p => p.phase === 'research')?.status).toBe('skipped')
    expect(state.progress).toBeGreaterThan(0)
  })

  it('marks failed phase and failed mission status', () => {
    const events: RunEvent[] = [
      evt({ id: 1, type: 'phase_started', phase: 'intake' }),
      evt({ id: 2, type: 'phase_failed', phase: 'intake', status: 'failed' }),
      evt({ id: 3, type: 'run_failed' }),
    ]
    const state = deriveMissionState(events, { ...({} as Run), status: 'failed' })
    expect(state.status).toBe('failed')
    expect(state.phases.find(p => p.phase === 'intake')?.status).toBe('failed')
  })

  it('reconstructs timeline from persisted events after late load', () => {
    const run: Run = {
      id: 'run-1',
      repo_path: '/tmp',
      task: 'Inspect',
      status: 'completed',
      current_phase: 'final_report',
      provider: 'nvidia',
      model: null,
      runtime_mode: 'local',
      model_profile: null,
      memory_mode: 'repo',
      allow_network: false,
      dry_run: false,
      created_at: '',
      updated_at: '',
      completed_at: '',
    }
    const state = deriveMissionState(completedReadOnlyEvents, run)
    expect(state.phases.find(p => p.phase === 'analysis')?.status).toBe('complete')
    expect(state.status).toBe('complete')
  })
})
