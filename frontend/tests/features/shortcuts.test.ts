import { describe, expect, it } from 'vitest'
import { GO_WINDOW_MS, nextShortcut, type KeyInfo, type ShortcutState } from '../../src/app/shortcuts'

const key = (k: string, extra: Partial<KeyInfo> = {}): KeyInfo => ({ key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, typing: false, ...extra })
const idle: ShortcutState = { goAt: null }

describe('keyboard shortcuts', () => {
  it('opens the palette with Ctrl or Cmd+K, even while typing', () => {
    expect(nextShortcut(idle, key('k', { ctrlKey: true, typing: true }), 0).action).toBe('palette')
    expect(nextShortcut(idle, key('K', { metaKey: true }), 0).action).toBe('palette')
  })
  it('handles "g then r" as a sequence', () => {
    const first = nextShortcut(idle, key('g'), 1000)
    expect(first.action).toBeUndefined()
    expect(nextShortcut(first.state, key('r'), 1500).action).toBe('go-runs')
  })
  it('forgets the g prefix after a pause', () => {
    const first = nextShortcut(idle, key('g'), 1000)
    expect(nextShortcut(first.state, key('r'), 1000 + GO_WINDOW_MS + 1).action).toBeUndefined()
  })
  it('an unknown second key cancels the sequence', () => {
    const first = nextShortcut(idle, key('g'), 0)
    const second = nextShortcut(first.state, key('z'), 10)
    expect(second.action).toBeUndefined()
    expect(nextShortcut(second.state, key('r'), 20).action).toBeUndefined()
  })
  it('n starts a run and ? opens help', () => {
    expect(nextShortcut(idle, key('n'), 0).action).toBe('new-run')
    expect(nextShortcut(idle, key('?', { shiftKey: true }), 0).action).toBe('help')
  })
  it('never fires while typing or with modifiers', () => {
    expect(nextShortcut(idle, key('n', { typing: true }), 0).action).toBeUndefined()
    expect(nextShortcut(idle, key('n', { ctrlKey: true }), 0).action).toBeUndefined()
  })
})
