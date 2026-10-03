import type { BudgetEntry, RunEvent, RunStatus } from '../api/types'
import { describeEvent, type EventCategory, type Tone } from './eventCopy'
import { parseTime } from './format'
import { MISSION_PHASES } from './phases'

/**
 * Pure reducer behind the run view. The same state comes out whether events were loaded from the ledger in one
 * go (page refresh) or arrived one at a time over the stream (duplicates, gaps and reordering included).
 */

export type PhaseStatus = 'pending' | 'running' | 'complete' | 'skipped' | 'blocked' | 'failed'

export interface PhaseInfo {
  phase: string
  status: PhaseStatus
  startedAt: string | null
  endedAt: string | null
  note: string | null
}

export interface TimelineItem {
  id: number
  type: string
  category: EventCategory
  tone: Tone
  title: string
  detail?: string
  code?: string
  collapseKey: string
  at: string
  phase: string | null
  event: RunEvent
}

export interface ApprovalInfo {
  id: string
  type: string
  command: string | null
  reason: string | null
  sideEffect: string
  risk: string
  phase: string | null
  requestedAt: string
  /** ISO time after which the request counts as denied, if the server said how long it waits. */
  expiresAt: string | null
  grantable: boolean
  eventId: number
}

export interface CheckpointInfo {
  seq: number
  phase: string | null
  eventCursor: number | null
  at: string
  eventId: number
}

export interface FailureInfo {
  kind: string
  retryable: boolean
  severity: string
  origin: string
  message: string
  recovery: string[]
  detail: string
  phase: string | null
  eventId: number
}

export interface CommandRecord {
  eventId: number
  command: string
  returncode: number | null
  durationS: number | null
  sideEffect: string | null
  timedOut: boolean
  status: 'running' | 'finished' | 'denied' | 'blocked'
  at: string
}

export interface PatchFile {
  path: string
  [key: string]: unknown
}

export interface StatusChange {
  eventId: number
  from: string | null
  to: string
  reason: string | null
  at: string
}

export interface RunState {
  /** Every event applied so far, ascending by id. */
  events: RunEvent[]
  timeline: TimelineItem[]
  lastId: number
  status: RunStatus | null
  statusReason: string | null
  statusTrail: StatusChange[]
  attempt: number
  phases: PhaseInfo[]
  currentPhase: string | null
  pendingApprovals: ApprovalInfo[]
  decidedApprovals: number
  checkpoints: CheckpointInfo[]
  budget: BudgetEntry[]
  failure: FailureInfo | null
  commands: CommandRecord[]
  proposedFiles: PatchFile[]
  promotedFiles: string[]
  /** Set once the ledger holds a terminal event (completed, failed or interrupted). */
  ended: boolean
  verdict: string | null
  outcome: string | null
  /** Reason the patch was not applied, from the last patch_rejected event. */
  rejectionReason: string | null
  reportReady: boolean
  /** replay/fork children announced in this run's own ledger */
  lineageNotes: { eventId: number; type: string; message: string | null }[]
}

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled'])
export const isTerminalStatus = (s: string | null | undefined): boolean => !!s && TERMINAL_STATUSES.has(s)
export const isActiveStatus = (s: string | null | undefined): boolean =>
  s === 'created' || s === 'queued' || s === 'running' || s === 'waiting_approval' || s === 'cancel_requested'

export function initialRunState(): RunState {
  return {
    events: [],
    timeline: [],
    lastId: 0,
    status: null,
    statusReason: null,
    statusTrail: [],
    attempt: 1,
    phases: MISSION_PHASES.map(phase => ({ phase, status: 'pending', startedAt: null, endedAt: null, note: null })),
    currentPhase: null,
    pendingApprovals: [],
    decidedApprovals: 0,
    checkpoints: [],
    budget: [],
    failure: null,
    commands: [],
    proposedFiles: [],
    promotedFiles: [],
    ended: false,
    verdict: null,
    outcome: null,
    rejectionReason: null,
    reportReady: false,
    lineageNotes: [],
  }
}

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}
const asStr = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
const asNum = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function parseFailure(payload: unknown, phase: string | null, eventId: number): FailureInfo | null {
  const f = asObj(asObj(payload).failure)
  if (!Object.keys(f).length) return null
  return {
    kind: asStr(f.kind) ?? 'UNKNOWN',
    retryable: f.retryable === true,
    severity: asStr(f.severity) ?? 'error',
    origin: asStr(f.origin) ?? 'runtime',
    message: asStr(f.message) ?? 'The run stopped unexpectedly.',
    recovery: Array.isArray(f.recovery) ? f.recovery.filter((r): r is string => typeof r === 'string') : [],
    detail: asStr(f.detail) ?? '',
    phase,
    eventId,
  }
}

