import type { Run, RunEvent } from '../api/types'
import { MISSION_PHASES, type MissionPhase } from './phases'

export type PhaseStatus = 'pending' | 'running' | 'complete' | 'skipped' | 'failed'

export type MissionStatus = 'ready' | 'running' | 'blocked' | 'failed' | 'complete'

export interface PhaseState {
  phase: MissionPhase
  status: PhaseStatus
}

export interface MissionState {
  phases: PhaseState[]
  progress: number
  status: MissionStatus
}

const TERMINAL: ReadonlySet<PhaseStatus> = new Set(['complete', 'skipped'])

function isMissionPhase(phase: string | null | undefined): phase is MissionPhase {
  return !!phase && (MISSION_PHASES as readonly string[]).includes(phase)
}

function applyPhaseTerminal(map: Map<MissionPhase, PhaseStatus>, phase: MissionPhase, status: PhaseStatus) {
  const current = map.get(phase)
  if (current === 'failed') return
  if (status === 'running' && (current === 'complete' || current === 'skipped')) return
  map.set(phase, status)
}

/** Derive timeline, progress, and mission status from persisted + live events. */
export function deriveMissionState(events: RunEvent[], run: Run | null): MissionState {
  const map = new Map<MissionPhase, PhaseStatus>()
  for (const phase of MISSION_PHASES) {
    map.set(phase, 'pending')
  }

  const sorted = [...events].sort((a, b) => {
    const ai = a.id ?? 0
    const bi = b.id ?? 0
    if (ai && bi) return ai - bi
    return (a.created_at || '').localeCompare(b.created_at || '')
  })

  for (const event of sorted) {
    const phase = event.phase
    if (!isMissionPhase(phase)) continue

    switch (event.type) {
      case 'phase_started':
        applyPhaseTerminal(map, phase, 'running')
        break
      case 'phase_completed':
        applyPhaseTerminal(map, phase, 'complete')
        break
      case 'phase_skipped':
        applyPhaseTerminal(map, phase, 'skipped')
        break
      case 'phase_failed':
        map.set(phase, 'failed')
        break
      default:
        break
    }
  }

  // Sub-events that imply phase completion when phase_completed was not emitted separately.
  for (const event of sorted) {
    if (event.type === 'repo_scan_completed') applyPhaseTerminal(map, 'repo_scan', 'complete')
    if (event.type === 'analysis_generated') applyPhaseTerminal(map, 'analysis', 'complete')
    if (event.type === 'security_scan_completed') applyPhaseTerminal(map, 'security_scan', 'complete')
    if (event.type === 'report_generated') applyPhaseTerminal(map, 'final_report', 'complete')
  }

  const runFailed = sorted.some(e => e.type === 'run_failed') || run?.status === 'failed'
  const runCompleted = sorted.some(e => e.type === 'run_completed') || run?.status === 'completed'
  const runBlocked = sorted.some(e => e.type === 'permission_required')
  const hasPhaseFailed = [...map.values()].some(s => s === 'failed')
  const hasStarted = sorted.some(e => e.type === 'phase_started')

  let status: MissionStatus = 'ready'
  if (runBlocked && !runCompleted && !runFailed) {
    status = 'blocked'
  } else if (runFailed || hasPhaseFailed) {
    status = 'failed'
  } else if (runCompleted) {
    status = 'complete'
  } else if (hasStarted || run?.status === 'created' || run?.status === 'running') {
    status = 'running'
  }

  const phases: PhaseState[] = MISSION_PHASES.map(phase => ({
    phase,
    status: map.get(phase) ?? 'pending',
  }))

  if (status === 'complete') {
    for (const p of phases) {
      if (p.status === 'pending' || p.status === 'running') {
        p.status = 'skipped'
      }
    }
  }

  const terminalCount = phases.filter(p => TERMINAL.has(p.status) || p.status === 'failed').length
  let progress = phases.length ? terminalCount / phases.length : 0
  if (status === 'complete') progress = 1
  if (status === 'failed') {
    const failedIdx = phases.findIndex(p => p.status === 'failed')
    progress = failedIdx >= 0 ? failedIdx / phases.length : progress
  }

  return { phases, progress, status }
}

/** Merge SSE/historical events without duplicates. */
export function mergeRunEvents(existing: RunEvent[], incoming: RunEvent): RunEvent[] {
  if (incoming.id != null && existing.some(e => e.id === incoming.id)) {
    return existing
  }
  const key = `${incoming.type}|${incoming.phase}|${incoming.message}|${incoming.created_at}`
  if (existing.some(e => `${e.type}|${e.phase}|${e.message}|${e.created_at}` === key)) {
    return existing
  }
  return [...existing, incoming]
}
