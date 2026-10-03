export type RunStatus =
  | 'created'
  | 'running'
  | 'waiting_approval'
  | 'cancel_requested'
  | 'interrupted'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type RunOutcome = 'applied' | 'rejected' | 'conflict' | 'no_changes' | 'no_patch' | 'read_only'
export type RunVerdict = 'passed' | 'regression' | 'unresolved' | 'no_tests'

export interface Run {
  id: string
  repo_path: string
  task: string
  status: RunStatus
  current_phase: string | null
  provider: string
  model: string | null
  runtime_mode: string
  model_profile?: string | null
  memory_mode?: string | null
  allow_network?: boolean
  dry_run?: boolean
  created_at: string
  updated_at: string
  completed_at: string | null
  workspace_id?: string
  outcome?: RunOutcome | null
  verdict?: RunVerdict | null
  failure_kind?: string | null
  attempt?: number
  parent_run_id?: string | null
  lineage_kind?: 'fork' | 'replay' | string | null
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
  event_uid?: string
  attempt?: number
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
  base_url?: string
  workspace_id?: string
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

export interface PendingApproval {
  id: string
  type: string
  command: string | null
  reason: string | null
  side_effect: string
  risk: string
  phase: string | null
  expires_at: string | null
  created_at: string
}

export type ApprovalDecisionKind = 'APPROVE_ONCE' | 'APPROVE_FOR_RUN' | 'DENY' | 'MODIFY' | 'CANCEL_RUN'

export interface ResumePlan {
  LAST_CHECKPOINT: string
  INTERRUPTED_OPERATION: string
  REPO_DRIFT: string
  SIDE_EFFECT_CERTAINTY: string
  RECOVERY_ACTION: string
  APPROVAL_REQUIRED: boolean
  CATEGORY: string
  REASONS: string[]
}

export interface CheckpointSummary {
  seq: number
  phase: string
  attempt: number
  event_cursor: number
  bytes: number
  created_at: string
  status: string
}

export interface BudgetEntry {
  kind: string
  used: number
  limit: number
  remaining?: number | null
  exhausted: boolean
}

export interface ReplayComparison {
  matched: boolean
  divergences: { aspect: string; original: unknown; replay: unknown }[]
}

export interface StateReplayReport {
  mode: 'state'
  ok: boolean
  findings: string[]
  status_trail: unknown[]
  phases: unknown
  events: unknown
  checkpoints: unknown
}

export interface LineageInfo {
  ancestry: Run[]
  children: Run[]
}

export interface EngineInfo {
  engine: string
  url: string
  available: boolean
  healthy: boolean
  models: string[]
  model_loaded: boolean
  context_limit: number | null
  capabilities: Record<string, unknown>
  latency_ms: number | null
  last_error: string | null
}

export interface ProviderHealthEntry {
  provider: string
  status: string
  last_error?: string | null
  [key: string]: unknown
}
