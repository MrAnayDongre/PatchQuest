import type { Run } from '../api/types'
import { median, parseTime } from './format'
import { isActiveStatus } from './runState'

export interface RunBuckets {
  active: Run[]
  waitingApproval: Run[]
  interrupted: Run[]
  completed: Run[]
  failed: Run[]
}

const byUpdatedDesc = (a: Run, b: Run) => (parseTime(b.updated_at) ?? 0) - (parseTime(a.updated_at) ?? 0)

/** Group runs by what the user should do about them. Each run lands in at most one bucket. */
export function bucketRuns(runs: Run[]): RunBuckets {
  const sorted = [...runs].sort(byUpdatedDesc)
  return {
    waitingApproval: sorted.filter(r => r.status === 'waiting_approval'),
    active: sorted.filter(r => isActiveStatus(r.status) && r.status !== 'waiting_approval'),
    interrupted: sorted.filter(r => r.status === 'interrupted'),
    completed: sorted.filter(r => r.status === 'completed'),
    failed: sorted.filter(r => r.status === 'failed'),
  }
}

export interface Metrics {
  total: number
  finished: number
  /** completed / (completed + failed); cancelled runs are the user's choice and are left out. */
  successRate: number | null
  medianDurationMs: number | null
  outcomes: Record<string, number>
  verdicts: Record<string, number>
  /** Durations of the most recent finished runs, oldest first (for a sparkline). */
  recentDurations: number[]
}

export function runDurationMs(run: Run): number | null {
  const start = parseTime(run.created_at)
  const end = parseTime(run.completed_at)
  if (start === null || end === null || end < start) return null
  return end - start
}

/** Honest metrics from the runs the server returned. Everything is null/zero when there is no data. */
export function computeMetrics(runs: Run[]): Metrics {
  const completed = runs.filter(r => r.status === 'completed')
  const failed = runs.filter(r => r.status === 'failed')
  const finishedRuns = runs.filter(r => r.status === 'completed' || r.status === 'failed' || r.status === 'cancelled')
  const outcomes: Record<string, number> = {}
  const verdicts: Record<string, number> = {}
  for (const r of completed) {
    if (r.outcome) outcomes[r.outcome] = (outcomes[r.outcome] ?? 0) + 1
    if (r.verdict) verdicts[r.verdict] = (verdicts[r.verdict] ?? 0) + 1
  }
  const decided = completed.length + failed.length
  const timed = finishedRuns
    .map(r => ({ run: r, ms: runDurationMs(r) }))
    .filter((x): x is { run: Run; ms: number } => x.ms !== null)
  const recent = [...timed].sort((a, b) => (parseTime(a.run.completed_at) ?? 0) - (parseTime(b.run.completed_at) ?? 0)).slice(-20)
  return {
    total: runs.length,
    finished: finishedRuns.length,
    successRate: decided ? completed.length / decided : null,
    medianDurationMs: median(timed.map(t => t.ms)),
    outcomes,
    verdicts,
    recentDurations: recent.map(t => t.ms),
  }
}

export type RunSort = 'updated' | 'created' | 'status'
export type StatusFilter = 'all' | 'active' | 'needs-you' | 'completed' | 'failed' | 'cancelled'

export interface RunFilters {
  status: StatusFilter
  outcome: string // 'all' or an outcome
  query: string
  sort: RunSort
}

export const DEFAULT_FILTERS: RunFilters = { status: 'all', outcome: 'all', query: '', sort: 'updated' }

const STATUS_RANK: Record<string, number> = {
  waiting_approval: 0,
  interrupted: 1,
  running: 2,
  cancel_requested: 3,
  created: 4,
  failed: 5,
  completed: 6,
  cancelled: 7,
}

export function filterRuns(runs: Run[], f: RunFilters): Run[] {
  const q = f.query.trim().toLowerCase()
  const out = runs.filter(r => {
    if (f.status === 'active' && !(isActiveStatus(r.status) && r.status !== 'waiting_approval')) return false
    if (f.status === 'needs-you' && r.status !== 'waiting_approval' && r.status !== 'interrupted') return false
    if (f.status === 'completed' && r.status !== 'completed') return false
    if (f.status === 'failed' && r.status !== 'failed') return false
    if (f.status === 'cancelled' && r.status !== 'cancelled') return false
    if (f.outcome !== 'all' && r.outcome !== f.outcome) return false
    if (q && !`${r.task} ${r.repo_path} ${r.id} ${r.model ?? ''} ${r.provider}`.toLowerCase().includes(q)) return false
    return true
  })
  const cmp: Record<RunSort, (a: Run, b: Run) => number> = {
    updated: byUpdatedDesc,
    created: (a, b) => (parseTime(b.created_at) ?? 0) - (parseTime(a.created_at) ?? 0),
    status: (a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9) || byUpdatedDesc(a, b),
  }
  return out.sort(cmp[f.sort])
}

/** j/k list navigation: returns the new selected index (clamped), or the same one for other keys. */
export function moveSelection(index: number, key: string, length: number): number {
  if (length <= 0) return -1
  if (key === 'j' || key === 'ArrowDown') return Math.min(length - 1, index + 1)
  if (key === 'k' || key === 'ArrowUp') return Math.max(0, index < 0 ? 0 : index - 1)
  return index
}
