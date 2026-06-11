export interface Run {
  id: string
  repo_path: string
  task: string
  status: string
  current_phase: string | null
  provider: string
  model: string | null
  runtime_mode: string
  model_profile: string | null
  memory_mode: string | null
  allow_network: boolean
  dry_run: boolean
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface RunEvent {
  id: number
  run_id: string
  type: string
  phase: string | null
  status: string | null
  message: string | null
  payload: Record<string, unknown> | null
  created_at: string
}

export interface PhaseStatus {
  phase: string
  status: string
  started_at?: string
  completed_at?: string
}

export interface ApprovalRequest {
  id: string
  run_id: string
  type: string
  command: string | null
  reason: string | null
  status: string
  created_at: string
}

export interface FinalReport {
  run_id: string
  report_md: string | null
  diff_patch: string | null
  commands_log: string | null
  created_at: string | null
}

export interface MemoryRecord {
  id: number
  scope: string
  record_type: string
  key: string
  value: unknown
  source_path: string | null
  status: string
  created_at: string
  updated_at: string
}

export interface ProviderProfile {
  provider: string
  model: string
  base_url?: string
  api_key_env?: string
  max_tokens: number
  temperature: number
}

export type GameMode = 'space-raiders' | 'snake-byte' | 'sudoku' | 'asteroid-drift' | 'guess-number' | 'flappy-bit' | 'xp-screensaver' | 'chill' | null

export interface CreateRunRequest {
  repo_path: string
  task: string
  provider?: string
  model?: string
  runtime_mode?: string
  model_profile?: string
  memory_mode?: string
  interface_mode?: string
  allow_network?: boolean
  dry_run?: boolean
}

export interface ProviderInfo {
  name: string
  display_name: string
  api_key_env: string | null
  base_url: string | null
  default_model: string
  models: string[]
}

export interface ProviderStatusInfo {
  name: string
  available: boolean
  key_set: boolean
  error: string | null
}
