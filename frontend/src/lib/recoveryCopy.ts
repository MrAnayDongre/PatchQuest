import type { ResumePlan } from '../api/types'
import type { Tone } from './eventCopy'

export const DRIFT_COPY: Record<string, { text: string; tone: Tone }> = {
  NO_DRIFT: { text: "Your repository hasn't changed since the checkpoint.", tone: 'success' },
  SAFE_DRIFT: { text: 'Your repository changed, but not in files this run touches.', tone: 'info' },
  CONFLICTING_DRIFT: { text: 'A file this run modifies has changed since the checkpoint.', tone: 'warning' },
  UNKNOWN_DRIFT: { text: "PatchQuest couldn't compare the repository with the checkpoint.", tone: 'warning' },
  NOT_CHECKED: { text: 'The repository was not compared.', tone: 'muted' },
}

export const CERTAINTY_COPY: Record<string, string> = {
  NONE: 'Nothing outside the isolated workspace had happened yet.',
  VERIFIED: 'Anything that already took effect was verified, so it will not be repeated.',
  UNCERTAIN: 'An action may already have taken effect. PatchQuest will check before repeating anything.',
}

export const CATEGORY_COPY: Record<string, { title: string; tone: Tone }> = {
  SAFE_RESUME: { title: 'Safe to continue from the last checkpoint', tone: 'success' },
  SAFE_RETRY: { title: 'Safe to start again from the beginning', tone: 'info' },
  HUMAN_CONFIRMATION_REQUIRED: { title: 'Needs your confirmation first', tone: 'warning' },
  ROLLBACK_REQUIRED: { title: 'Partly written files must be restored first', tone: 'warning' },
  NON_RECOVERABLE: { title: "This run can't be resumed", tone: 'danger' },
}

export type ConfirmKind = 'drift' | 'rollback' | null

/** Which explicit confirmation (if any) the resume plan demands, and whether resuming is possible at all. */
export function resumeRequirements(plan: Pick<ResumePlan, 'CATEGORY'>): { canResume: boolean; confirm: ConfirmKind } {
  switch (plan.CATEGORY) {
    case 'NON_RECOVERABLE':
      return { canResume: false, confirm: null }
    case 'HUMAN_CONFIRMATION_REQUIRED':
      return { canResume: true, confirm: 'drift' }
    case 'ROLLBACK_REQUIRED':
      return { canResume: true, confirm: 'rollback' }
    default:
      return { canResume: true, confirm: null }
  }
}

export function resumeBody(confirm: ConfirmKind, confirmed: boolean): { accept_drift: boolean; rollback: boolean } {
  return { accept_drift: confirm === 'drift' && confirmed, rollback: confirm === 'rollback' && confirmed }
}

export interface OverrideParse {
  overrides: Record<string, number | string | boolean>
  errors: string[]
}

/** Parse "agent.max_model_calls=80" lines. Only `agent.*` keys are allowed; safety settings cannot be overridden. */
export function parseOverrides(text: string): OverrideParse {
  const overrides: Record<string, number | string | boolean> = {}
  const errors: string[] = []
  text.split('\n').forEach((raw, i) => {
    const line = raw.trim()
    if (!line) return
    const eq = line.indexOf('=')
    if (eq <= 0) {
      errors.push(`Line ${i + 1}: use the form agent.name=value.`)
      return
    }
    const key = line.slice(0, eq).trim()
    const val = line.slice(eq + 1).trim()
    if (!/^agent\.[A-Za-z0-9_]+$/.test(key)) {
      errors.push(`Line ${i + 1}: only agent.* settings can be changed for a fork (got "${key}").`)
      return
    }
    if (val === '') {
      errors.push(`Line ${i + 1}: "${key}" needs a value.`)
      return
    }
    overrides[key] = val === 'true' ? true : val === 'false' ? false : /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val
  })
  return { overrides, errors }
}
