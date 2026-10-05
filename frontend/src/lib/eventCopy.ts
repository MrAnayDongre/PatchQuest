import type { RunEvent, RunStatus } from '../api/types'
import { formatDuration, humanize, plural } from './format'
import { phaseLabel } from './phases'

export type EventCategory = 'agent' | 'model' | 'command' | 'patch' | 'validation' | 'approval' | 'system'
export type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'muted'

export const CATEGORY_LABELS: Record<EventCategory, string> = {
  agent: 'Agent',
  model: 'Model',
  command: 'Commands',
  patch: 'Patch',
  validation: 'Validation',
  approval: 'Approval',
  system: 'System',
}

export const CATEGORY_ORDER: EventCategory[] = ['agent', 'model', 'command', 'patch', 'validation', 'approval', 'system']

export interface EventCopy {
  category: EventCategory
  tone: Tone
  title: string
  detail?: string
  /** Monospace text (a command) shown under the title. */
  code?: string
  /** Consecutive events with equal keys collapse into one row. */
  collapseKey: string
}

const STATUS_COPY: Record<RunStatus, string> = {
  created: 'Starting',
  queued: 'Waiting for a worker',
  running: 'Running',
  waiting_approval: 'Waiting for approval',
  cancel_requested: 'Cancelling',
  interrupted: 'Interrupted',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return 'Unknown'
  return (STATUS_COPY as Record<string, string>)[status] ?? humanize(status)
}

const OUTCOME_COPY: Record<string, string> = {
  applied: 'Patch applied to your repository',
  rejected: 'Patch rejected',
  conflict: 'Patch could not be applied',
  no_changes: 'No changes needed',
  no_patch: 'No patch produced',
  read_only: 'Read-only analysis',
}

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return ''
  return OUTCOME_COPY[outcome] ?? humanize(outcome)
}

const OUTCOME_SHORT: Record<string, string> = {
  applied: 'Applied',
  rejected: 'Rejected',
  conflict: 'Conflict',
  no_changes: 'No changes',
  no_patch: 'No patch',
  read_only: 'Read-only',
}

export function outcomeShort(outcome: string | null | undefined): string {
  if (!outcome) return ''
  return OUTCOME_SHORT[outcome] ?? humanize(outcome)
}

const VERDICT_COPY: Record<string, string> = {
  passed: 'Tests passed',
  regression: 'Tests regressed',
  unresolved: 'Tests still failing',
  no_tests: 'No tests ran',
}

export function verdictLabel(verdict: string | null | undefined): string {
  if (!verdict) return ''
  return VERDICT_COPY[verdict] ?? humanize(verdict)
}

export function verdictTone(verdict: string | null | undefined): Tone {
  if (verdict === 'passed') return 'success'
  if (verdict === 'regression') return 'danger'
  if (verdict === 'unresolved') return 'warning'
  return 'muted'
}

const SIDE_EFFECT_COPY: Record<string, string> = {
  PURE: 'Only computes a result; touches nothing',
  READ_ONLY: 'Reads files; changes nothing',
  NETWORK_READ: 'Reads from the network',
  WORKSPACE_WRITE: 'Writes files in the isolated workspace',
  REPOSITORY_WRITE: 'Writes to your repository',
  EXTERNAL_WRITE: 'Sends data to or changes something outside this machine',
  HOST_MUTATION: 'Changes your computer outside the repository',
  DESTRUCTIVE: 'Can delete or overwrite data',
  UNKNOWN: 'Effect unknown. PatchQuest could not tell what this does',
}

export function sideEffectLabel(effect: string | null | undefined): string {
  if (!effect) return SIDE_EFFECT_COPY.UNKNOWN
  return SIDE_EFFECT_COPY[effect] ?? humanize(effect)
}

export function sideEffectTone(effect: string | null | undefined): Tone {
  switch (effect) {
    case 'PURE':
    case 'READ_ONLY':
      return 'success'
    case 'NETWORK_READ':
    case 'WORKSPACE_WRITE':
      return 'info'
    case 'REPOSITORY_WRITE':
    case 'UNKNOWN':
    case 'EXTERNAL_WRITE':
      return 'warning'
    case 'HOST_MUTATION':
    case 'DESTRUCTIVE':
      return 'danger'
    default:
      return 'neutral'
  }
}

