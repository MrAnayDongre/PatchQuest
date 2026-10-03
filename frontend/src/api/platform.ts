import { request } from './client'

const enc = encodeURIComponent
const q = (params: Record<string, string | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) p.set(k, v)
  return p.toString()
}

// ---- who am I
export interface WorkspaceInfo {
  id: string
  name: string
  role: string
  permissions: string[]
}
export interface Me {
  id?: string
  name?: string
  workspaces: WorkspaceInfo[]
}
export const getMe = (signal?: AbortSignal): Promise<Me> => request('/me', { signal })

// ---- demo environment (404 anywhere else)
export const getDemoInfo = (signal?: AbortSignal): Promise<{ demo: boolean }> => request('/demo', { signal })

// ---- integrations
export type ConfigType = 'string' | 'string_list' | 'string_map'
export interface KindAction {
  name: string
  side_effect: string
  requires_approval: boolean
}
export interface KindConfigField {
  name: string
  type: ConfigType
  required: boolean
  description: string
}
export interface IntegrationKind {
  kind: string
  title: string
  inbound: boolean
  triggers: string[]
  actions: KindAction[]
  config: KindConfigField[]
  secrets: { name: string; required: boolean }[]
  notes: string
  test: string
}
export interface KindsResponse {
  kinds: IntegrationKind[]
  stored_secrets_available: boolean
}
/** What the API says about a saved secret. The secret itself is never returned. */
export interface SecretRef {
  env?: string
  stored?: boolean
}
export interface Integration {
  id: string
  kind: string
  name: string
  config: Record<string, unknown>
  status: 'connected' | 'error' | 'disabled' | string
  secrets: Record<string, SecretRef>
  created_at: string
  updated_at: string
  last_checked_at: string | null
  last_error: string | null
  webhook_path: string
}
export interface IntegrationEvent {
  source: string
  external_id: string
  received_at: string
  status: string
  run_id: string | null
}
export type SecretInput = { env: string } | { value: string }
export interface CreateIntegrationBody {
  workspace_id: string
  kind: string
  name: string
  config: Record<string, unknown>
  secrets: Record<string, SecretInput>
}

export const getIntegrationKinds = (signal?: AbortSignal): Promise<KindsResponse> => request('/integrations/kinds', { signal })
export const listIntegrations = (ws: string, signal?: AbortSignal): Promise<Integration[]> => request(`/integrations?${q({ workspace_id: ws })}`, { signal })
export const createIntegration = (body: CreateIntegrationBody): Promise<Integration> => request('/integrations', { method: 'POST', body })
export const setIntegrationEnabled = (ws: string, id: string, enabled: boolean): Promise<Integration> =>
  request(`/integrations/${enc(id)}`, { method: 'PUT', body: { workspace_id: ws, enabled } })
export const deleteIntegration = (ws: string, id: string): Promise<{ deleted: boolean }> =>
  request(`/integrations/${enc(id)}?${q({ workspace_id: ws })}`, { method: 'DELETE' })
export const testIntegration = (ws: string, id: string): Promise<{ ok: boolean; detail: unknown; error: string | null }> =>
  request(`/integrations/${enc(id)}/test?${q({ workspace_id: ws })}`, { method: 'POST' })
export const getIntegrationEvents = (ws: string, id: string, signal?: AbortSignal): Promise<IntegrationEvent[]> =>
  request(`/integrations/${enc(id)}/events?${q({ workspace_id: ws })}`, { signal })

// ---- repositories, profile, memory, preferences
export interface Repository {
  id: string
  path: string
  name: string | null
  [key: string]: unknown
}
export interface ProfileField {
  value: unknown
  source: string
  reason: string
  confidence: number | null
  last_verified: string | null
  memory_id: string | null
  evidence: Record<string, unknown>
}
export interface RepoProfile {
  path: string
  profile: Record<string, ProfileField>
}
export interface MemoryItem {
  id: string
  kind: string
  scope: string
  scope_id: string | null
  key: string
  value: unknown
  source: string
  trusted: boolean
  reason: string
  confidence: number
  status: string
  updated_at: string
}
export interface PreferenceOrigin {
  scope: string
  scope_id: string | null
  value: unknown
  source: string
  memory_id: string
}
export interface Preference {
  key?: string
  value: unknown
  decided_by: PreferenceOrigin | string
  overridden: PreferenceOrigin[]
  description: string
  applied_by: string
}

export const listRepositories = (ws: string, signal?: AbortSignal): Promise<Repository[]> => request(`/repositories?${q({ workspace_id: ws })}`, { signal })
export const registerRepository = (ws: string, path: string, name?: string): Promise<Repository> =>
  request('/repositories', { method: 'POST', body: { workspace_id: ws, path, name: name || undefined } })
export const getRepoProfile = (ws: string, path: string, signal?: AbortSignal): Promise<RepoProfile> =>
  request(`/repositories/profile?${q({ workspace_id: ws, path })}`, { signal })
export const setProfileField = (ws: string, path: string, field: string, value: unknown): Promise<unknown> =>
  request(`/repositories/profile/${enc(field)}`, { method: 'PUT', body: { workspace_id: ws, path, value } })
export const listMemories = (ws: string, repo: string, signal?: AbortSignal): Promise<MemoryItem[]> =>
  request(`/memories?${q({ workspace_id: ws, repo })}`, { signal })
export const addMemory = (ws: string, repo: string, key: string, value: unknown): Promise<MemoryItem> =>
  request('/memories', { method: 'POST', body: { workspace_id: ws, scope: 'repository', ref: repo, kind: 'repository', key, value } })
export const forgetMemory = (ws: string, id: string): Promise<{ forgotten: boolean }> =>
  request(`/memories/${enc(id)}?${q({ workspace_id: ws })}`, { method: 'DELETE' })
export const listPreferences = (ws: string, repo: string, signal?: AbortSignal): Promise<Record<string, Preference>> =>
  request(`/preferences?${q({ workspace_id: ws, repo })}`, { signal })
export const setPreference = (ws: string, scope: string, ref: string | undefined, key: string, value: unknown): Promise<unknown> =>
  request('/preferences', { method: 'PUT', body: { workspace_id: ws, scope, ref, key, value } })
export const clearPreference = (ws: string, scope: string, ref: string | undefined, key: string): Promise<{ cleared: boolean }> =>
  request(`/preferences?${q({ workspace_id: ws, key, scope, ref })}`, { method: 'DELETE' })

// ---- why / operations
export interface Explanation {
  id: number
  type: string
  phase: string | null
  message: string
  created_at: string
  payload: Record<string, any> // shape depends on `type`; lib/platform.ts reads it defensively
}
export const getExplanations = (runId: string, signal?: AbortSignal): Promise<Explanation[]> => request(`/runs/${enc(runId)}/explanations`, { signal })

export interface ActionStats {
  steps: number | null
  failed: number | null
  failure_rate: number | null
  latency_s: number | null
}
export interface OperationsResponse {
  runs?: number
  context: { runs_with_context: number | null; mean_files_selected: number | null; mean_context_tokens: number | null; context_precision: number | null }
  memory: Record<string, number | null>
  policy: Record<string, number | null>
  workers: { recoveries: number | null }
  workflows: { actions: Record<string, ActionStats>; inbound: Record<string, Record<string, number>> }
}
export const getOperations = (window: string, signal?: AbortSignal): Promise<OperationsResponse> => request(`/metrics/operations?${q({ window })}`, { signal })
