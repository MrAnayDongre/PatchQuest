/** Small, pure formatting helpers shared across the UI. */

const SEC = 1000
const MIN = 60 * SEC
const HOUR = 60 * MIN
const DAY = 24 * HOUR

export function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}

/** "just now", "5 min ago", "3 h ago", "yesterday", "12 Mar". */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  const t = parseTime(iso)
  if (t === null) return ''
  const diff = now - t
  if (diff < 0) return diff > -MIN ? 'just now' : `in ${formatDuration(-diff)}`
  if (diff < 10 * SEC) return 'just now'
  if (diff < MIN) return `${Math.floor(diff / SEC)} s ago`
  if (diff < HOUR) return `${Math.floor(diff / MIN)} min ago`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} h ago`
  if (diff < 2 * DAY) return 'yesterday'
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)} days ago`
  return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** Compact duration: "850 ms", "42 s", "3 min 05 s", "2 h 10 min". */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < SEC) return `${Math.round(ms)} ms`
  if (ms < MIN) return `${ms < 10 * SEC ? (ms / SEC).toFixed(1) : Math.round(ms / SEC)} s`
  if (ms < HOUR) {
    const m = Math.floor(ms / MIN)
    const s = Math.round((ms % MIN) / SEC)
    return s === 60 ? `${m + 1} min` : `${m} min ${String(s).padStart(2, '0')} s`
  }
  if (ms < DAY) {
    const h = Math.floor(ms / HOUR)
    const m = Math.round((ms % HOUR) / MIN)
    return m === 60 ? `${h + 1} h` : `${h} h ${String(m).padStart(2, '0')} min`
  }
  const d = Math.floor(ms / DAY)
  const h = Math.round((ms % DAY) / HOUR)
  return h === 24 ? `${d + 1} d` : `${d} d ${h} h`
}

/** Elapsed time between two ISO stamps (end defaults to `now`). */
export function elapsed(startIso: string | null | undefined, endIso: string | null | undefined, now: number = Date.now()): number | null {
  const start = parseTime(startIso)
  if (start === null) return null
  const end = parseTime(endIso) ?? now
  return Math.max(0, end - start)
}

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function formatCount(n: number): string {
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 10_000) return `${Math.round(n / 1000)}k`
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(Math.round(n * 100) / 100)
}

export function formatPercent(fraction: number | null): string {
  if (fraction === null || !Number.isFinite(fraction)) return '—'
  return `${Math.round(fraction * 100)}%`
}

export function median(values: number[]): number | null {
  if (!values.length) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  return `${text.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

/** Last path segment of a repo path ("/home/a/b/" -> "b"). */
export function baseName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean)
  return parts.length ? parts[parts.length - 1] : path
}

export function shortId(id: string): string {
  return id.slice(0, 8)
}

/** "waiting_approval" -> "Waiting approval". */
export function humanize(token: string): string {
  const base = token === token.toUpperCase() ? token.toLowerCase() : token
  const spaced = base.replace(/[_-]+/g, ' ').trim()
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : ''
}

export function plural(n: number, one: string, many: string = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** A model name for people: a replay's internal session name ("replay:<run>:<run>") reads as what it is. */
export function modelLabel(model: string | null | undefined): string {
  return model?.startsWith('replay:') ? 'recorded answers' : (model ?? '')
}

/** "local:local" and "user:ana" as a person would say it. */
export function actorLabel(actor: string): string {
  const [kind, name] = actor.split(':')
  return name && name !== kind ? name : kind === 'local' ? 'this machine' : actor
}
