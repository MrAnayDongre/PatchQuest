import { currentToken, notifyUnauthorized } from './auth'
import { ApiError, normalizeError } from './errors'
import type {
  ApprovalDecisionKind,
  BudgetEntry,
  CheckpointSummary,
  CreateRunRequest,
  EngineInfo,
  FinalReport,
  LineageInfo,
  MemoryRecord,
  PendingApproval,
  ProviderHealthEntry,
  ProviderInfo,
  ProviderStatusInfo,
  ReplayComparison,
  ResumePlan,
  Run,
  RunEvent,
  StateReplayReport,
} from './types'

export { ApiError } from './errors'

const BASE_URL = '/api'

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const token = currentToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let res: Response
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'Network error', 'network')
  }

  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      // non-JSON error body
    }
    const error = normalizeError(res.status, body, res.statusText)
    if (res.status === 401) notifyUnauthorized()
    throw error
  }
  if (res.status === 204) return undefined as T
  try {
    return (await res.json()) as T
  } catch {
    throw new ApiError(res.status, 'The server sent a response we could not read', 'bad_response')
  }
}

const enc = encodeURIComponent

// ---- runs
export const createRun = (req: CreateRunRequest): Promise<Run> => request('/runs', { method: 'POST', body: req })
export const RUNS_PAGE = 200
/** Newest first. `before` is the created_at of the oldest run already seen. */
export function listRuns(signal?: AbortSignal, opts: { limit?: number; before?: string } = {}): Promise<Run[]> {
  const params = new URLSearchParams()
  params.set('limit', String(opts.limit ?? RUNS_PAGE))
  if (opts.before) params.set('before', opts.before)
  return request(`/runs?${params.toString()}`, { signal })
}
export const getRun = (runId: string, signal?: AbortSignal): Promise<Run> => request(`/runs/${enc(runId)}`, { signal })
export const getRunEvents = (runId: string, afterId = 0, signal?: AbortSignal): Promise<RunEvent[]> =>
  request(`/runs/${enc(runId)}/events${afterId ? `?after_id=${afterId}` : ''}`, { signal })

export function runStreamUrl(runId: string, afterId: number): string {
  const params = new URLSearchParams({ after_id: String(afterId) })
  const token = currentToken()
  if (token) params.set('token', token)
  return `${BASE_URL}/runs/${enc(runId)}/stream?${params.toString()}`
}

// ---- approvals
export const getPendingApprovals = (runId: string, signal?: AbortSignal): Promise<PendingApproval[]> =>
  request(`/runs/${enc(runId)}/approvals`, { signal })

export interface DecisionInput {
  decision: ApprovalDecisionKind
  note?: string
  modified_command?: string
}
export const decideApproval = (runId: string, approvalId: string, input: DecisionInput): Promise<{ status: string; decision: string }> =>
  request(`/runs/${enc(runId)}/approvals/${enc(approvalId)}`, { method: 'POST', body: input })

// ---- control
export const cancelRun = (runId: string): Promise<{ status: string }> => request(`/runs/${enc(runId)}/cancel`, { method: 'POST' })
export const getResumePlan = (runId: string): Promise<ResumePlan> => request(`/runs/${enc(runId)}/resume-plan`)
export const resumeRun = (runId: string, body: { accept_drift: boolean; rollback: boolean }): Promise<ResumePlan> =>
  request(`/runs/${enc(runId)}/resume`, { method: 'POST', body })

export interface ForkInput {
  from_checkpoint?: number
  provider?: string
  model?: string
  base_url?: string
  overrides?: Record<string, number | string | boolean>
  accept_drift?: boolean
}
export const forkRun = (runId: string, body: ForkInput): Promise<Run> => request(`/runs/${enc(runId)}/fork`, { method: 'POST', body })

export type ReplayResult = StateReplayReport | { mode: 'model' | 'live'; run: Run }
export const replayRun = (runId: string, mode: 'state' | 'model' | 'live'): Promise<ReplayResult> =>
  request(`/runs/${enc(runId)}/replay`, { method: 'POST', body: { mode } })
export const getReplayComparison = (runId: string, replayId: string): Promise<ReplayComparison> =>
  request(`/runs/${enc(runId)}/replay/${enc(replayId)}/comparison`)
export const getLineage = (runId: string, signal?: AbortSignal): Promise<LineageInfo> => request(`/runs/${enc(runId)}/lineage`, { signal })
export const getCheckpoints = (runId: string, signal?: AbortSignal): Promise<CheckpointSummary[]> =>
  request(`/runs/${enc(runId)}/checkpoints`, { signal })
export const getBudget = (runId: string, signal?: AbortSignal): Promise<BudgetEntry[]> => request(`/runs/${enc(runId)}/budget`, { signal })

// ---- metrics
export interface Percentiles {
  p50: number | null
  p95: number | null
  count?: number
}
export interface MetricsBlock {
  runs: number
  finished: number
  by_status: Record<string, number>
  by_outcome: Record<string, number>
  task_success_rate: number | null
  validation_pass_rate: number | null
  first_pass_success_rate: number | null
  resume_success_rate: number | null
  runs_resumed: number
  failure_distribution: Record<string, number>
  approval_latency_s: Percentiles
  time_to_completion_s: Percentiles
  tokens: { total: number; prompt: number; completion: number; per_success: number | null }
  cost: { total: number | null; per_success: number | null; currency: string | null }
  human_interventions: number
  provider_failovers: number
  [key: string]: unknown
}
export interface ModelLatency {
  provider: string
  model: string
  calls: number
  error_rate: number | null
  latency_ms: Percentiles
}
export interface MetricsResponse {
  window: { since: string | null; until: string | null }
  totals: MetricsBlock
  models: ModelLatency[]
  groups?: Record<string, MetricsBlock>
}
export function getMetrics(window: string, groupBy: string | null, signal?: AbortSignal): Promise<MetricsResponse> {
  const params = new URLSearchParams({ window })
  if (groupBy) params.set('group_by', groupBy)
  return request(`/metrics?${params.toString()}`, { signal })
}

// ---- reports
export const getReport = (runId: string, signal?: AbortSignal): Promise<FinalReport> => request(`/reports/${enc(runId)}`, { signal })

// ---- providers
export const healthCheck = (): Promise<{ status: string; version: string }> => request('/health')
export const listProviders = (signal?: AbortSignal): Promise<ProviderInfo[]> => request('/providers', { signal })
export const getProviderStatus = (signal?: AbortSignal): Promise<ProviderStatusInfo[]> => request('/providers/status', { signal })
export const getEngines = (signal?: AbortSignal): Promise<EngineInfo[]> => request('/providers/engines', { signal })
export const getProviderHealth = (signal?: AbortSignal): Promise<ProviderHealthEntry[]> => request('/providers/health', { signal })
export const testProvider = (provider: string, model?: string): Promise<ProviderStatusInfo> =>
  request('/providers/test', { method: 'POST', body: { provider, model } })

// ---- settings & memory (existing pages)
export async function getSettings(): Promise<Record<string, unknown>> {
  const res = await request<{ settings: Record<string, unknown> }>('/settings')
  return res.settings
}
export async function updateSettings(settings: Record<string, unknown>): Promise<void> {
  await request('/settings', { method: 'POST', body: { settings } })
}
export const getMemory = (): Promise<MemoryRecord[]> => request('/memory')

/** Legacy yes/no approval used by the old safety queue page. */
export async function approveAction(runId: string, approvalId: string, approved: boolean, note?: string): Promise<void> {
  await request(`/runs/${enc(runId)}/approve`, { method: 'POST', body: { approval_id: approvalId, approved, note } })
}
