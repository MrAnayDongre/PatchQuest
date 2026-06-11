import { useCallback, useEffect, useRef, useState } from 'react'
import GameShell from './GameShell'
import { readGamePalette } from './gamePalette'
import { useGameKeyboardLock } from './useGameKeyboard'
import type { GameShellState } from './types'

const GRID = 20
const SIZE = 15

interface SnakeByteProps {
  onExit: () => void
}

export default function SnakeByte({ onExit }: SnakeByteProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [session, setSession] = useState(0)
  const [phase, setPhase] = useState<'ready' | 'playing' | 'paused' | 'lost'>('ready')
  const [score, setScore] = useState(0)

  const shellState: GameShellState =
    phase === 'ready' ? 'ready'
    : phase === 'playing' ? 'running'
    : phase === 'paused' ? 'paused'
    : 'lost'

  const resetGame = useCallback(() => {
    setPhase('ready')
    setScore(0)
    setSession(s => s + 1)
  }, [])

  useGameKeyboardLock(phase === 'playing')

  useEffect(() => {
    if (phase !== 'playing' && phase !== 'paused') return
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const palette = readGamePalette()

    let snake = [{ x: 10, y: 10 }]
    let dir = { x: 1, y: 0 }
    let food = { x: 15, y: 15 }
    let running = true

    const placeFood = () => {
      food = { x: Math.floor(Math.random() * GRID), y: Math.floor(Math.random() * GRID) }
    }

    const handleKey = (e: KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowUp': case 'w': if (dir.y === 0) dir = { x: 0, y: -1 }; break
        case 'ArrowDown': case 's': if (dir.y === 0) dir = { x: 0, y: 1 }; break
        case 'ArrowLeft': case 'a': if (dir.x === 0) dir = { x: -1, y: 0 }; break
        case 'ArrowRight': case 'd': if (dir.x === 0) dir = { x: 1, y: 0 }; break
      }
    }
    window.addEventListener('keydown', handleKey)

    const interval = setInterval(() => {
      if (phase === 'paused' || !running) return
      const head = { x: snake[0].x + dir.x, y: snake[0].y + dir.y }

      if (head.x < 0 || head.x >= GRID || head.y < 0 || head.y >= GRID) {
        running = false
        setPhase('lost')
        return
      }
      if (snake.some(s => s.x === head.x && s.y === head.y)) {
        running = false
        setPhase('lost')
        return
      }

      snake.unshift(head)
      if (head.x === food.x && head.y === food.y) {
        setScore(s => s + 1)
        placeFood()
      } else {
        snake.pop()
      }

      ctx.fillStyle = palette.canvasBg
      ctx.fillRect(0, 0, GRID * SIZE, GRID * SIZE)
      ctx.fillStyle = palette.player
      snake.forEach(s => ctx.fillRect(s.x * SIZE, s.y * SIZE, SIZE - 1, SIZE - 1))
      ctx.fillStyle = palette.food
      ctx.fillRect(food.x * SIZE, food.y * SIZE, SIZE - 1, SIZE - 1)
    }, 130)

    return () => {
      clearInterval(interval)
      window.removeEventListener('keydown', handleKey)
    }
  }, [phase, session])

  useEffect(() => {
    if (phase !== 'ready') return
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const palette = readGamePalette()
    ctx.fillStyle = palette.canvasBg
    ctx.fillRect(0, 0, GRID * SIZE, GRID * SIZE)
    ctx.fillStyle = palette.player
    ctx.fillRect(10 * SIZE, 10 * SIZE, SIZE - 1, SIZE - 1)
  }, [phase, session])

  return (
    <GameShell
      key={session}
      title="Snake Byte"
      description="Grow the chain, avoid the walls."
      controls="Arrow keys or WASD"
      state={shellState}
      stats={[{ label: 'Score', value: score }]}
      onStart={() => setPhase('playing')}
      onPause={() => setPhase('paused')}
      onResume={() => setPhase('playing')}
      onRestart={resetGame}
      onExit={onExit}
      resultTitle="Game Over"
      resultMessage={`Score: ${score}. Try again?`}
    >
      <canvas ref={canvasRef} width={GRID * SIZE} height={GRID * SIZE} className="game-canvas" aria-label="Snake Byte playfield" />
    </GameShell>
  )
}
