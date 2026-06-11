import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import GameErrorBoundary from './GameErrorBoundary'

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
