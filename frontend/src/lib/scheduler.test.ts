import { describe, expect, it, vi } from 'vitest'
import {
  buildCreateScheduledTaskPayload,
  defaultSchedulerTimezone,
  formatNextRun,
  isCompletedOneShot,
  modelForProvider,
  taskBadgeStatus,
  taskStatusLabel,
} from './scheduler'

describe('scheduler helpers', () => {
  it('defaults timezone to browser timezone with fallback', () => {
    const spy = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => ({
      resolvedOptions: () => ({ timeZone: 'America/New_York' }),
    }) as Intl.DateTimeFormat)
    expect(defaultSchedulerTimezone()).toBe('America/New_York')
    spy.mockRestore()
  })

  it('falls back when browser timezone is empty', () => {
    const spy = vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => ({
      resolvedOptions: () => ({ timeZone: '' }),
    }) as Intl.DateTimeFormat)
    expect(defaultSchedulerTimezone()).toBe('America/Los_Angeles')
    spy.mockRestore()
  })

  it('buildCreateScheduledTaskPayload stores nvidia provider and model', () => {
    const payload = buildCreateScheduledTaskPayload({
      title: 'T',
      taskPrompt: 'Analyze',
      repoPath: '/repo',
      scheduleType: 'one_shot',
      scheduleExpr: '',
      timezone: 'America/Los_Angeles',
      provider: 'nvidia',
      model: 'openai/gpt-oss-20b',
      runtimeMode: 'docker',
      memoryMode: 'repo',
    })
    expect(payload.provider).toBe('nvidia')
    expect(payload.model).toBe('openai/gpt-oss-20b')
    expect(payload.runtime_mode).toBe('docker')
    expect(payload.memory_mode).toBe('repo')
  })

  it('clears model for mock provider', () => {
    expect(modelForProvider('mock', 'anything')).toBeUndefined()
    expect(buildCreateScheduledTaskPayload({
      title: 'T',
      taskPrompt: 't',
      repoPath: '/repo',
      scheduleType: 'one_shot',
      scheduleExpr: '',
      timezone: 'UTC',
      provider: 'mock',
      model: '',
      runtimeMode: 'local',
      memoryMode: 'repo',
    }).model).toBeUndefined()
  })

  it('active scheduled task before due is not running', () => {
    const task = {
      status: 'active',
      enabled: true,
      schedule_type: 'one_shot',
      next_run_at: '2099-01-01T00:00:00+00:00',
    }
    expect(taskBadgeStatus(task, [])).toBe('scheduled')
    expect(taskStatusLabel(task, [])).toBe('Scheduled')
  })

  it('shows running only when history has active run', () => {
    const task = {
      status: 'active',
      enabled: true,
      schedule_type: 'daily',
      next_run_at: '2099-01-01T00:00:00+00:00',
    }
    expect(taskBadgeStatus(task, [{ status: 'started' }])).toBe('running')
  })

  it('completed one-shot shows no stale next run', () => {
    const task = {
      status: 'completed',
      enabled: false,
      schedule_type: 'one_shot',
      next_run_at: null,
    }
    expect(formatNextRun(task)).toBe('None (one-shot completed)')
    expect(isCompletedOneShot(task)).toBe(true)
  })
})