function parseBudget(raw: unknown): BudgetEntry[] | null {
  if (!Array.isArray(raw)) return null
  const out: BudgetEntry[] = []
  for (const item of raw) {
    const o = asObj(item)
    const kind = asStr(o.kind)
    const used = asNum(o.used)
    const limit = asNum(o.limit)
    if (!kind || used === null || limit === null) continue
    const exhausted = typeof o.exhausted === 'boolean' ? o.exhausted : limit > 0 && used >= limit
    out.push({ kind, used, limit, remaining: asNum(o.remaining) ?? (limit > 0 ? Math.max(0, limit - used) : null), exhausted })
  }
  return out
}

function setPhase(phases: PhaseInfo[], name: string | null, patch: Partial<PhaseInfo>): PhaseInfo[] {
  if (!name) return phases
  const idx = phases.findIndex(p => p.phase === name)
  if (idx === -1) {
    return [...phases, { phase: name, status: 'pending', startedAt: null, endedAt: null, note: null, ...patch }]
  }
  const next = phases.slice()
  next[idx] = { ...next[idx], ...patch }
  return next
}

function toTimelineItem(e: RunEvent): TimelineItem {
  const c = describeEvent(e)
  return {
    id: e.id,
    type: e.type,
    category: c.category,
    tone: c.tone,
    title: c.title,
    detail: c.detail,
    code: c.code,
    collapseKey: c.collapseKey,
    at: e.created_at,
    phase: e.phase,
    event: e,
  }
}

