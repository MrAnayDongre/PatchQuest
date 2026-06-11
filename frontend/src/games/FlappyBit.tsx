import { useCallback, useEffect, useRef, useState } from 'react'
import GameShell from './GameShell'
import { readGamePalette } from './gamePalette'
import { useGameKeyboardLock } from './useGameKeyboard'
import type { GameShellState } from './types'

interface FlappyBitProps {
  onExit: () => void
}

type Phase = 'ready' | 'countdown' | 'playing' | 'paused' | 'lost'

export default function FlappyBit({ onExit }: FlappyBitProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const phaseRef = useRef<Phase>('ready')
  const [session, setSession] = useState(0)
  const [phase, setPhase] = useState<Phase>('ready')
  const [countdown, setCountdown] = useState(0)
  const [score, setScore] = useState(0)

  phaseRef.current = phase

  const shellState: GameShellState =
    phase === 'ready' ? 'ready'
    : phase === 'countdown' ? 'running'
    : phase === 'playing' ? 'running'
    : phase === 'paused' ? 'paused'
    : 'lost'

  const resetGame = useCallback(() => {
    setPhase('ready')
    setCountdown(0)
    setScore(0)
    setSession(s => s + 1)
  }, [])

  const startCountdown = useCallback(() => {
    setCountdown(3)
    setPhase('countdown')
  }, [])

  useEffect(() => {
    if (phase !== 'countdown' || countdown <= 0) return
    const t = window.setTimeout(() => {
      if (countdown === 1) {
        setPhase('playing')
        setCountdown(0)
      } else {
        setCountdown(c => c - 1)
      }
    }, 700)
    return () => clearTimeout(t)
  }, [phase, countdown])

  useGameKeyboardLock(phase === 'playing' || phase === 'countdown' || phase === 'ready')

  // Idle frame when ready
  useEffect(() => {
    if (phase !== 'ready') return
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const palette = readGamePalette()
    ctx.fillStyle = palette.canvasBg
    ctx.fillRect(0, 0, 400, 360)
    ctx.fillStyle = palette.accent
    ctx.fillRect(30, 180, 16, 12)
  }, [phase, session])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const palette = readGamePalette()
    let bird = { y: 180, vel: 0 }
    let pipes: { x: number; gapY: number; scored: boolean }[] = []
    let frame = 0
    let currentScore = 0
    let graceFrames = 50
    let animId = 0

    const flap = () => {
      const p = phaseRef.current
      if (p === 'ready') startCountdown()
      else if (p === 'playing') bird.vel = -4.5
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === ' ' || e.key === 'ArrowUp') {
        e.preventDefault()
        flap()
      }
    }
    const onClick = () => flap()
    window.addEventListener('keydown', onKey)
    canvas.addEventListener('click', onClick)

    const loop = () => {
      const p = phaseRef.current
      if (p === 'paused' || p === 'ready' || p === 'lost') {
        animId = requestAnimationFrame(loop)
        return
      }
      if (p === 'countdown') {
        ctx.fillStyle = palette.canvasBg
        ctx.fillRect(0, 0, 400, 360)
        ctx.fillStyle = palette.accent
        ctx.fillRect(30, 180, 16, 12)
        animId = requestAnimationFrame(loop)
        return
      }

      frame++
      if (graceFrames > 0) {
        graceFrames--
        bird.vel = 0
      } else {
        bird.vel += 0.22
      }
      bird.y += bird.vel

      if (bird.y < 0 || bird.y > 340) {
        setPhase('lost')
        return
      }

      if (frame === 80) {
        pipes.push({ x: 420, gapY: 110 + Math.random() * 100, scored: false })
      } else if (pipes.length && pipes[pipes.length - 1].x < 210) {
        pipes.push({ x: 420, gapY: 90 + Math.random() * 120, scored: false })
      }

      pipes.forEach(pipe => { pipe.x -= 2.2 })
      pipes = pipes.filter(pipe => pipe.x > -40)

      const gapHalf = 42
      for (const pipe of pipes) {
        const hitX = 34 > pipe.x && 34 < pipe.x + 28
        if (hitX && (bird.y < pipe.gapY - gapHalf || bird.y > pipe.gapY + gapHalf)) {
          setPhase('lost')
          return
        }
        if (!pipe.scored && pipe.x + 28 < 34) {
          pipe.scored = true
          currentScore++
          setScore(currentScore)
        }
      }

      ctx.fillStyle = palette.canvasBg
      ctx.fillRect(0, 0, 400, 360)
      ctx.fillStyle = palette.pipe
      pipes.forEach(pipe => {
        ctx.fillRect(pipe.x, 0, 28, pipe.gapY - gapHalf)
        ctx.fillRect(pipe.x, pipe.gapY + gapHalf, 28, 360 - pipe.gapY - gapHalf)
      })
      ctx.fillStyle = palette.accent
      ctx.fillRect(30, bird.y, 16, 12)

      animId = requestAnimationFrame(loop)
    }

    animId = requestAnimationFrame(loop)
    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('keydown', onKey)
      canvas.removeEventListener('click', onClick)
    }
  }, [session, startCountdown])

  return (
    <GameShell
      key={session}
      title="Flappy Bit"
      description="Navigate the neon pipes."
      controls="Press Start, then Space / ↑ / click to flap."
      state={shellState}
      stats={[{ label: 'Score', value: score }]}
      countdown={phase === 'countdown' ? countdown : undefined}
      onStart={startCountdown}
      onPause={() => setPhase('paused')}
      onResume={() => setPhase('playing')}
      onRestart={resetGame}
      onExit={onExit}
      resultTitle="Game Over"
      resultMessage={`Score: ${score}. Press Try Again — no need to close the overlay.`}
    >
      <canvas ref={canvasRef} width={400} height={360} className="game-canvas" aria-label="Flappy Bit playfield" />
    </GameShell>
  )
}
