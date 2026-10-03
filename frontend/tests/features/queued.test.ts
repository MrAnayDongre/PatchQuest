import { describe, expect, it } from 'vitest'
import type { Run } from '../../src/api/types'
import { describeEvent, friendlyStatusLine, statusLabel, statusTone } from '../../src/lib/eventCopy'
import { applyEvents, initialRunState, isActiveStatus, isTerminalStatus, reduceEvents } from '../../src/lib/runState'
import { bucketRuns, computeMetrics, DEFAULT_FILTERS, filterRuns } from '../../src/lib/runStats'
import { appliedExplanation } from '../../src/lib/changes'
import { count, millis, money, pair, rate, seconds } from '../../src/lib/metricsView'
import { ev } from './helpers'

const run = (id: string, status: Run['status']): Run => ({
  id, repo_path: '/r', task: id, status, current_phase: null, provider: 'mock', model: null, runtime_mode: 'local',
  created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:05:00Z', completed_at: null,
})

describe('queued status', () => {
  it('is active and not terminal', () => {
    expect(isActiveStatus('queued')).toBe(true)
    expect(isTerminalStatus('queued')).toBe(false)
  })
  it('reads as "Waiting for a worker"', () => {
    expect(statusLabel('queued')).toBe('Waiting for a worker')
    expect(friendlyStatusLine({ status: 'queued' })).toBe('Waiting for a worker')
    expect(statusTone('queued')).toBe('info')
    expect(describeEvent(ev(1, 'run_state_changed', { payload: { from: 'created', to: 'queued' }, message: 'queued for a worker' })).title).toBe('Waiting for a worker')
    expect(describeEvent(ev(2, 'run_state_changed', { payload: { from: 'queued', to: 'running' } })).title).toBe('A worker picked it up')
  })
  it('does not end the run in the reducer, including a queued resume', () => {
    const s = reduceEvents([
      ev(1, 'run_state_changed', { payload: { from: 'created', to: 'queued' } }),
    ])
    expect(s.status).toBe('queued')
    expect(s.ended).toBe(false)
    const resumed = reduceEvents([
      ev(1, 'run_state_changed', { payload: { from: 'created', to: 'running' } }),
      ev(2, 'run_interrupted', { message: 'worker died' }),
      ev(3, 'run_resume_requested'),
      ev(4, 'run_state_changed', { payload: { from: 'interrupted', to: 'queued' } }),
    ])
    expect(resumed.status).toBe('queued')
    expect(resumed.ended).toBe(false)
    expect(applyEvents(initialRunState(), []).status).toBeNull()
  })
  it('counts as working in buckets and filters, and ranks near running', () => {
    const runs = [run('q', 'queued'), run('r', 'running'), run('c', 'completed')]
    expect(bucketRuns(runs).active.map(r => r.id).sort()).toEqual(['q', 'r'])
    expect(filterRuns(runs, { ...DEFAULT_FILTERS, status: 'active' }).map(r => r.id).sort()).toEqual(['q', 'r'])
    expect(computeMetrics(runs).finished).toBe(1)
  })
  it('says nothing is applied yet while queued', () => {
    expect(appliedExplanation({ status: 'queued', outcome: null, verdict: null, rejectionReason: null }).title).toBe('Nothing applied yet')
  })
})

describe('metrics formatting', () => {
  it('renders missing values as a dash, never zero', () => {
    expect(rate(null)).toBe('—')
    expect(seconds(undefined)).toBe('—')
    expect(millis(null)).toBe('—')
    expect(count(null)).toBe('—')
    expect(money(null, null)).toBe('—')
    expect(pair({ p50: null, p95: null }, seconds)).toBe('—')
  })
  it('formats real values', () => {
    expect(rate(0.5)).toBe('50%')
    expect(rate(0)).toBe('0%')
    expect(seconds(90)).toBe('1 min 30 s')
    expect(pair({ p50: 2, p95: 30 }, seconds)).toBe('2.0 s typical, 30 s slowest 5%')
    expect(money(1.5, 'USD')).toContain('1.5')
  })
})
