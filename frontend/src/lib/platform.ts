/** Pure helpers for the integrations, repositories and "why" surfaces: form -> request mapping, labels, plain-language copy. */
import type { Explanation, IntegrationKind, PreferenceOrigin, ProfileField, SecretInput, SecretRef } from '../api/platform'
import { humanize, plural } from './format'

export const ALL_PERMISSIONS = ['run.read', 'run.create', 'settings.read', 'settings.write', 'connector.manage', 'repository.manage']
export const LOCAL_WORKSPACE = { id: 'ws_local', name: 'Local workspace', role: 'owner', permissions: ALL_PERMISSIONS }

/** A remembered fact's key as a person would read it ("profile.test_commands" -> "test commands"; "run:ab12…" -> "note from an earlier run"). */
function factName(key: string): string {
  if (key.startsWith('profile.')) return key.slice(8).replace(/_/g, ' ')
  return key.startsWith('run:') ? 'note from an earlier run' : key
}

/* ------------------------------------------------------------ integrations */
/** Comma- or newline-separated entries, trimmed, blanks dropped, duplicates removed. */
export function parseList(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map(s => s.trim()).filter(Boolean))]
}

/** Lines of `name=value`. Returns the map and the lines that could not be read. */
export function parseMap(text: string): { map: Record<string, string>; bad: string[] } {
  const map: Record<string, string> = {}
  const bad: string[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const i = line.indexOf('=')
    const name = i > 0 ? line.slice(0, i).trim() : ''
    const value = i > 0 ? line.slice(i + 1).trim() : ''
    if (!name || !value) bad.push(line)
    else map[name] = value
  }
  return { map, bad }
}

export interface SecretDraft {
  mode: 'env' | 'stored'
  text: string
}
export interface ConnectDraft {
  name: string
  config: Record<string, string>
  secrets: Record<string, SecretDraft>
}

export type BuildResult =
  | { ok: true; body: { workspace_id: string; kind: string; name: string; config: Record<string, unknown>; secrets: Record<string, SecretInput> } }
  | { ok: false; errors: Record<string, string> }

/** Turn what the person typed into the POST body. Optional fields left blank are omitted; secrets are `{env}` or `{value}`. */
export function buildCreateBody(workspaceId: string, kind: IntegrationKind, draft: ConnectDraft): BuildResult {
  const errors: Record<string, string> = {}
  const config: Record<string, unknown> = {}
  const name = draft.name.trim()
  if (!name) errors.name = 'Give this connection a name.'

  for (const f of kind.config) {
    const text = (draft.config[f.name] ?? '').trim()
    if (!text) {
      if (f.required) errors[`config.${f.name}`] = `${humanize(f.name)} is required.`
      continue
    }
    if (f.type === 'string_list') config[f.name] = parseList(text)
    else if (f.type === 'string_map') {
      const { map, bad } = parseMap(text)
      if (bad.length) errors[`config.${f.name}`] = `Use one name=https://… per line. Check: ${bad[0]}`
      else config[f.name] = map
    } else config[f.name] = text
  }

  const secrets: Record<string, SecretInput> = {}
  for (const s of kind.secrets) {
    const d = draft.secrets[s.name] ?? { mode: 'env', text: '' }
    const text = d.mode === 'env' ? d.text.trim() : d.text
    if (!text) {
      if (s.required) errors[`secret.${s.name}`] = d.mode === 'env' ? `Enter the name of the environment variable that holds ${humanize(s.name).toLowerCase()}.` : `${humanize(s.name)} is required.`
      continue
    }
    secrets[s.name] = d.mode === 'env' ? { env: text } : { value: text }
  }
  if (Object.keys(errors).length) return { ok: false, errors }
  return { ok: true, body: { workspace_id: workspaceId, kind: kind.kind, name, config, secrets } }
}

/** How a saved secret is described. Only what the API returns: the variable name, or "stored". Never a value. */
export function describeSecret(ref: SecretRef | undefined): string {
  if (ref?.env) return `{env: ${ref.env}}`
  if (ref?.stored) return '{stored: true}'
  return 'not set'
}

export const isSimulated = (name: string): boolean => /\(simulator\)/i.test(name)

