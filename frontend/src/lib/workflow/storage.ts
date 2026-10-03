import type { Pos, WfDef } from './types'

/** Browser-side conveniences. Every access is guarded: private windows and blocked storage must never break editing. */
const DRAFT = (key: string) => `patchquest-wf-draft:${key}`
const LAYOUT = (key: string) => `patchquest-wf-layout:${key}`
const VERSIONS = (name: string) => `patchquest-wf-versions:${name}`

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

/** Node positions are editor state, not part of the definition (the backend schema has no place for them). */
export const loadLayout = (key: string): Record<string, Pos> | undefined => read<Record<string, Pos>>(LAYOUT(key)) ?? undefined
export const saveLayout = (key: string, l: Record<string, Pos>): boolean => write(LAYOUT(key), l)

export interface SavedVersion {
  id: string
  version: number
  savedAt: number
}
/** The API lists only the newest version, so this browser remembers the ids it has saved. */
export const loadVersions = (name: string): SavedVersion[] => read<SavedVersion[]>(VERSIONS(name)) ?? []
export function rememberVersion(name: string, v: SavedVersion): void {
  const list = loadVersions(name).filter(x => x.id !== v.id)
  write(VERSIONS(name), [...list, v].sort((a, b) => b.version - a.version).slice(0, 50))
}
