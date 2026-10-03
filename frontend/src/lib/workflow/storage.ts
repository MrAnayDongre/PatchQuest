import type { WfDef } from './types'

/** Browser-side conveniences. Every access is guarded: private windows and blocked storage must never break editing. */
const DRAFT = (key: string) => `patchquest-wf-draft:${key}`

function read<T>(k: string): T | null {
  try {
    const raw = localStorage.getItem(k)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}
function write(k: string, v: unknown): boolean {
  try {
    localStorage.setItem(k, JSON.stringify(v))
    return true
  } catch {
    return false
  }
}
function drop(k: string): void {
  try {
    localStorage.removeItem(k)
  } catch {
    // ignore
  }
}

export interface Draft {
  def: WfDef
  savedAt: number
  /** version the draft was started from, if any */
  baseId: string | null
}

export const loadDraft = (key: string): Draft | null => read<Draft>(DRAFT(key))
export const saveDraft = (key: string, d: Draft): boolean => write(DRAFT(key), d)
export const clearDraft = (key: string): void => drop(DRAFT(key))
