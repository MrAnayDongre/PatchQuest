import { useCallback, useEffect, useRef, useState } from 'react'
import GameShell from './GameShell'
import { readGamePalette } from './gamePalette'
import { useGameKeyboardLock } from './useGameKeyboard'
import type { GameShellState } from './types'

interface Asteroid {
  x: number
  y: number
  size: number
  speed: number
}

interface AsteroidDriftProps {
  onExit: () => void
}

export default function AsteroidDrift({ onExit }: AsteroidDriftProps) {
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

    let ship = { x: 200, y: 300 }
    let asteroids: Asteroid[] = []
    let frame = 0
    let running = true
    let currentScore = 0
    const keys: Record<string, boolean> = {}

    const handleKey = (e: KeyboardEvent, down: boolean) => { keys[e.key] = down }
    window.addEventListener('keydown', (e) => handleKey(e, true))
    window.addEventListener('keyup', (e) => handleKey(e, false))

    let animId = 0
    const loop = () => {
      if (phase === 'paused') {
        animId = requestAnimationFrame(loop)
        return
      }
      if (!running) return
      frame++

      if (keys['ArrowLeft'] || keys['a']) ship.x = Math.max(10, ship.x - 3)
      if (keys['ArrowRight'] || keys['d']) ship.x = Math.min(390, ship.x + 3)
      if (keys['ArrowUp'] || keys['w']) ship.y = Math.max(10, ship.y - 3)
      if (keys['ArrowDown'] || keys['s']) ship.y = Math.min(370, ship.y + 3)

      if (frame > 60 && frame % 24 === 0) {
        asteroids.push({
          x: Math.random() * 380 + 10,
          y: -20,
          size: 8 + Math.random() * 14,
          speed: 1.2 + Math.random() * 1.8 + currentScore * 0.015,
        })
      }

      asteroids.forEach(a => { a.y += a.speed })
      asteroids = asteroids.filter(a => a.y < 400)

      for (const a of asteroids) {
        if (Math.hypot(a.x - ship.x, a.y - ship.y) < a.size + 8) {
          running = false
          setPhase('lost')
          return
        }
      }

      if (frame % 12 === 0) {
        currentScore++
        setScore(currentScore)
      }

      ctx.fillStyle = palette.canvasBg
      ctx.fillRect(0, 0, 400, 380)
      ctx.fillStyle = palette.star
      for (let i = 0; i < 30; i++) {
        ctx.fillRect((i * 137 + frame * 0.5) % 400, (i * 97 + frame * 0.3) % 380, 1, 1)
      }
      ctx.fillStyle = palette.player
      ctx.beginPath()
      ctx.moveTo(ship.x, ship.y - 10)
      ctx.lineTo(ship.x - 8, ship.y + 8)
      ctx.lineTo(ship.x + 8, ship.y + 8)
      ctx.closePath()
      ctx.fill()
      asteroids.forEach(a => {
        ctx.fillStyle = palette.star
        ctx.beginPath()
        ctx.arc(a.x, a.y, a.size, 0, Math.PI * 2)
        ctx.fill()
      })

      animId = requestAnimationFrame(loop)
    }
    animId = requestAnimationFrame(loop)

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('keydown', (e) => handleKey(e, true))
      window.removeEventListener('keyup', (e) => handleKey(e, false))
    }
  }, [phase, session])

  return (
    <GameShell
      key={session}
      title="Asteroid Drift"
      description="Dodge the debris field."
      controls="Arrow keys or WASD to drift"
      state={shellState}
      stats={[{ label: 'Survived', value: score }]}
      onStart={() => setPhase('playing')}
      onPause={() => setPhase('paused')}
      onResume={() => setPhase('playing')}
      onRestart={resetGame}
      onExit={onExit}
      resultTitle="Impact!"
      resultMessage={`You survived ${score} ticks. Drift again?`}
    >
      <canvas ref={canvasRef} width={400} height={380} className="game-canvas" aria-label="Asteroid Drift playfield" />
    </GameShell>
  )
}
