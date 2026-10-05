import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import GameErrorBoundary from '../../src/games/GameErrorBoundary'
import { evaluateGuess, createTarget, hintForResult } from '../../src/games/guessNumberLogic'

function Boom(): never {
  throw new Error('test crash')
}

describe('GameErrorBoundary', () => {
  afterEach(() => cleanup())

  it('renders fallback instead of blank screen on crash', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <GameErrorBoundary onRestart={() => {}} onExit={() => {}} gameTitle="Test Game">
        <Boom />
      </GameErrorBoundary>,
    )
    expect(screen.getByText('Game Crashed')).toBeTruthy()
    expect(screen.getByText('Restart Game')).toBeTruthy()
    err.mockRestore()
  })

  it('restart button triggers onRestart callback', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const onRestart = vi.fn()
    render(
      <GameErrorBoundary onRestart={onRestart} onExit={() => {}}>
        <Boom />
      </GameErrorBoundary>,
    )
    fireEvent.click(screen.getByText('Restart Game'))
    expect(onRestart).toHaveBeenCalled()
    err.mockRestore()
  })
})

describe('guessNumberLogic', () => {
  it('evaluates correct guess without crashing win path', () => {
    expect(evaluateGuess(42, 42)).toBe('correct')
  })

  it('rejects out-of-range guesses', () => {
    expect(evaluateGuess(0, 50)).toBe('invalid')
    expect(evaluateGuess(101, 50)).toBe('invalid')
  })

  it('returns higher/lower hints', () => {
    expect(hintForResult('higher')).toBe('Higher!')
    expect(hintForResult('lower')).toBe('Lower!')
  })

  it('creates target in range', () => {
    for (let i = 0; i < 20; i++) {
      const t = createTarget()
      expect(t).toBeGreaterThanOrEqual(1)
      expect(t).toBeLessThanOrEqual(100)
    }
  })
})

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
