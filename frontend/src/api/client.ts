import type { CreateRunRequest, FinalReport, MemoryRecord, ProviderInfo, ProviderStatusInfo, Run, RunEvent } from './types'

const BASE_URL = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    throw new Error(`API error: ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function createRun(req: CreateRunRequest): Promise<Run> {
  return request('/runs', { method: 'POST', body: JSON.stringify(req) })
}

export async function listRuns(): Promise<Run[]> {
  return request('/runs')
}

export async function getRun(runId: string): Promise<Run> {
  return request(`/runs/${runId}`)
}

export async function getRunEvents(runId: string): Promise<RunEvent[]> {
  return request(`/runs/${runId}/events`)
}

export async function approveAction(runId: string, approvalId: string, approved: boolean, note?: string): Promise<void> {
  await request(`/runs/${runId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ approval_id: approvalId, approved, note }),
  })
}

export async function getSettings(): Promise<Record<string, unknown>> {
  const res = await request<{ settings: Record<string, unknown> }>('/settings')
  return res.settings
}

export async function updateSettings(settings: Record<string, unknown>): Promise<void> {
  await request('/settings', { method: 'POST', body: JSON.stringify({ settings }) })
}

export async function getMemory(): Promise<MemoryRecord[]> {
  return request('/memory')
}

export async function getReport(runId: string): Promise<FinalReport> {
  return request(`/reports/${runId}`)
}

export async function healthCheck(): Promise<{ status: string; version: string }> {
  return request('/health')
}

export async function listProviders(): Promise<ProviderInfo[]> {
  return request('/providers')
}

export async function getProviderStatus(): Promise<ProviderStatusInfo[]> {
  return request('/providers/status')
}

export async function testProvider(provider: string, model?: string): Promise<ProviderStatusInfo> {
  return request('/providers/test', {
    method: 'POST',
    body: JSON.stringify({ provider, model }),
  })
}
