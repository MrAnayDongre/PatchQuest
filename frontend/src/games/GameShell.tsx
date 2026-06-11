import type { ReactNode } from 'react'
import GameButton from './GameButton'
import GameControlsHint from './GameControlsHint'
import GameResultPanel from './GameResultPanel'
import GameStat from './GameStat'
import type { GameShellState, GameStatItem } from './types'
import { shellStateLabel } from './GameResultPanel'

interface GameShellProps {
  title: string
  description?: string
  controls?: string
  state: GameShellState
  stats?: GameStatItem[]
  sessionKey?: number
  onStart?: () => void
  onPause?: () => void
  onResume?: () => void
  onRestart: () => void
  onExit: () => void
  resultTitle?: string
  resultMessage?: string
  resultDetail?: ReactNode
  countdown?: number
  /** Ambient games skip start/pause controls */
  ambient?: boolean
  children: ReactNode
}

export default function GameShell({
  title,
  description,
  controls,
  state,
  stats = [],
  onStart,
  onPause,
  onResume,
  onRestart,
  onExit,
  resultTitle,
  resultMessage,
  resultDetail,
  countdown,
  ambient = false,
  children,
}: GameShellProps) {
  const showReady = state === 'ready' || state === 'idle'
  const showPaused = state === 'paused'
  const showWon = state === 'won'
  const showLost = state === 'lost'
  const showCountdown = countdown != null && countdown > 0

  return (
    <div className={`game-shell game-shell--${state}`}>
      <header className="game-shell__header">
        <div>
          <h2 className="game-shell__title">{title}</h2>
          {description && <p className="game-shell__desc">{description}</p>}
        </div>
        <span className={`game-shell__badge game-shell__badge--${state}`}>
          {shellStateLabel(state)}
        </span>
      </header>

      {stats.length > 0 && (
        <div className="game-shell__stats">
          {stats.map(s => <GameStat key={s.label} label={s.label} value={s.value} />)}
        </div>
      )}

      <div className="game-shell__stage">
        {children}

        {showCountdown && (
          <GameResultPanel
            variant="countdown"
            title="Get Ready"
            countdown={countdown}
            primaryLabel="Go"
          />
        )}

        {showReady && !ambient && !showCountdown && (
          <GameResultPanel
            variant="ready"
            title="Ready?"
            message={controls || 'Press Start when you are ready.'}
            onPrimary={onStart}
            primaryLabel="Start"
            onSecondary={onExit}
            secondaryLabel="Exit"
          />
        )}

        {showPaused && (
          <GameResultPanel
            variant="paused"
            title="Paused"
            message="Take a breath. The mission keeps running."
            onPrimary={onResume}
            primaryLabel="Resume"
            onSecondary={onRestart}
            secondaryLabel="Restart"
          />
        )}

        {showWon && (
          <GameResultPanel
            variant="won"
            title={resultTitle || 'Victory!'}
            message={resultMessage}
            detail={resultDetail}
            onPrimary={onRestart}
            primaryLabel="Play Again"
            onSecondary={onExit}
            secondaryLabel="Exit"
          />
        )}

        {showLost && (
          <GameResultPanel
            variant="lost"
            title={resultTitle || 'Game Over'}
            message={resultMessage}
            detail={resultDetail}
            onPrimary={onRestart}
            primaryLabel="Try Again"
            onSecondary={onExit}
            secondaryLabel="Exit"
          />
        )}
      </div>

      {controls && state === 'running' && <GameControlsHint text={controls} />}

      <footer className="game-shell__footer">
        {!ambient && state === 'running' && onPause && (
          <GameButton variant="ghost" size="sm" onClick={onPause}>Pause</GameButton>
        )}
        {!ambient && state !== 'idle' && state !== 'ready' && (
          <GameButton variant="ghost" size="sm" onClick={onRestart}>Restart</GameButton>
        )}
        <GameButton variant="ghost" size="sm" onClick={onExit}>Exit</GameButton>
      </footer>
    </div>
  )
}
