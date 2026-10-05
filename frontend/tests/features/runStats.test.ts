import { describe, expect, it } from 'vitest'
import type { Run } from '../../src/api/types'
import { bucketRuns, computeMetrics, DEFAULT_FILTERS, filterRuns } from '../../src/lib/runStats'

function run(id: string, status: Run['status'], extra: Partial<Run> = {}): Run {
  return {
    id,
    repo_path: '/r/' + id,
    task: 'task ' + id,
    status,
    current_phase: null,
    provider: 'mock',
    model: null,
    runtime_mode: 'local',
    created_at: '2026-01-01T10:00:00Z',
    updated_at: '2026-01-01T10:05:00Z',
    completed_at: null,
    ...extra,
  }
}

const runs: Run[] = [
  run('a', 'completed', { completed_at: '2026-01-01T10:02:00Z', outcome: 'applied', verdict: 'passed' }),
  run('b', 'completed', { completed_at: '2026-01-01T10:04:00Z', outcome: 'read_only', updated_at: '2026-01-01T10:06:00Z' }),
  run('c', 'failed', { completed_at: '2026-01-01T10:10:00Z', failure_kind: 'MODEL_TIMEOUT' }),
  run('d', 'cancelled', { completed_at: '2026-01-01T10:01:00Z' }),
  run('e', 'running'),
  run('f', 'waiting_approval'),
  run('g', 'interrupted'),
]

describe('computeMetrics', () => {
  it('computes honest numbers from real runs only', () => {
    const m = computeMetrics(runs)
    expect(m.total).toBe(7)
    expect(m.finished).toBe(4)
    expect(m.successRate).toBeCloseTo(2 / 3)
    expect(m.outcomes).toEqual({ applied: 1, read_only: 1 })
    expect(m.verdicts).toEqual({ passed: 1 })
    // durations: 2 min, 4 min, 10 min, 1 min -> median 3 min
    expect(m.medianDurationMs).toBe(3 * 60_000)
    expect(m.recentDurations).toHaveLength(4)
  })

  it('reports no data as null, never as zero', () => {
    const m = computeMetrics([run('x', 'running')])
    expect(m.successRate).toBeNull()
    expect(m.medianDurationMs).toBeNull()
    expect(computeMetrics([]).total).toBe(0)
  })

  it('ignores runs with impossible timestamps', () => {
    const m = computeMetrics([run('x', 'completed', { completed_at: '2025-01-01T00:00:00Z' })])
    expect(m.medianDurationMs).toBeNull()
  })
})

describe('bucketRuns', () => {
  it('separates what needs the user from what is just running', () => {
    const b = bucketRuns(runs)
    expect(b.waitingApproval.map(r => r.id)).toEqual(['f'])
    expect(b.active.map(r => r.id)).toEqual(['e'])
    expect(b.interrupted.map(r => r.id)).toEqual(['g'])
    expect(b.failed.map(r => r.id)).toEqual(['c'])
    expect(b.completed.map(r => r.id)).toEqual(['b', 'a'])
  })
})

describe('filterRuns', () => {
  it('filters by status group', () => {
    expect(filterRuns(runs, { ...DEFAULT_FILTERS, status: 'needs-you' }).map(r => r.id).sort()).toEqual(['f', 'g'])
    expect(filterRuns(runs, { ...DEFAULT_FILTERS, status: 'active' }).map(r => r.id)).toEqual(['e'])
  })

  it('filters by outcome and search text', () => {
    expect(filterRuns(runs, { ...DEFAULT_FILTERS, outcome: 'applied' }).map(r => r.id)).toEqual(['a'])
    expect(filterRuns(runs, { ...DEFAULT_FILTERS, query: 'TASK c' }).map(r => r.id)).toEqual(['c'])
  })

  it('sorts needs-attention first when sorting by status', () => {
    const ids = filterRuns(runs, { ...DEFAULT_FILTERS, sort: 'status' }).map(r => r.id)
    expect(ids.slice(0, 3)).toEqual(['f', 'g', 'e'])
  })
})
