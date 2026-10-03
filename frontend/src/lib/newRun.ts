import type { CreateRunRequest } from '../api/types'
import { parseOverrides } from './recoveryCopy'

export interface NewRunForm {
  repoPath: string
  task: string
  provider: string
  model: string
  runtime: 'local' | 'docker'
  dryRun: boolean
  baseUrl: string
  workspaceId: string
  /** One `agent.name=value` per line. */
  overrides: string
}

export type NewRunErrors = Partial<Record<'repoPath' | 'task' | 'baseUrl' | 'overrides', string>>

export function validateNewRun(f: NewRunForm): NewRunErrors {
  const e: NewRunErrors = {}
  const repo = f.repoPath.trim()
  if (!repo) e.repoPath = 'Enter the folder of the repository to work on.'
  else if (!/^([/~]|[A-Za-z]:[\\/]|\\\\)/.test(repo)) e.repoPath = 'Use the full path, starting with / or ~ (or a drive letter on Windows).'
  if (f.task.trim().length < 5) e.task = 'Describe the task in a sentence so the agent knows what to do.'
  const url = f.baseUrl.trim()
  if (url && !/^https?:\/\/\S+$/i.test(url)) e.baseUrl = 'Use a full URL, such as http://localhost:11434/v1.'
  const o = parseOverrides(f.overrides)
  if (o.errors.length) e.overrides = o.errors[0]
  return e
}

export function buildCreateRequest(f: NewRunForm): CreateRunRequest {
  const req: CreateRunRequest = {
    repo_path: f.repoPath.trim(),
    task: f.task.trim(),
    provider: f.provider || 'mock',
    runtime_mode: f.runtime,
    dry_run: f.dryRun,
  }
  if (f.provider !== 'mock' && f.model.trim()) req.model = f.model.trim()
  if (f.baseUrl.trim()) req.base_url = f.baseUrl.trim()
  if (f.workspaceId) req.workspace_id = f.workspaceId
  const { overrides } = parseOverrides(f.overrides)
  if (Object.keys(overrides).length) req.overrides = overrides
  return req
}

export const SAMPLE_TASK = 'Explain what this repository does and list the three most important files. Do not change anything.'
