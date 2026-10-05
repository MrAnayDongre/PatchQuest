/** Undo/redo as a pure reducer over immutable snapshots. */
export interface History<T> {
  past: T[]
  present: T
  future: T[]
  /** Commits with the same key in a row (typing in one field) fold into a single undo step. */
  lastKey: string | null
}

export type HistoryAction<T> =
  | { type: 'commit'; value: T; key?: string }
  | { type: 'undo' }
  | { type: 'redo' }
  | { type: 'reset'; value: T }

export const HISTORY_LIMIT = 100

export function initHistory<T>(value: T): History<T> {
  return { past: [], present: value, future: [], lastKey: null }
}

export function historyReducer<T>(state: History<T>, action: HistoryAction<T>, limit = HISTORY_LIMIT): History<T> {
  switch (action.type) {
    case 'commit': {
      if (action.value === state.present) return state
      if (action.key && action.key === state.lastKey) return { ...state, present: action.value, future: [] }
      const past = [...state.past, state.present]
      return { past: past.length > limit ? past.slice(past.length - limit) : past, present: action.value, future: [], lastKey: action.key ?? null }
    }
    case 'undo': {
      if (!state.past.length) return state
      return { past: state.past.slice(0, -1), present: state.past[state.past.length - 1], future: [state.present, ...state.future], lastKey: null }
    }
    case 'redo': {
      if (!state.future.length) return state
      return { past: [...state.past, state.present], present: state.future[0], future: state.future.slice(1), lastKey: null }
    }
    case 'reset':
      return initHistory(action.value)
  }
}

export const canUndo = <T,>(h: History<T>): boolean => h.past.length > 0
export const canRedo = <T,>(h: History<T>): boolean => h.future.length > 0
