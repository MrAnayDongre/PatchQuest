import { request } from './client'
import type { ActionMeta } from '../lib/workflow/clientCheck'
import type { Problem, WfDef } from '../lib/workflow/types'
import type { WfRun } from '../lib/workflow/run'

const enc = encodeURIComponent

export interface WorkflowSummary {
  id: string
  workspace_id: string
  name: string
  version: number
  trigger_type: string
  created_at: string
  description: string
}

export interface WorkflowRecord {
  id: string
  name: string
  version: number
  workspace_id: string
  trigger_type?: string
  created_at?: string
  definition: WfDef
}

export interface TemplateSummary {
  name: string
  description: string
  trigger: string
  variables: Record<string, { default?: unknown; required?: boolean }>
  requires: string[]
}

export interface WorkflowRunSummary {
  id: string
  workflow_id: string
  workspace_id: string
  status: string
  created_by: string | null
  created_at: string
  completed_at: string | null
  error: string | null
}

export const listWorkflows = (signal?: AbortSignal): Promise<WorkflowSummary[]> => request('/workflows', { signal })
export const getWorkflow = (id: string, signal?: AbortSignal): Promise<WorkflowRecord> => request(`/workflows/${enc(id)}`, { signal })
export const saveWorkflow = (definition: WfDef, workspaceId?: string): Promise<{ id: string; name: string; version: number; workspace_id: string }> =>
  request('/workflows', { method: 'POST', body: { definition, workspace_id: workspaceId } })
export const validateWorkflow = (definition: WfDef, signal?: AbortSignal): Promise<{ ok: boolean; problems: Problem[] }> =>
  request('/workflows/validate', { method: 'POST', body: { definition }, signal })
export const listTemplates = (signal?: AbortSignal): Promise<TemplateSummary[]> => request('/workflows/templates', { signal })
export const getTemplate = (name: string, signal?: AbortSignal): Promise<{ definition: WfDef; requires: string[] }> =>
  request(`/workflows/templates/${enc(name)}`, { signal })
export const listActions = (signal?: AbortSignal): Promise<ActionMeta[]> => request('/workflows/actions', { signal })
export const startWorkflowRun = (id: string, variables: Record<string, unknown>, payload: Record<string, unknown> = {}): Promise<{ run_id: string }> =>
  request(`/workflows/${enc(id)}/runs`, { method: 'POST', body: { variables, payload } })
export const listWorkflowRuns = (signal?: AbortSignal): Promise<WorkflowRunSummary[]> => request('/workflows/runs', { signal })
export const getWorkflowRun = (id: string, signal?: AbortSignal): Promise<WfRun> => request(`/workflows/runs/${enc(id)}`, { signal })
export const decideStep = (runId: string, node: string, decision: 'approve' | 'deny'): Promise<{ status: string }> =>
  request(`/workflows/runs/${enc(runId)}/steps/${enc(node)}/decision`, { method: 'POST', body: { decision } })
export const resolveStep = (runId: string, node: string, outcome: 'happened' | 'retry' | 'failed'): Promise<{ status: string }> =>
  request(`/workflows/runs/${enc(runId)}/steps/${enc(node)}/resolve`, { method: 'POST', body: { outcome } })
export const cancelWorkflowRun = (runId: string): Promise<{ status: string }> => request(`/workflows/runs/${enc(runId)}/cancel`, { method: 'POST' })

/** Problems from a 422 invalid_workflow response, if that is what this error is. */
export function problemsFromError(err: unknown): Problem[] | null {
  const data = (err as { data?: { problems?: Problem[] } } | null)?.data
  return data && Array.isArray(data.problems) ? data.problems.map(p => ({ ...p, source: 'server' as const })) : null
}