export function webhookUrl(path: string, origin: string): string {
  return `${origin.replace(/\/$/, '')}${path.startsWith('/') ? path : `/${path}`}`
}

export function configSummary(config: Record<string, unknown>): string {
  return Object.entries(config)
    .map(([k, v]) => `${humanize(k)}: ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' && v ? Object.keys(v).join(', ') : String(v)}`)
    .join(' · ')
}

export const SIDE_EFFECT_LABEL: Record<string, string> = {
  READ_ONLY: 'Reads only',
  WORKSPACE_WRITE: 'Writes files in the isolated workspace',
  EXTERNAL_WRITE: 'Changes something outside PatchQuest',
  NETWORK: 'Uses the network',
}
export const sideEffectLabel = (s: string): string => SIDE_EFFECT_LABEL[s] ?? humanize(s)

/* ------------------------------------------------------------ repositories */
/** Where a profile value came from, in words. */
export function profileSourceLabel(f: Pick<ProfileField, 'source' | 'reason' | 'evidence'>): string {
  const files = Array.isArray(f.evidence?.files) ? (f.evidence.files as unknown[]).filter((x): x is string => typeof x === 'string') : []
  switch (f.source) {
    case 'user_explicit':
      return 'You set this'
    case 'repository_detected':
      return files.length ? `Detected from ${files.slice(0, 3).join(', ')}` : f.reason ? humanize(f.reason) : 'Detected from the repository'
    case 'configuration':
      return 'From configuration'
    case 'test_result':
      return 'Learned from a test run'
    case 'ci':
      return 'Learned from CI'
    case 'accepted_patch':
      return 'Learned from an accepted patch'
    case 'rejected_patch':
      return 'Learned from a rejected patch'
    case 'workflow_observation':
      return 'Seen in a workflow'
    case 'import':
      return 'Imported'
    case 'agent_inference':
      return 'Guessed by the agent'
    default:
      return humanize(f.source)
  }
}

export function memorySourceLabel(source: string): string {
  return profileSourceLabel({ source, reason: '', evidence: {} })
}

/** Show a stored value compactly: lists joined, objects as JSON. */
export function showValue(v: unknown): string {
  if (v === null || v === undefined) return 'not set'
  if (typeof v === 'string') return v
  if (Array.isArray(v) && v.every(x => typeof x === 'string')) return v.join(', ')
  return JSON.stringify(v)
}

/** Text from an input box to a JSON value: valid JSON (true, 3, ["a"]) as such, anything else as text. */
export function parseValueInput(text: string): unknown {
  const t = text.trim()
  try {
    return JSON.parse(t)
  } catch {
    return t
  }
}

export function preferenceOrigin(by: PreferenceOrigin | string): { label: string; scope: string | null } {
  if (typeof by === 'string') return { label: 'Default', scope: null }
  return { label: `${humanize(by.scope)} setting`, scope: by.scope }
}

/* ------------------------------------------------------------ why */
const possessive = (scope: string): string => (scope === 'organization' ? "your organisation's" : scope === 'user' ? 'your personal' : `your ${scope}`)

type Obj = Record<string, any>

export interface WhyEntry {
  id: number
  title: string
  details: string[]
  phase: string | null
  createdAt: string
  tone: 'info' | 'warning' | 'neutral'
}

function becauseText(b: Obj): string {
  if (b.kind === 'preference') return `${possessive(String(b.scope ?? 'repository'))} preference${b.key ? ` for ${b.key}` : ''} says so`
  if (b.kind === 'planner') return 'the plan named it and it is allowed to run unattended'
  if (b.kind === 'repository_detection') return "it was found in the repository's manifests"
  return typeof b.note === 'string' ? b.note : humanize(String(b.kind ?? 'unknown reason')).toLowerCase()
}

