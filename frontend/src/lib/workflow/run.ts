import type { Tone } from '../eventCopy'
import { humanize } from '../format'
import type { WfDef } from './types'

export interface WfStep {
  node_id: string
  visit: number
  status: string
  output?: unknown
  error?: string | null
  wait_kind?: string | null
  wake_at?: string | null
  child_run_id?: string | null
  decision?: string | null
  decided_by?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface WfEvent {
  id: number
  type: string
  node_id: string | null
  actor: string
  message: string | null
  payload: unknown
  created_at: string
}

export interface WfRun {
  id: string
  workflow_id: string
  workspace_id: string
  status: string
  error: string | null
  created_by?: string | null
  created_at: string
  updated_at?: string
  completed_at: string | null
  steps: WfStep[]
  events: WfEvent[]
  trigger: Record<string, unknown>
  vars: Record<string, unknown>
}

export const RUN_TERMINAL = ['completed', 'failed', 'cancelled']
export const isRunTerminal = (status: string): boolean => RUN_TERMINAL.includes(status)

export type NodeVisual = 'idle' | 'running' | 'waiting' | 'done' | 'failed' | 'skipped' | 'uncertain'

/** The latest visit of each node decides how it is drawn. */
export function latestSteps(steps: WfStep[]): Map<string, WfStep> {
  const m = new Map<string, WfStep>()
  for (const s of steps) {
    const prev = m.get(s.node_id)
    if (!prev || s.visit >= prev.visit) m.set(s.node_id, s)
  }
  return m
}

export function visualOf(step: WfStep | undefined): NodeVisual {
  if (!step) return 'idle'
  switch (step.status) {
    case 'running':
    case 'pending': return 'running'
    case 'waiting': return 'waiting'
    case 'succeeded': return 'done'
    case 'failed': return 'failed'
    case 'skipped': return 'skipped'
    case 'uncertain': return 'uncertain'
    default: return 'idle'
  }
}

export function nodeVisuals(def: WfDef, steps: WfStep[]): Record<string, NodeVisual> {
  const latest = latestSteps(steps)
  const out: Record<string, NodeVisual> = {}
  for (const n of def.nodes) out[n.id] = visualOf(latest.get(n.id))
  return out
}

export const VISUAL_COPY: Record<NodeVisual, { label: string; tone: Tone }> = {
  idle: { label: 'Not reached', tone: 'muted' },
  running: { label: 'Working', tone: 'info' },
  waiting: { label: 'Waiting', tone: 'warning' },
  done: { label: 'Done', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  skipped: { label: 'Skipped', tone: 'muted' },
  uncertain: { label: 'Needs your decision', tone: 'danger' },
}

export function waitingExplanation(step: WfStep): string {
  switch (step.wait_kind) {
    case 'approval': return 'Waiting for a person to approve or deny.'
    case 'timer': return 'Waiting for a timer to finish.'
    case 'event': return 'Waiting for an outside event.'
    case 'child_run': return 'Waiting for the agent run to finish.'
    default: return 'Waiting.'
  }
}

export function runStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: 'Starting', running: 'Running', waiting: 'Waiting', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled',
  }
  return map[status] ?? humanize(status)
}

export function runStatusTone(status: string): Tone {
  return status === 'completed' ? 'success' : status === 'failed' ? 'danger' : status === 'waiting' ? 'warning' : status === 'cancelled' ? 'muted' : 'info'
}

/** Steps a person has to act on right now. */
export function pendingApprovals(steps: WfStep[]): WfStep[] {
  return [...latestSteps(steps).values()].filter(s => s.status === 'waiting' && s.wait_kind === 'approval')
}

export function uncertainSteps(steps: WfStep[]): WfStep[] {
  return [...latestSteps(steps).values()].filter(s => s.status === 'uncertain')
}

const EVENT_TITLES: Record<string, string> = {
  workflow_started: 'Workflow started',
  workflow_completed: 'Workflow completed',
  workflow_failed: 'Workflow failed',
  workflow_cancelled: 'Workflow cancelled',
  step_started: 'Step started',
  step_succeeded: 'Step finished',
  step_failed: 'Step failed',
  step_waiting: 'Step is waiting',
  step_woken: 'Step woke up',
  step_timeout: 'Step timed out',
  step_uncertain: 'A person needs to check this step',
  step_resolved: 'A person settled this step',
  approval_requested: 'Waiting for approval',
  approval_decided: 'Approval answered',
  approval_expired: 'Approval timed out',
}

export function describeWfEvent(e: WfEvent): { title: string; detail: string | null; tone: Tone } {
  const title = EVENT_TITLES[e.type] ?? humanize(e.type)
  const tone: Tone = /failed|uncertain/.test(e.type) ? 'danger' : /waiting|requested|timeout|expired/.test(e.type) ? 'warning' : /succeeded|completed|decided|resolved/.test(e.type) ? 'success' : 'neutral'
  return { title: e.node_id ? `${title}: ${e.node_id}` : title, detail: e.message, tone }
}

/** The question an approval step is asking, from its approval_requested event. */
export function approvalMessage(events: WfEvent[], nodeId: string): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === 'approval_requested' && events[i].node_id === nodeId) return events[i].message
  }
  return null
}
