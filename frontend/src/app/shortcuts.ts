import { useEffect, useRef } from 'react'

export type ShortcutAction = 'go-home' | 'go-runs' | 'go-engines' | 'go-settings' | 'go-extras' | 'new-run' | 'help' | 'palette' | 'toggle-theme'

export const GO_KEYS: Record<string, ShortcutAction> = {
  h: 'go-home',
  r: 'go-runs',
  e: 'go-engines',
  s: 'go-settings',
  x: 'go-extras',
}

export interface ShortcutState {
  /** Timestamp of a pending "g" prefix, or null. */
  goAt: number | null
}

export const GO_WINDOW_MS = 1200

export interface KeyInfo {
  key: string
  ctrlKey: boolean
  metaKey: boolean
  altKey: boolean
  shiftKey: boolean
  /** True when focus is in a text field, select or editable element. */
  typing: boolean
}

/** Pure shortcut state machine so the sequence rules are testable without a DOM. */
export function nextShortcut(state: ShortcutState, k: KeyInfo, now: number): { state: ShortcutState; action?: ShortcutAction } {
  const idle: ShortcutState = { goAt: null }
  if ((k.ctrlKey || k.metaKey) && k.key.toLowerCase() === 'k') return { state: idle, action: 'palette' }
  if (k.typing || k.ctrlKey || k.metaKey || k.altKey) return { state: idle }
  const key = k.key
  if (state.goAt !== null && now - state.goAt <= GO_WINDOW_MS) {
    const action = GO_KEYS[key.toLowerCase()]
    return { state: idle, action }
  }
  if (key === 'g') return { state: { goAt: now } }
  if (key === 'n') return { state: idle, action: 'new-run' }
  if (key === '?') return { state: idle, action: 'help' }
  return { state: idle }
}

export function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable || el.getAttribute('role') === 'combobox'
}

export function useGlobalShortcuts(onAction: (a: ShortcutAction) => void, suspended: boolean): void {
  const state = useRef<ShortcutState>({ goAt: null })
  const cb = useRef(onAction)
  cb.current = onAction
  useEffect(() => {
    if (suspended) {
      // Ctrl/Cmd+K still has to work (to close the palette), everything else waits.
    }
    const handler = (e: KeyboardEvent) => {
      const info: KeyInfo = {
        key: e.key,
        ctrlKey: e.ctrlKey,
        metaKey: e.metaKey,
        altKey: e.altKey,
        shiftKey: e.shiftKey,
        typing: isTypingTarget(e.target) || suspended,
      }
      const { state: next, action } = nextShortcut(state.current, info, Date.now())
      state.current = next
      if (action) {
        e.preventDefault()
        cb.current(action)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [suspended])
}

export const SHORTCUT_HELP: { keys: string[]; label: string }[] = [
  { keys: ['Ctrl/⌘', 'K'], label: 'Open the command palette' },
  { keys: ['n'], label: 'Start a new run' },
  { keys: ['g', 'h'], label: 'Go to Home' },
  { keys: ['g', 'r'], label: 'Go to Runs' },
  { keys: ['g', 'e'], label: 'Go to Engines' },
  { keys: ['g', 's'], label: 'Go to Settings' },
  { keys: ['g', 'x'], label: 'Go to Extras' },
  { keys: ['j', 'k'], label: 'Move down or up in a list of runs' },
  { keys: ['Enter'], label: 'Open the selected run' },
  { keys: ['?'], label: 'Show this help' },
]
