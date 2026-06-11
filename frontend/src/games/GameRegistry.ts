import type { GameMode } from '../api/types'

export interface GameInfo {
  id: GameMode
  name: string
  description: string
  difficulty: 'Easy' | 'Medium' | 'Hard' | 'Zen'
  duration: string
}

export const GAMES: GameInfo[] = [
  { id: 'space-raiders', name: 'Space Raiders', description: 'Shoot down the invader grid before they advance.', difficulty: 'Medium', duration: '2–5 min' },
  { id: 'snake-byte', name: 'Snake Byte', description: 'Classic snake — grow the chain, avoid walls and yourself.', difficulty: 'Easy', duration: '1–3 min' },
  { id: 'sudoku', name: 'Sudoku Grid', description: '4×4 logic puzzle to sharpen focus between phases.', difficulty: 'Easy', duration: '3–8 min' },
  { id: 'asteroid-drift', name: 'Asteroid Drift', description: 'Pilot through an escalating debris field.', difficulty: 'Medium', duration: '1–4 min' },
  { id: 'flappy-bit', name: 'Flappy Bit', description: 'Flap through neon pipes with a fair start countdown.', difficulty: 'Medium', duration: '30s–2 min' },
  { id: 'guess-number', name: 'Guess Number', description: 'Binary-search a hidden number from 1 to 100.', difficulty: 'Easy', duration: '1–2 min' },
  { id: 'xp-screensaver', name: 'XP Screensaver', description: 'Bouncing PatchQuest logo — ambient zen mode.', difficulty: 'Zen', duration: '∞' },
  { id: 'chill', name: 'Chill Zone', description: 'Curated external links for a quick mental reset.', difficulty: 'Zen', duration: '—' },
]

export function getGameInfo(id: GameMode): GameInfo | undefined {
  return GAMES.find(g => g.id === id)
}