export function riskTone(risk: string | null | undefined): Tone {
  const r = (risk ?? '').toLowerCase()
  if (r === 'low' || r === 'safe') return 'success'
  if (r === 'medium' || r === 'moderate') return 'warning'
  if (r === 'high' || r === 'critical' || r === 'dangerous') return 'danger'
  return 'neutral'
}

export const DECISION_LABELS: Record<string, string> = {
  APPROVE_ONCE: 'approved it once',
  APPROVE_FOR_RUN: 'approved it for this run',
  DENY: 'denied it',
  MODIFY: 'approved an edited version',
  CANCEL_RUN: 'denied it and cancelled the run',
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v ? v : undefined
}
function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}
function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}
function fileCount(payload: Record<string, unknown>): number | undefined {
  return Array.isArray(payload.files) ? payload.files.length : undefined
}

/** The human-readable failure message inside a failure payload, if present. */
export function failureMessage(payload: Record<string, unknown> | null | undefined): string | undefined {
  return str(obj(obj(payload).failure).message)
}

/** One readable line (plus optional detail) for any ledger event. Unknown types are humanized, never hidden. */
export function describeEvent(e: RunEvent): EventCopy {
  const p = obj(e.payload)
  const phase = phaseLabel(e.phase)
  const msg = e.message ?? undefined
  const make = (category: EventCategory, tone: Tone, title: string, detail?: string, extra: Partial<EventCopy> = {}): EventCopy => ({
    category,
    tone,
    title,
    detail,
    collapseKey: `${e.type}|${title}`,
    ...extra,
  })

  switch (e.type) {
    case 'run_created':
      return make('system', 'neutral', 'Run created', msg)
    case 'run_state_changed': {
      const from = str(p.from)
      const to = str(p.to) ?? e.status ?? ''
      if (to === 'queued') return make('system', 'info', 'Waiting for a worker', msg)
      if (to === 'waiting_approval') return make('system', 'warning', 'Waiting for your approval', msg)
      if (to === 'running' && from === 'queued') return make('system', 'info', 'A worker picked it up', msg)
      if (to === 'running' && from === 'created') return make('system', 'info', 'Run started', msg)
      if (to === 'running' && from === 'waiting_approval') return make('system', 'info', 'Back to work', msg)
      if (to === 'running' && from === 'interrupted') return make('system', 'info', 'Resumed', msg)
      if (to === 'completed') return make('system', 'success', 'Marked completed', msg)
      if (to === 'failed') return make('system', 'danger', 'Marked failed', msg)
      if (to === 'cancelled') return make('system', 'muted', 'Run cancelled', msg)
      if (to === 'cancel_requested') return make('system', 'warning', 'Cancelling…', msg)
      if (to === 'interrupted') return make('system', 'warning', 'Run interrupted', msg)
      return make('system', 'neutral', `Status: ${statusLabel(to).toLowerCase()}`, msg)
    }
    case 'run_resume_requested': {
      const attempt = e.attempt && e.attempt > 1 ? e.attempt : undefined
      return make('system', 'info', attempt ? `Resumed after interruption (attempt ${attempt})` : 'Resumed after interruption', msg)
    }
    case 'run_interrupted':
      return make('system', 'warning', 'Run interrupted', msg ?? 'The worker stopped while this run was in progress.')
    case 'run_completed': {
      const outcome = outcomeLabel(str(p.outcome))
      const verdict = verdictLabel(str(p.verdict))
      return make('system', 'success', 'Run completed', [outcome, verdict].filter(Boolean).join(' · ') || msg)
    }
    case 'run_failed':
      return make('system', 'danger', 'Run failed', failureMessage(e.payload) ?? msg)

    case 'phase_started':
      return make('agent', 'neutral', `Started: ${phase.toLowerCase()}`, undefined, { collapseKey: `${e.type}|${e.phase}|${e.id}` })
    case 'phase_completed':
      return make('agent', 'success', `Finished: ${phase.toLowerCase()}`, undefined, { collapseKey: `${e.type}|${e.phase}|${e.id}` })
    case 'phase_skipped':
      return make('agent', 'muted', `Skipped: ${phase.toLowerCase()}`, msg, { collapseKey: `${e.type}|${e.phase}|${e.id}` })
    case 'phase_blocked':
      return make('agent', 'warning', `Blocked: ${phase.toLowerCase()}`, msg, { collapseKey: `${e.type}|${e.phase}|${e.id}` })
    case 'phase_failed':
      return make('agent', 'danger', `Failed: ${phase.toLowerCase()}`, failureMessage(e.payload) ?? msg, {
        collapseKey: `${e.type}|${e.phase}|${e.id}`,
      })

    case 'repo_scan_started':
      return make('agent', 'neutral', 'Scanning the repository…')
    case 'repo_scan_completed':
      return make('agent', 'success', 'Repository scanned')
    case 'plan_created':
      return make('agent', 'success', 'Plan created', str(p.summary) ?? undefined)
    case 'plan_scope_overridden':
      return make('agent', 'info', 'Plan updated: this task needs code changes', msg)
    case 'context_selected': {
      const n = Array.isArray(p.items) ? p.items.length : undefined
      return make('agent', 'neutral', n !== undefined ? `Selected ${plural(n, 'file')} for context` : msg ?? 'Selected files for context')
    }
    case 'analysis_generated':
      return make('agent', 'success', 'Analysis ready', msg)

    case 'model_call': {
      const role = str(p.role) ?? 'agent'
      const ms = num(p.duration_ms)
      const who = [str(p.provider), str(p.model)].filter(Boolean).join('/')
      const usage = obj(p.usage)
      const tokens = (num(usage.prompt_tokens) ?? 0) + (num(usage.completion_tokens) ?? 0)
      const detail = [ms !== undefined ? formatDuration(ms) : '', who, tokens ? `${tokens.toLocaleString()} tokens` : '']
        .filter(Boolean)
        .join(' · ')
      return make('model', p.degraded ? 'warning' : 'neutral', `Asked the model (${role})`, detail || undefined, {
        collapseKey: `model_call|${role}`,
      })
    }
    case 'retry_scheduled': {
      const delay = num(p.delay_s)
      return make(
        'model',
        'warning',
        delay !== undefined ? `Retrying in ${formatDuration(delay * 1000)}` : 'Retrying',
        failureMessage(e.payload) ?? undefined,
      )
    }
    case 'provider_failover':
      return make('model', 'warning', `Switched model: ${str(p.from) ?? 'primary'} → ${str(p.to) ?? 'fallback'}`, str(p.reason))
    case 'provider_failover_refused':
      return make('model', 'warning', 'Stayed on the current model', str(p.reason) ?? 'A fallback would have changed the results.')

    case 'patch_empty':
      return make('patch', 'warning', 'The model proposed no edits; asking again', msg)
    case 'patch_missing':
      return make('patch', 'warning', 'No edits were produced for a task that needs them', msg)
    case 'patch_retry':
      return make('patch', 'warning', 'Patch did not apply; trying again', str(p.error) ?? msg)
    case 'patch_proposed': {
      const n = fileCount(p)
      return make('patch', 'info', n !== undefined ? `Patch proposed (${plural(n, 'file')})` : 'Patch proposed')
    }
    case 'patch_staged':
      return make('patch', 'success', 'Patch applied in the isolated workspace', 'Your repository is untouched so far.')
    case 'patch_rejected':
      return make('patch', 'warning', 'Patch not applied', msg?.replace(/^Patch (rejected|not applied):?\s*/i, '') || undefined)
    case 'patch_applied': {
      const n = fileCount(p)
      return make('patch', 'success', n !== undefined ? `Patch applied to your repository (${plural(n, 'file')})` : 'Patch applied to your repository')
    }
    case 'secret_detected':
      return make('patch', 'danger', 'A secret was detected in the patch and blocked', msg)
    case 'promotion_started':
      return make('patch', 'info', 'Writing the patch to your repository…')
    case 'promotion_completed':
      return make('patch', 'success', 'Patch written to your repository')
    case 'promotion_failed':
      return make('patch', 'danger', 'Writing to your repository failed', msg)
    case 'promotion_reconciled':
      return make('patch', 'success', 'Patch was already applied before the interruption', 'It was not applied twice.')
    case 'promotion_rolled_back':
      return make('patch', 'warning', 'Partial changes were rolled back', msg)

    case 'tests_started':
      return make('validation', 'info', 'Running tests…')
    case 'tests_completed': {
      const verdict = str(p.verdict)
      return make('validation', verdictTone(verdict), verdict ? `Validation: ${verdictLabel(verdict).toLowerCase()}` : 'Validation finished', msg)
    }
    case 'baseline_started':
      return make('validation', 'neutral', 'Re-running failing checks without the patch', 'This shows whether the failure was already there.')
    case 'repair_started':
      return make('validation', 'warning', msg ?? 'Repairing failing tests')
    case 'security_scan_completed': {
      const n = num(p.findings_count)
      return make('validation', n ? 'warning' : 'success', n === undefined ? 'Security scan finished' : n ? `Security scan found ${plural(n, 'issue')}` : 'Security scan found nothing')
    }

    case 'command_started':
      return make('command', 'info', 'Running command', undefined, { code: str(p.command) ?? msg, collapseKey: `${e.type}|${e.id}` })
    case 'command_executed': {
      const code = num(p.returncode)
      const dur = num(p.duration_s)
      const timedOut = p.timed_out === true
      const title = timedOut ? 'Command timed out' : code === 0 ? 'Command succeeded' : `Command failed (exit ${code ?? '?'})`
      return make('command', timedOut || (code !== undefined && code !== 0) ? 'warning' : 'success', title, dur !== undefined ? formatDuration(dur * 1000) : undefined, {
        code: str(p.command),
        collapseKey: `${e.type}|${e.id}`,
      })
    }
    case 'command_denied':
      return make('command', 'warning', 'Command not approved', undefined, { code: str(p.command), collapseKey: `${e.type}|${e.id}` })
    case 'command_blocked':
      return make('command', 'danger', 'Command blocked by policy', str(p.reason) ?? msg, { code: str(p.command), collapseKey: `${e.type}|${e.id}` })

    case 'approval_requested':
      return make('approval', 'warning', 'Waiting for your approval', str(p.reason) ?? msg, { code: str(p.command), collapseKey: `${e.type}|${e.id}` })
    case 'approval_decided': {
      const how = DECISION_LABELS[str(p.decision) ?? ''] ?? 'decided'
      return make('approval', str(p.decision) === 'DENY' || str(p.decision) === 'CANCEL_RUN' ? 'warning' : 'success', `You ${how}`, str(p.note))
    }
    case 'approval_expired':
      return make('approval', 'warning', 'Approval timed out and was treated as a denial')
    case 'approval_reused':
      return make('approval', 'neutral', 'Approved earlier in this run', undefined, { code: str(p.command), collapseKey: `${e.type}|${e.id}` })

    case 'checkpoint_created': {
      const seq = num(p.seq)
      const where = phase ? ` after ${phase.toLowerCase()}` : ''
      return make('system', 'muted', seq !== undefined ? `Checkpoint ${seq} saved${where}` : `Checkpoint saved${where}`)
    }
    case 'checkpoint_failed':
      return make('system', 'warning', 'A checkpoint could not be saved', msg ?? 'The run continues but can only resume from an earlier checkpoint.')
    case 'workspace_created':
      return make('system', 'muted', 'Isolated workspace ready')
    case 'report_generated':
      return make('system', 'success', 'Report ready')
    case 'fork_created':
      return make('system', 'info', 'Forked from an earlier checkpoint', msg)
    case 'replay_created':
      return make('system', 'info', 'Replay started', msg)
    case 'replay_completed':
      return make('system', 'success', 'Replay matches the original', msg)
    case 'replay_diverged':
      return make('system', 'warning', 'Replay differs from the original', msg)
    default:
      return make('system', 'neutral', humanize(e.type), msg)
  }
}

/** One line that says where a run stands, e.g. "Patch applied to your repository · Tests passed". */
export function friendlyStatusLine(run: {
  status: string
  outcome?: string | null
  verdict?: string | null
  failure_kind?: string | null
}): string {
  if (run.status === 'failed') {
    return run.failure_kind ? `Failed: ${humanize(run.failure_kind).toLowerCase()}` : 'Failed'
  }
  if (run.status === 'completed') {
    return [outcomeLabel(run.outcome), verdictLabel(run.verdict)].filter(Boolean).join(' · ') || 'Completed'
  }
  return statusLabel(run.status)
}

export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case 'running':
    case 'created':
    case 'queued':
      return 'info'
    case 'waiting_approval':
    case 'interrupted':
    case 'cancel_requested':
      return 'warning'
    case 'completed':
      return 'success'
    case 'failed':
      return 'danger'
    default:
      return 'muted'
  }
}
