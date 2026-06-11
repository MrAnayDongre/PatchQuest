export type GameShellState = 'idle' | 'ready' | 'running' | 'paused' | 'won' | 'lost' | 'crashed'

export interface GameStatItem {
  label: string
  value: string | number
}
