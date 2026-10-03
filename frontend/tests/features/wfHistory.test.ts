import { describe, expect, it } from 'vitest'
import { canRedo, canUndo, historyReducer, initHistory } from '../../src/lib/workflow/history'

describe('history reducer', () => {
  it('commits, undoes and redoes', () => {
    let h = initHistory('a')
    h = historyReducer(h, { type: 'commit', value: 'b' })
    h = historyReducer(h, { type: 'commit', value: 'c' })
    expect(h.present).toBe('c')
    h = historyReducer(h, { type: 'undo' })
    expect(h.present).toBe('b')
    h = historyReducer(h, { type: 'undo' })
    expect(h.present).toBe('a')
    expect(canUndo(h)).toBe(false)
    expect(canRedo(h)).toBe(true)
    h = historyReducer(h, { type: 'redo' })
    expect(h.present).toBe('b')
  })
  it('a new edit after undo discards the redo branch', () => {
    let h = initHistory(1)
    h = historyReducer(h, { type: 'commit', value: 2 })
    h = historyReducer(h, { type: 'undo' })
    h = historyReducer(h, { type: 'commit', value: 3 })
    expect(h.future).toEqual([])
    expect(historyReducer(h, { type: 'redo' })).toBe(h)
  })
  it('ignores no-op commits and empty undo/redo', () => {
    const h = initHistory('a')
    expect(historyReducer(h, { type: 'commit', value: 'a' })).toBe(h)
    expect(historyReducer(h, { type: 'undo' })).toBe(h)
    expect(historyReducer(h, { type: 'redo' })).toBe(h)
  })
  it('folds consecutive commits with the same key into one undo step', () => {
    let h = initHistory('')
    for (const v of ['h', 'he', 'hel', 'hello']) h = historyReducer(h, { type: 'commit', value: v, key: 'task' })
    expect(h.past).toEqual([''])
    h = historyReducer(h, { type: 'commit', value: 'hello!', key: 'other' })
    expect(h.past).toEqual(['', 'hello'])
    h = historyReducer(h, { type: 'undo' })
    expect(h.present).toBe('hello')
    h = historyReducer(h, { type: 'commit', value: 'hello2', key: 'task' }) // key reset by undo
    expect(h.past).toEqual(['', 'hello'])
  })
  it('keeps at most `limit` steps', () => {
    let h = initHistory(0)
    for (let i = 1; i <= 10; i++) h = historyReducer(h, { type: 'commit', value: i }, 3)
    expect(h.past).toEqual([7, 8, 9])
  })
  it('reset clears everything', () => {
    let h = historyReducer(initHistory(1), { type: 'commit', value: 2 })
    h = historyReducer(h, { type: 'reset', value: 9 })
    expect(h).toEqual(initHistory(9))
  })
})
