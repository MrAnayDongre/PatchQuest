export interface ScheduledTaskLike {
  status: string
  enabled: boolean
  schedule_type: string
  next_run_at: string | null
}

export interface HistoryEntryLike {
  status: string
}

export function defaultSchedulerTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    return tz && tz.trim() ? tz : 'America/Los_Angeles'
  } catch {
    return 'America/Los_Angeles'
  }
}

export function taskBadgeStatus(
  task: ScheduledTaskLike,
  historyEntries: HistoryEntryLike[] = [],
): 'running' | 'complete' | 'blocked' | 'pending' | 'failed' | 'scheduled' {
  if (task.status === 'completed') return 'complete'
  if (task.status === 'failed') return 'failed'
  if (task.status === 'paused' || !task.enabled) return 'blocked'

  const hasActiveRun = historyEntries.some(h => h.status === 'started' || h.status === 'running')
  if (hasActiveRun || task.status === 'running') return 'running'

  if (task.status === 'active' && task.enabled) return 'scheduled'
  return 'pending'
}

export function taskStatusLabel(task: ScheduledTaskLike, historyEntries: HistoryEntryLike[] = []): string {
  const badge = taskBadgeStatus(task, historyEntries)
  if (badge === 'scheduled') return 'Scheduled'
  if (badge === 'running') return 'Running'
  if (badge === 'complete') return 'Completed'
  if (badge === 'blocked') return task.status === 'paused' ? 'Paused' : 'Inactive'
  if (badge === 'failed') return 'Failed'
  return task.status
}

export function formatNextRun(task: ScheduledTaskLike): string {
  if (task.status === 'completed') {
    return task.schedule_type === 'one_shot' ? 'None (one-shot completed)' : 'None'
  }
  if (!task.enabled || task.status === 'paused') {
    return task.next_run_at ? `Paused — was ${task.next_run_at}` : 'Paused'
  }
  return task.next_run_at ?? '—'
}

export function isCompletedOneShot(task: ScheduledTaskLike): boolean {
  return task.schedule_type === 'one_shot' && task.status === 'completed'
}

export function modelForProvider(provider: string, model: string): string | undefined {
  if (provider === 'mock') return undefined
  return model.trim() || undefined
}

export interface CreateScheduledTaskPayload {
  title: string
  task_prompt: string
  repo_path: string
  schedule_type: string
  schedule_expr: string | null
  timezone: string
  provider: string
  model?: string
  runtime_mode: string
  memory_mode: string
}

export function buildCreateScheduledTaskPayload(input: {
  title: string
  taskPrompt: string
  repoPath: string
  scheduleType: string
  scheduleExpr: string
  timezone: string
  provider: string
  model: string
  runtimeMode: string
  memoryMode: string
}): CreateScheduledTaskPayload {
  const tz = input.timezone.trim() || defaultSchedulerTimezone()
  return {
    title: input.title,
    task_prompt: input.taskPrompt,
    repo_path: input.repoPath,
    schedule_type: input.scheduleType,
    schedule_expr: input.scheduleExpr || null,
    timezone: tz,
    provider: input.provider,
    model: modelForProvider(input.provider, input.model),
    runtime_mode: input.runtimeMode,
    memory_mode: input.memoryMode,
  }
}