/** Apply one event to a working copy. Small collections are replaced, never mutated. */
function applyOne(s: RunState, e: RunEvent): void {
  const p = asObj(e.payload)
  s.lastId = Math.max(s.lastId, e.id)
  if (typeof e.attempt === 'number') s.attempt = Math.max(s.attempt, e.attempt)

  switch (e.type) {
    case 'run_resume_requested':
      if (typeof e.attempt !== 'number') s.attempt += 1
      s.ended = false
      break

    case 'run_state_changed': {
      const to = asStr(p.to) ?? e.status
      if (!to) break
      s.status = to as RunStatus
      s.statusReason = e.message
      s.statusTrail = [...s.statusTrail, { eventId: e.id, from: asStr(p.from), to, reason: e.message, at: e.created_at }]
      if (to !== 'waiting_approval' && to !== 'running' && to !== 'created' && to !== 'queued') s.pendingApprovals = []
      else s.ended = false
      break
    }

    case 'run_completed':
      s.status = 'completed'
      s.ended = true
      s.pendingApprovals = []
      s.verdict = asStr(p.verdict) ?? s.verdict
      s.outcome = asStr(p.outcome) ?? s.outcome
      break
    case 'run_failed':
      s.status = 'failed'
      s.ended = true
      s.pendingApprovals = []
      s.failure = parseFailure(e.payload, s.currentPhase, e.id) ?? s.failure ?? {
        kind: 'UNKNOWN',
        retryable: false,
        severity: 'error',
        origin: 'runtime',
        message: e.message ?? 'The run stopped unexpectedly.',
        recovery: [],
        detail: '',
        phase: s.currentPhase,
        eventId: e.id,
      }
      break
    case 'run_interrupted':
      s.status = 'interrupted'
      s.ended = true
      s.pendingApprovals = []
      s.statusReason = e.message ?? s.statusReason
      break

    case 'phase_started':
      if (e.phase) s.currentPhase = e.phase
      s.phases = setPhase(s.phases, e.phase, { status: 'running', startedAt: e.created_at, endedAt: null, note: null })
      break
    case 'phase_completed':
      s.phases = setPhase(s.phases, e.phase, { status: 'complete', endedAt: e.created_at })
      break
    case 'phase_skipped':
      s.phases = setPhase(s.phases, e.phase, { status: 'skipped', endedAt: e.created_at, note: e.message })
      break
    case 'phase_blocked':
      s.phases = setPhase(s.phases, e.phase, { status: 'blocked', endedAt: e.created_at, note: e.message })
      break
    case 'phase_failed':
      s.phases = setPhase(s.phases, e.phase, { status: 'failed', endedAt: e.created_at, note: e.message })
      s.failure = parseFailure(e.payload, e.phase, e.id) ?? s.failure
      break

    case 'approval_requested': {
      const id = asStr(p.approval_id)
      if (!id) break
      const wait = asNum(p.expires_in_s)
      const t = parseTime(e.created_at)
      const info: ApprovalInfo = {
        id,
        type: asStr(p.type) ?? 'command',
        command: asStr(p.command),
        reason: asStr(p.reason) ?? e.message,
        sideEffect: asStr(p.side_effect) ?? 'UNKNOWN',
        risk: asStr(p.risk) ?? 'unknown',
        phase: e.phase,
        requestedAt: e.created_at,
        expiresAt: wait !== null && t !== null ? new Date(t + wait * 1000).toISOString() : null,
        grantable: p.grantable === true,
        eventId: e.id,
      }
      s.pendingApprovals = [...s.pendingApprovals.filter(a => a.id !== id), info]
      break
    }
    case 'approval_decided':
      s.decidedApprovals += 1
      s.pendingApprovals = s.pendingApprovals.filter(a => a.id !== asStr(p.approval_id))
      break
    case 'approval_expired':
      s.pendingApprovals = s.pendingApprovals.filter(a => a.id !== asStr(p.approval_id))
      break

    case 'checkpoint_created': {
      const seq = asNum(p.seq)
      if (seq !== null) {
        s.checkpoints = [
          ...s.checkpoints.filter(c => c.seq !== seq),
          { seq, phase: e.phase, eventCursor: asNum(p.event_cursor), at: e.created_at, eventId: e.id },
        ]
      }
      const b = parseBudget(p.budget)
      if (b) s.budget = b
      break
    }

    case 'command_started':
      s.commands = [
        ...s.commands,
        {
          eventId: e.id,
          command: asStr(p.command) ?? e.message ?? '',
          returncode: null,
          durationS: null,
          sideEffect: asStr(p.side_effect),
          timedOut: false,
          status: 'running',
          at: e.created_at,
        },
      ]
      break
    case 'command_executed': {
      const cmd = asStr(p.command) ?? e.message ?? ''
      const idx = findLastIndex(s.commands, c => c.status === 'running' && c.command === cmd)
      const done: CommandRecord = {
        eventId: idx >= 0 ? s.commands[idx].eventId : e.id,
        command: cmd,
        returncode: asNum(p.returncode),
        durationS: asNum(p.duration_s),
        sideEffect: asStr(p.side_effect),
        timedOut: p.timed_out === true,
        status: 'finished',
        at: idx >= 0 ? s.commands[idx].at : e.created_at,
      }
      s.commands = idx >= 0 ? s.commands.map((c, i) => (i === idx ? done : c)) : [...s.commands, done]
      break
    }
    case 'command_denied':
    case 'command_blocked':
      s.commands = [
        ...s.commands,
        {
          eventId: e.id,
          command: asStr(p.command) ?? '',
          returncode: null,
          durationS: null,
          sideEffect: asStr(p.side_effect),
          timedOut: false,
          status: e.type === 'command_denied' ? 'denied' : 'blocked',
          at: e.created_at,
        },
      ]
      break

    case 'patch_proposed': {
      const files = Array.isArray(p.files) ? p.files : []
      s.proposedFiles = files
        .map(f => (typeof f === 'string' ? { path: f } : asObj(f)))
        .filter((f): f is PatchFile => typeof f.path === 'string')
      break
    }
    case 'patch_rejected':
      s.rejectionReason = e.message
      break
    case 'promotion_completed':
    case 'patch_applied': {
      const files = Array.isArray(p.files) ? p.files : []
      const paths = files.map(f => (typeof f === 'string' ? f : asStr(asObj(f).path))).filter((f): f is string => !!f)
      if (paths.length) s.promotedFiles = Array.from(new Set([...s.promotedFiles, ...paths]))
      break
    }
    case 'tests_completed':
      s.verdict = asStr(p.verdict) ?? s.verdict
      break
    case 'report_generated':
      s.reportReady = true
      break
    case 'fork_created':
    case 'replay_created':
    case 'replay_completed':
    case 'replay_diverged':
      s.lineageNotes = [...s.lineageNotes, { eventId: e.id, type: e.type, message: e.message }]
      break
    default:
      break
  }
}

