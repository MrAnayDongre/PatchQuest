import { useCallback, useEffect, useRef, useState } from 'react'
import GameShell from './GameShell'
import { readGamePalette } from './gamePalette'
import type { GameShellState } from './types'

interface XpScreensaverProps {
  onExit: () => void
}

export default function XpScreensaver({ onExit }: XpScreensaverProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [session, setSession] = useState(0)
  const [running, setRunning] = useState(true)

  const state: GameShellState = running ? 'running' : 'paused'

  const resetGame = useCallback(() => {
    setRunning(true)
    setSession(s => s + 1)
  }, [])

  useEffect(() => {
    if (!running) return
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const palette = readGamePalette()

    let x = 100, y = 100, dx = 2, dy = 1.5
    const w = 120, h = 40
    let hue = 200
    const trails: { x: number; y: number; hue: number; alpha: number }[] = []

    let animId = 0
    const loop = () => {
      ctx.fillStyle = `${palette.canvasBg}0d`
      ctx.fillRect(0, 0, 500, 360)
      x += dx
      y += dy
      if (x <= 0 || x + w >= 500) { dx = -dx; hue = (hue + 30) % 360 }
      if (y <= 0 || y + h >= 360) { dy = -dy; hue = (hue + 30) % 360 }

      trails.push({ x, y, hue, alpha: 1 })
      if (trails.length > 50) trails.shift()
      trails.forEach(t => {
        t.alpha -= 0.02
        if (t.alpha > 0) {
          ctx.fillStyle = `hsla(${t.hue}, 70%, 55%, ${t.alpha * 0.25})`
          ctx.fillRect(t.x, t.y, w, h)
        }
      })

      ctx.fillStyle = palette.player
      ctx.fillRect(x, y, w, h)
      ctx.fillStyle = palette.canvasBg
      ctx.font = 'bold 14px var(--font-mono)'
      ctx.fillText('PATCHQUEST', x + 12, y + 25)

      animId = requestAnimationFrame(loop)
    }
    animId = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(animId)
  }, [running, session])

  return (
    <GameShell
      key={session}
      title="XP Screensaver"
      description="Bouncing logo zen while agents work."
      state={state}
      ambient
      onPause={() => setRunning(false)}
      onResume={() => setRunning(true)}
      onRestart={resetGame}
      onExit={onExit}
    >
      <canvas ref={canvasRef} width={500} height={360} className="game-canvas" aria-label="Screensaver animation" />
    </GameShell>
  )
}
