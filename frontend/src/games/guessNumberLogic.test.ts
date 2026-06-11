import { describe, expect, it } from 'vitest'
import { evaluateGuess, createTarget, hintForResult } from './guessNumberLogic'

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
