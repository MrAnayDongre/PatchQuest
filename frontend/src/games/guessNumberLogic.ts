export type GuessResult = 'correct' | 'higher' | 'lower' | 'invalid'

export function evaluateGuess(guess: number, target: number, min = 1, max = 100): GuessResult {
  if (!Number.isFinite(guess) || guess < min || guess > max) return 'invalid'
  if (guess === target) return 'correct'
  return guess < target ? 'higher' : 'lower'
}

export function createTarget(min = 1, max = 100): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

export function hintForResult(result: Exclude<GuessResult, 'invalid' | 'correct'>): string {
  return result === 'higher' ? 'Higher!' : 'Lower!'
}
