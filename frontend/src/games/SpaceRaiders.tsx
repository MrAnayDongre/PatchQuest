import { useCallback, useEffect, useRef, useState } from 'react'
import GameShell from './GameShell'
import { readGamePalette } from './gamePalette'
import { useGameKeyboardLock } from './useGameKeyboard'
import type { GameShellState } from './types'

interface Entity {
  x: number
  y: number
  alive: boolean
}

interface SpaceRaidersProps {
  onExit: () => void
}

export default function SpaceRaiders({ onExit }: SpaceRaidersProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [session, setSession] = useState(0)
  const [phase, setPhase] = useState<'ready' | 'playing' | 'paused' | 'won' | 'lost'>('ready')
  const [score, setScore] = useState(0)

  const shellState: GameShellState =
    phase === 'ready' ? 'ready'
    : phase === 'playing' ? 'running'
    : phase === 'paused' ? 'paused'
    : phase === 'won' ? 'won'
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

    const state = {
      player: { x: 200, y: 350 },
      bullets: [] as { x: number; y: number }[],
      enemies: [] as Entity[],
      keys: {} as Record<string, boolean>,
      score: 0,
      frame: 0,
    }

    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 8; col++) {
        state.enemies.push({ x: 30 + col * 50, y: 30 + row * 40, alive: true })
      }
    }

    const handleKey = (e: KeyboardEvent, down: boolean) => {
      state.keys[e.key] = down
      if (down && e.key === ' ') {
        e.preventDefault()
        state.bullets.push({ x: state.player.x + 10, y: state.player.y })
      }
    }
    window.addEventListener('keydown', (e) => handleKey(e, true))
    window.addEventListener('keyup', (e) => handleKey(e, false))

    let animId = 0
    let running = true

    const loop = () => {
      if (phase === 'paused') {
        animId = requestAnimationFrame(loop)
        return
      }
      if (!running) return
      state.frame++

      if (state.keys['ArrowLeft'] || state.keys['a']) state.player.x = Math.max(0, state.player.x - 4)
      if (state.keys['ArrowRight'] || state.keys['d']) state.player.x = Math.min(380, state.player.x + 4)

      state.bullets = state.bullets.filter(b => b.y > 0)
      state.bullets.forEach(b => { b.y -= 6 })

      if (state.frame % 30 === 0) {
        state.enemies.forEach(e => { if (e.alive) e.x += 5 * (state.frame % 60 < 30 ? 1 : -1) })
      }

      state.bullets.forEach(b => {
        state.enemies.forEach(e => {
          if (e.alive && Math.abs(b.x - e.x) < 15 && Math.abs(b.y - e.y) < 15) {
            e.alive = false
            b.y = -10
            state.score++
            setScore(state.score)
          }
        })
      })

      ctx.fillStyle = palette.canvasBg
      ctx.fillRect(0, 0, 420, 400)
      ctx.fillStyle = palette.player
      ctx.fillRect(state.player.x, state.player.y, 20, 12)
      ctx.fillRect(state.player.x + 8, state.player.y - 5, 4, 5)
      ctx.fillStyle = palette.bullet
      state.bullets.forEach(b => ctx.fillRect(b.x, b.y, 2, 8))
      ctx.fillStyle = palette.enemy
      state.enemies.forEach(e => {
        if (e.alive) {
          ctx.fillRect(e.x, e.y, 20, 16)
          ctx.fillRect(e.x - 3, e.y + 4, 4, 8)
          ctx.fillRect(e.x + 19, e.y + 4, 4, 8)
        }
      })

      if (state.enemies.every(e => !e.alive)) {
        running = false
        setPhase('won')
        return
      }

      animId = requestAnimationFrame(loop)
    }
    animId = requestAnimationFrame(loop)

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('keydown', (e) => handleKey(e, true))
      window.removeEventListener('keyup', (e) => handleKey(e, false))
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
    ctx.fillRect(0, 0, 420, 400)
    ctx.fillStyle = palette.player
    ctx.fillRect(200, 350, 20, 12)
  }, [phase, session])

  return (
    <GameShell
      key={session}
      title="Space Raiders"
      description="Clear the invader grid."
      controls="← → or A/D to move · Space to fire"
      state={shellState}
      stats={[{ label: 'Score', value: score }]}
      onStart={() => setPhase('playing')}
      onPause={() => setPhase('paused')}
      onResume={() => setPhase('playing')}
      onRestart={resetGame}
      onExit={onExit}
      resultTitle={phase === 'won' ? 'Sector Clear!' : 'Game Over'}
      resultMessage={phase === 'won' ? `All invaders destroyed. Score: ${score}.` : undefined}
    >
      <canvas ref={canvasRef} width={420} height={400} className="game-canvas" aria-label="Space Raiders playfield" />
    </GameShell>
  )
}
