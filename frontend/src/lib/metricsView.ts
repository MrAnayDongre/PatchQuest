/** Formatting for the metrics page. Missing values always render as an em dash, never as zero. */
import { formatCount, formatDuration, formatPercent } from './format'

export const rate = (v: number | null | undefined): string => (typeof v === 'number' ? formatPercent(v) : '—')

export const seconds = (v: number | null | undefined): string => (typeof v === 'number' ? formatDuration(v * 1000) : '—')

export const millis = (v: number | null | undefined): string => (typeof v === 'number' ? formatDuration(v) : '—')

export const count = (v: number | null | undefined): string => (typeof v === 'number' ? formatCount(v) : '—')

export function pair(p: { p50: number | null; p95: number | null } | undefined, fmt: (v: number | null) => string): string {
  if (!p || (p.p50 === null && p.p95 === null)) return '—'
  return `${fmt(p.p50)} typical, ${fmt(p.p95)} slowest 5%`
}

export function money(total: number | null | undefined, currency: string | null | undefined): string {
  if (typeof total !== 'number') return '—'
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || 'USD', maximumFractionDigits: 4 }).format(total)
  } catch {
    return `${total.toFixed(4)} ${currency ?? ''}`.trim()
  }
}

export const WINDOWS = [
  { id: '24h', label: 'Last 24 hours' },
  { id: '7d', label: 'Last 7 days' },
  { id: '30d', label: 'Last 30 days' },
]

export const GROUPINGS = [
  { id: '', label: 'No grouping' },
  { id: 'model', label: 'By model' },
  { id: 'provider', label: 'By provider' },
  { id: 'repository', label: 'By repository' },
]
