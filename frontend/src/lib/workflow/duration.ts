export type Unit = 'seconds' | 'minutes' | 'hours' | 'days'

export const UNIT_SECONDS: Record<Unit, number> = { seconds: 1, minutes: 60, hours: 3600, days: 86400 }

/** Largest unit that divides the value evenly, so 86400 shows as "1 day" and 90 as "90 seconds". */
export function splitDuration(seconds: number | undefined): { amount: number | ''; unit: Unit } {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return { amount: '', unit: 'minutes' }
  for (const unit of ['days', 'hours', 'minutes'] as Unit[]) {
    if (seconds % UNIT_SECONDS[unit] === 0) return { amount: seconds / UNIT_SECONDS[unit], unit }
  }
  return { amount: seconds, unit: 'seconds' }
}

export function toSeconds(amount: number | '', unit: Unit): number | undefined {
  if (amount === '' || !Number.isFinite(amount) || amount <= 0) return undefined
  return Math.round(amount * UNIT_SECONDS[unit])
}

/** Coerce text typed for a value: keep numbers and booleans when the original was one, otherwise text. */
export function coerceLike(text: string, original: unknown): unknown {
  if (typeof original === 'number' && /^-?\d+(\.\d+)?$/.test(text.trim())) return Number(text)
  if (typeof original === 'boolean' && /^(true|false)$/.test(text.trim())) return text.trim() === 'true'
  return text
}

/** For agent overrides, where numbers are expected: "80" becomes 80, "true" becomes true. */
export function coerceAuto(text: string): unknown {
  const t = text.trim()
  if (/^-?\d+(\.\d+)?$/.test(t)) return Number(t)
  if (t === 'true') return true
  if (t === 'false') return false
  return text
}

export function showValue(v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v)
}
