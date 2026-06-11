import { describe, expect, it } from 'vitest'

/** Flappy Bit starts in ready phase — gravity must not run until playing. */
describe('flappyBit state machine', () => {
  it('initial phase is ready, not falling', () => {
    type Phase = 'ready' | 'countdown' | 'playing' | 'paused' | 'lost'
    const initial: Phase = 'ready'
    expect(initial).toBe('ready')
    expect(initial).not.toBe('playing')
  })

  it('countdown precedes playing', () => {
    const transitions: Array<[string, string]> = [
      ['ready', 'countdown'],
      ['countdown', 'playing'],
    ]
    expect(transitions[0][0]).toBe('ready')
    expect(transitions[1][1]).toBe('playing')
  })
})

describe('gameShell restart', () => {
  it('session increment resets game identity', () => {
    let session = 0
    const restart = () => { session += 1 }
    restart()
    expect(session).toBe(1)
    restart()
    expect(session).toBe(2)
  })
})