/** One recorded explanation in plain language. Reads the payload defensively: a field that is missing is simply left out. */
export function explain(e: Explanation): WhyEntry {
  const p: Obj = e.payload ?? {}
  const base = { id: e.id, phase: e.phase, createdAt: e.created_at }
  switch (e.type) {
    case 'decision_explained': {
      const chosen: string[] = Array.isArray(p.chosen) ? p.chosen.map(String) : []
      const because: Obj[] = Array.isArray(p.because) ? p.because : []
      const subject = p.decision === 'test_commands' ? 'test command' : humanize(String(p.decision ?? 'option')).toLowerCase()
      const reasons = because.map(becauseText).join(' and ')
      const details: string[] = []
      for (const o of Array.isArray(p.overridden) ? (p.overridden as Obj[]) : []) details.push(`Overrode ${possessive(String(o.scope ?? 'lower'))} setting: ${showValue(o.value)}`)
      for (const r of Array.isArray(p.rejected) ? (p.rejected as Obj[]) : []) details.push(`Not used: ${r.command ?? showValue(r)}${r.why ? ` (${r.why})` : ''}`)
      const head = chosen.length ? `Used ${chosen.join('; ')} as the ${plural(chosen.length, subject).replace(/^\d+ /, '')}` : e.message
      return { ...base, title: reasons ? `${head} because ${reasons}` : head, details, tone: 'info' }
    }
    case 'assumption_invalidated': {
      const details = (Array.isArray(p.rejected) ? (p.rejected as Obj[]) : []).map(r => `${r.command ?? showValue(r)}${r.why ? `: ${r.why}` : ''}`)
      return { ...base, title: e.message || 'An assumption no longer held', details, tone: 'warning' }
    }
    case 'repository_profile_changed': {
      const changed = [...(Array.isArray(p.changed) ? p.changed : []), ...(Array.isArray(p.removed) ? p.removed : [])]
      return { ...base, title: changed.length ? `The repository changed since last time: ${changed.map(c => humanize(String(c)).toLowerCase()).join(', ')} updated in its profile` : e.message, details: [], tone: 'info' }
    }
    case 'memory_selected': {
      const n = Number(p.selected ?? 0)
      const considered = Number(p.considered ?? n)
      const tokens = Number(p.tokens ?? 0)
      const details: string[] = []
      if (p.stale_rejected) details.push(`${plural(Number(p.stale_rejected), 'fact')} left out because they looked out of date`)
      if (p.low_confidence_rejected) details.push(`${plural(Number(p.low_confidence_rejected), 'fact')} left out for low confidence`)
      if (p.over_budget) details.push(`${plural(Number(p.over_budget), 'fact')} left out to stay within the memory budget`)
      for (const i of Array.isArray(p.items) ? (p.items as Obj[]) : []) {
        details.push(`${factName(String(i.key))} (${memorySourceLabel(String(i.source)).toLowerCase()}${i.trusted === false ? ', untrusted' : ''})${i.reason ? `: ${i.reason}` : ''}`)
      }
      return { ...base, title: `Remembered ${n} of ${plural(considered, 'fact')} (about ${plural(tokens, 'token')})`, details, tone: 'neutral' }
    }
    case 'memory_invalidated': {
      const keys: string[] = Array.isArray(p.keys) ? p.keys.map(String) : []
      const n = Array.isArray(p.ids) ? p.ids.length : keys.length
      return { ...base, title: `${plural(n, 'remembered fact')} no longer matched the repository and ${n === 1 ? 'was' : 'were'} set aside`, details: keys, tone: 'warning' }
    }
    case 'memory_withheld':
      return { ...base, title: e.message || 'Memory was kept out of this run', details: ['A policy decides whether remembered facts may be sent to this kind of model.'], tone: 'warning' }
    default:
      return { ...base, title: e.message || humanize(e.type), details: [], tone: 'neutral' }
  }
}

/** The value to send for a preference or profile field. Lists are one entry per line (commands may contain commas). */
export function valueFromText(text: string, asList: boolean): unknown {
  if (asList) return text.split('\n').map(s => s.trim()).filter(Boolean)
  return parseValueInput(text)
}

/** Text to prefill an edit box with, and whether the value is edited as a list. */
export function valueToText(v: unknown, keyHint = ''): { text: string; list: boolean } {
  if (Array.isArray(v) && v.every(x => typeof x === 'string')) return { text: v.join('\n'), list: true }
  if (v === null || v === undefined) return { text: '', list: /(commands|paths|roots|languages)$/.test(keyHint) }
  return { text: typeof v === 'string' ? v : JSON.stringify(v), list: false }
}
