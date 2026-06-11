import { useCallback, useState } from 'react'
import GameShell from './GameShell'
import { createTarget, evaluateGuess, hintForResult } from './guessNumberLogic'
import { useGameKeyboardLock } from './useGameKeyboard'
import type { GameShellState } from './types'

interface GuessNumberProps {
  onExit: () => void
}

export default function GuessNumber({ onExit }: GuessNumberProps) {
  const [session, setSession] = useState(0)
  const [target, setTarget] = useState(() => createTarget())
  const [guess, setGuess] = useState('')
  const [attempts, setAttempts] = useState(0)
  const [history, setHistory] = useState<string[]>([])
  const [state, setState] = useState<GameShellState>('ready')
  const [lastHint, setLastHint] = useState('Guess a number between 1 and 100')

  const resetGame = useCallback(() => {
    setTarget(createTarget())
    setGuess('')
    setAttempts(0)
    setHistory([])
    setLastHint('Guess a number between 1 and 100')
    setState('ready')
    setSession(s => s + 1)
  }, [])

  const handleStart = () => setState('running')

  const handleGuess = useCallback(() => {
    if (state !== 'running') return
    const num = parseInt(guess, 10)
    const result = evaluateGuess(num, target)
    if (result === 'invalid') {
      setLastHint('Enter a whole number between 1 and 100.')
      return
    }
    const nextAttempts = attempts + 1
    setAttempts(nextAttempts)
    if (result === 'correct') {
      const msg = `Correct! You got it in ${nextAttempts} attempt${nextAttempts === 1 ? '' : 's'}.`
      setLastHint(msg)
      setHistory(h => [`Attempt ${nextAttempts}: ${num} — Correct!`, ...h])
      setState('won')
    } else {
      const hint = hintForResult(result)
      setLastHint(hint)
      setHistory(h => [`Attempt ${nextAttempts}: ${num} — ${hint}`, ...h])
    }
    setGuess('')
  }, [attempts, guess, state, target])

  useGameKeyboardLock(state === 'running')

  return (
    <GameShell
      key={session}
      title="Guess Number"
      description="Binary search your way to victory."
      controls="Type a number 1–100 and press Enter or Guess."
      state={state}
      stats={[
        { label: 'Range', value: '1–100' },
        { label: 'Attempts', value: attempts },
      ]}
      onStart={handleStart}
      onRestart={resetGame}
      onExit={onExit}
      resultTitle="Number Found!"
      resultMessage={lastHint}
      resultDetail={
        history.length > 0 ? (
          <ul className="game-history">
            {history.slice(0, 6).map((line, i) => (
              <li key={`${session}-${i}`}>{line}</li>
            ))}
          </ul>
        ) : null
      }
    >
      <div className="game-guess">
        <p className={`game-guess__hint ${state === 'won' ? 'game-guess__hint--won' : ''}`}>
          {lastHint}
        </p>
        {state === 'running' && (
          <div className="game-guess__input-row">
            <input
              type="number"
              inputMode="numeric"
              aria-label="Your guess"
              value={guess}
              onChange={(e) => setGuess(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  handleGuess()
                }
              }}
              min={1}
              max={100}
              className="game-guess__input"
              autoFocus
            />
            <button type="button" className="game-btn game-btn--primary" onClick={handleGuess}>
              Guess
            </button>
          </div>
        )}
        {history.length > 0 && state === 'running' && (
          <ul className="game-history game-history--inline">
            {history.slice(0, 4).map((line, i) => (
              <li key={`run-${i}`}>{line}</li>
            ))}
          </ul>
        )}
      </div>
    </GameShell>
  )
}