function findLastIndex<T>(arr: T[], pred: (t: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i--) if (pred(arr[i])) return i
  return -1
}

function bsearch(events: RunEvent[], id: number): number {
  let lo = 0
  let hi = events.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const v = events[mid].id
    if (v === id) return mid
    if (v < id) lo = mid + 1
    else hi = mid - 1
  }
  return -1
}

export function isRunEvent(e: unknown): e is RunEvent {
  if (!e || typeof e !== 'object') return false
  const o = e as Record<string, unknown>
  return typeof o.id === 'number' && Number.isFinite(o.id) && typeof o.type === 'string' && o.type !== 'ping'
}

/** Fold a sorted list of unique events into a fresh state. */
export function reduceEvents(events: RunEvent[]): RunState {
  return foldAppend(initialRunState(), events)
}

function foldAppend(base: RunState, sortedNew: RunEvent[]): RunState {
  if (!sortedNew.length) return base
  const s: RunState = { ...base }
  const events = base.events.slice()
  const timeline = base.timeline.slice()
  for (const e of sortedNew) {
    applyOne(s, e)
    events.push(e)
    timeline.push(toTimelineItem(e))
  }
  s.events = events
  s.timeline = timeline
  return s
}

/**
 * Apply a batch of events in any order, with duplicates. Events already known (by id) are dropped. If an event
 * lands before the newest one we already have, the state is rebuilt from the full ordered list so the result is
 * identical to loading the ledger fresh.
 */
export function applyEvents(state: RunState, incoming: unknown[]): RunState {
  const fresh = new Map<number, RunEvent>()
  for (const raw of incoming) {
    if (!isRunEvent(raw)) continue
    if (bsearch(state.events, raw.id) !== -1) continue
    fresh.set(raw.id, raw)
  }
  if (!fresh.size) return state
  const batch = [...fresh.values()].sort((a, b) => a.id - b.id)
  if (batch[0].id > state.lastId) return foldAppend(state, batch)
  const merged = [...state.events, ...batch].sort((a, b) => a.id - b.id)
  return reduceEvents(merged)
}

/** Elapsed-based phase progress used by the stepper summary (complete + skipped phases over the canonical list). */
export function phaseProgress(phases: PhaseInfo[]): { done: number; total: number } {
  const canonical = phases.filter(p => (MISSION_PHASES as readonly string[]).includes(p.phase))
  const done = canonical.filter(p => p.status === 'complete' || p.status === 'skipped').length
  return { done, total: canonical.length }
}

/** Map the pending-approvals API row onto the shape the approval card renders. */
export function pendingToInfo(a: {
  id: string
  type: string
  command: string | null
  reason: string | null
  side_effect: string
  risk: string
  phase: string | null
  expires_at: string | null
  created_at: string
  grantable?: boolean
}): ApprovalInfo {
  return {
    id: a.id,
    type: a.type,
    command: a.command,
    reason: a.reason,
    sideEffect: a.side_effect,
    risk: a.risk,
    phase: a.phase,
    requestedAt: a.created_at,
    expiresAt: a.expires_at,
    grantable: a.grantable === true,
    eventId: 0,
  }
}
