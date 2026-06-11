import { useCallback, useEffect, useState } from 'react'
import type { GameMode } from '../api/types'
import GameErrorBoundary from './GameErrorBoundary'
import SpaceRaiders from './SpaceRaiders'
import SnakeByte from './SnakeByte'
import SudokuGrid from './SudokuGrid'
import AsteroidDrift from './AsteroidDrift'
import FlappyBit from './FlappyBit'
import GuessNumber from './GuessNumber'
import XpScreensaver from './XpScreensaver'
import ChillLauncher from './ChillLauncher'
import { getGameInfo } from './GameRegistry'

interface Props {
  game: GameMode
  onClose: () => void
}

export default function GameOverlay({ game, onClose }: Props) {
  const [session, setSession] = useState(0)
  const info = getGameInfo(game)

  const handleRestart = useCallback(() => setSession(s => s + 1), [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    document.body.classList.add('game-overlay-open')
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.classList.remove('game-overlay-open')
    }
  }, [onClose])

  const renderGame = () => {
    const exit = onClose
    switch (game) {
      case 'space-raiders': return <SpaceRaiders key={session} onExit={exit} />
      case 'snake-byte': return <SnakeByte key={session} onExit={exit} />
      case 'sudoku': return <SudokuGrid key={session} onExit={exit} />
      case 'asteroid-drift': return <AsteroidDrift key={session} onExit={exit} />
      case 'flappy-bit': return <FlappyBit key={session} onExit={exit} />
      case 'guess-number': return <GuessNumber key={session} onExit={exit} />
      case 'xp-screensaver': return <XpScreensaver key={session} onExit={exit} />
      case 'chill': return <ChillLauncher key={session} onExit={exit} />
      default: return <div className="game-overlay__empty">Select a game from the arcade.</div>
    }
  }

  return (
    <div className="game-overlay-backdrop" role="dialog" aria-modal="true" aria-label={info?.name || 'Game'}>
      <div className="game-overlay">
        <button type="button" className="game-overlay__close" onClick={onClose} aria-label="Close game">
          ✕
        </button>
        {info && (
          <div className="game-overlay__meta">
            <span className="game-overlay__tag">{info.difficulty}</span>
            <span className="game-overlay__tag">{info.duration}</span>
          </div>
        )}
        <GameErrorBoundary
          key={`${game}-${session}`}
          gameTitle={info?.name}
          onRestart={handleRestart}
          onExit={onClose}
        >
          {renderGame()}
        </GameErrorBoundary>
      </div>
    </div>
  )
}
