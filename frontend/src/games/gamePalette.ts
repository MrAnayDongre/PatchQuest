export interface GamePalette {
  canvasBg: string
  player: string
  enemy: string
  accent: string
  danger: string
  success: string
  pipe: string
  star: string
  bullet: string
  food: string
}

const FALLBACK: GamePalette = {
  canvasBg: '#0a0a14',
  player: '#5eead4',
  enemy: '#a78bfa',
  accent: '#fbbf24',
  danger: '#f87171',
  success: '#34d399',
  pipe: '#34d399',
  star: '#3d3d58',
  bullet: '#34d399',
  food: '#f87171',
}

export function readGamePalette(): GamePalette {
  if (typeof document === 'undefined') return FALLBACK
  const s = getComputedStyle(document.documentElement)
  const v = (name: keyof GamePalette, fb: string) => {
    const map: Record<keyof GamePalette, string> = {
      canvasBg: '--game-canvas-bg',
      player: '--game-player',
      enemy: '--game-enemy',
      accent: '--game-accent',
      danger: '--game-danger',
      success: '--game-success',
      pipe: '--game-pipe',
      star: '--game-star',
      bullet: '--game-success',
      food: '--game-danger',
    }
    return s.getPropertyValue(map[name]).trim() || fb
  }
  return {
    canvasBg: v('canvasBg', FALLBACK.canvasBg),
    player: v('player', FALLBACK.player),
    enemy: v('enemy', FALLBACK.enemy),
    accent: v('accent', FALLBACK.accent),
    danger: v('danger', FALLBACK.danger),
    success: v('success', FALLBACK.success),
    pipe: v('pipe', FALLBACK.pipe),
    star: v('star', FALLBACK.star),
    bullet: v('bullet', FALLBACK.bullet),
    food: v('food', FALLBACK.food),
  }
}
