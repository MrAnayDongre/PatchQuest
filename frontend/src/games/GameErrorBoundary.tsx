import { Component, type ErrorInfo, type ReactNode } from 'react'
import GameButton from './GameButton'

interface Props {
  children: ReactNode
  onRestart: () => void
  onExit: () => void
  gameTitle?: string
}

interface State {
  hasError: boolean
  message: string
}

export default class GameErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message || 'Unknown error' }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[PatchQuest Game]', error, info.componentStack)
  }

  handleRestart = () => {
    this.setState({ hasError: false, message: '' })
    this.props.onRestart()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="game-shell game-shell--crashed">
          <div className="game-result game-result--crashed">
            <div className="game-result__icon" aria-hidden>⚠</div>
            <h3 className="game-result__title">Game Crashed</h3>
            <p className="game-result__message">
              {this.props.gameTitle ? `${this.props.gameTitle} hit an error.` : 'This game hit an error.'}
              {' '}The mission console is still running.
            </p>
            <p className="game-result__detail mono">{this.state.message}</p>
            <div className="game-shell__actions">
              <GameButton variant="primary" onClick={this.handleRestart}>Restart Game</GameButton>
              <GameButton variant="ghost" onClick={this.props.onExit}>Close</GameButton>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
