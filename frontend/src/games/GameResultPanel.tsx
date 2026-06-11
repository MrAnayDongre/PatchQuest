import type { ReactNode } from 'react'
import GameButton from './GameButton'
import type { GameShellState } from './types'

interface GameResultPanelProps {
  variant: 'won' | 'lost' | 'ready' | 'paused' | 'countdown'
  title: string
  message?: string
  detail?: ReactNode
  onPrimary?: () => void
  primaryLabel?: string
  onSecondary?: () => void
  secondaryLabel?: string
  countdown?: number
}

export default function GameResultPanel({
  variant,
  title,
  message,
  detail,
  onPrimary,
  primaryLabel = 'Play Again',
  onSecondary,
  secondaryLabel,
  countdown,
}: GameResultPanelProps) {
  return (
    <div className={`game-result game-result--${variant}`} role="status" aria-live="polite">
      {countdown != null && variant === 'countdown' && (
        <div className="game-result__countdown" aria-hidden>{countdown}</div>
      )}
      <h3 className="game-result__title">{title}</h3>
      {message && <p className="game-result__message">{message}</p>}
      {detail && <div className="game-result__detail">{detail}</div>}
      <div className="game-shell__actions">
        {onPrimary && (
          <GameButton variant="primary" onClick={onPrimary}>{primaryLabel}</GameButton>
        )}
        {onSecondary && secondaryLabel && (
          <GameButton variant="ghost" onClick={onSecondary}>{secondaryLabel}</GameButton>
        )}
      </div>
    </div>
  )
}

export function shellStateLabel(state: GameShellState): string {
  const labels: Record<GameShellState, string> = {
    idle: 'Idle',
    ready: 'Ready',
    running: 'Running',
    paused: 'Paused',
    won: 'Victory',
    lost: 'Game Over',
    crashed: 'Error',
  }
  return labels[state]
}
