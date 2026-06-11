import { useState, useEffect, useCallback } from 'react'
import PixelPanel from '../components/PixelPanel'
import PixelButton from '../components/PixelButton'
import StatusBadge from '../components/StatusBadge'
import RunActionButtons from '../components/RunActionButtons'
import MissionProviderFields from '../components/MissionProviderFields'
import { listProviders, getProviderStatus } from '../api/client'
import type { ProviderInfo, ProviderStatusInfo } from '../api/types'
import { formatRunWhen, shortRunId } from '../lib/runId'
import {
  buildCreateScheduledTaskPayload,
  defaultSchedulerTimezone,
  formatNextRun,
  isCompletedOneShot,
  taskBadgeStatus,
  taskStatusLabel,
} from '../lib/scheduler'

interface ScheduledTask {
  id: number
  title: string
  task_prompt: string
  repo_path: string
  schedule_type: string
  schedule_expr: string | null
  timezone: string
  next_run_at: string | null
  last_run_at: string | null
  enabled: boolean
  status: string
  provider: string
  model: string | null
  runtime_mode: string
  memory_mode: string
  created_at: string
}

interface HistoryEntry {
  id: number
  run_id: string | null
  started_at: string
  finished_at: string | null
  status: string
  message: string | null
  provider?: string | null
  model?: string | null
  runtime_mode?: string | null
}

interface Props {
  onOpenConsole: (runId: string) => void
  onViewReport: (runId: string) => void
}

export default function SchedulerPage({ onOpenConsole, onViewReport }: Props) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [history, setHistory] = useState<Record<number, HistoryEntry[]>>({})
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runNowMsg, setRunNowMsg] = useState<string | null>(null)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [providerStatuses, setProviderStatuses] = useState<ProviderStatusInfo[]>([])

  const [title, setTitle] = useState('')
  const [taskPrompt, setTaskPrompt] = useState('')
  const [repoPath, setRepoPath] = useState('')
  const [scheduleType, setScheduleType] = useState('one_shot')
  const [scheduleExpr, setScheduleExpr] = useState('')
  const [tz, setTz] = useState(defaultSchedulerTimezone)
  const [provider, setProvider] = useState('mock')
  const [model, setModel] = useState('')
  const [runtimeMode, setRuntimeMode] = useState('local')
  const [memoryMode, setMemoryMode] = useState('repo')

  useEffect(() => {
    listProviders().then(setProviders).catch(() => {})
    getProviderStatus().then(setProviderStatuses).catch(() => {})
  }, [])

  const selectedProvider = providers.find(p => p.name === provider)

  useEffect(() => {
    if (provider === 'mock') {
      setModel('')
      return
    }
    if (selectedProvider && !model) {
      setModel(selectedProvider.default_model)
    }
  }, [provider, selectedProvider, model])

  const handleProviderChange = (name: string) => {
    setProvider(name)
    const p = providers.find(pr => pr.name === name)
    setModel(name === 'mock' ? '' : (p?.default_model ?? ''))
  }

  const loadHistory = useCallback(async (taskId: number) => {
    const res = await fetch(`/api/scheduler/tasks/${taskId}/history`)
    if (!res.ok) return
    const data: HistoryEntry[] = await res.json()
    setHistory(prev => ({ ...prev, [taskId]: data }))
  }, [])

  const fetchTasks = useCallback(async () => {
    const res = await fetch('/api/scheduler/tasks')
    if (!res.ok) return
    const data: ScheduledTask[] = await res.json()
    setTasks(data)
    await Promise.all(data.map(t => loadHistory(t.id)))
  }, [loadHistory])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  const handleCreate = async () => {
    setError(null)
    const payload = buildCreateScheduledTaskPayload({
      title,
      taskPrompt,
      repoPath,
      scheduleType,
      scheduleExpr,
      timezone: tz,
      provider,
      model,
      runtimeMode,
      memoryMode,
    })

    const selectedStatus = providerStatuses.find(s => s.name === provider)
    if (provider !== 'mock' && provider !== 'ollama' && selectedStatus && !selectedStatus.key_set) {
      const proceed = window.confirm(
        `Provider "${selectedProvider?.display_name ?? provider}" has no API key configured. ` +
        'The scheduled run will fail when triggered. Schedule anyway?'
      )
      if (!proceed) return
    }

    try {
      const res = await fetch('/api/scheduler/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text)
      }
      setShowForm(false)
      setTitle('')
      setTaskPrompt('')
      setRepoPath('')
      setProvider('mock')
      setModel('')
      fetchTasks()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create task')
    }
  }

  const handlePause = async (taskId: number) => {
    await fetch(`/api/scheduler/tasks/${taskId}/pause`, { method: 'POST' })
    fetchTasks()
  }

  const handleResume = async (taskId: number) => {
    await fetch(`/api/scheduler/tasks/${taskId}/resume`, { method: 'POST' })
    fetchTasks()
  }

  const handleRunNow = async (task: ScheduledTask) => {
    setRunNowMsg(null)
    const res = await fetch(`/api/scheduler/tasks/${task.id}/run-now`, { method: 'POST' })
    if (!res.ok) {
      setRunNowMsg('Failed to start run')
      return
    }
    const data = await res.json()
    setRunNowMsg(`Started new immediate run${data.run_id ? `: ${shortRunId(data.run_id as string)}` : ''}`)
    fetchTasks()
    if (data.run_id) onOpenConsole(data.run_id as string)
  }

  const handleDelete = async (taskId: number) => {
    await fetch(`/api/scheduler/tasks/${taskId}`, { method: 'DELETE' })
    fetchTasks()
  }

  return (
    <div className="launch-panel" style={{ maxWidth: '960px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <div>
          <h1 className="page-title">Quest Queue</h1>
          <p className="page-subtitle">Schedule agentic runs and jump to their reports when complete.</p>
        </div>
        <PixelButton onClick={() => setShowForm(!showForm)}>
          {showForm ? 'Cancel' : '+ Schedule Task'}
        </PixelButton>
      </div>

      {runNowMsg && (
        <div className="launch-alert launch-alert--online">{runNowMsg}</div>
      )}

      {showForm && (
        <PixelPanel glow style={{ marginBottom: '1.5rem' }}>
          <div className="launch-form">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
              <div>
                <label className="section-title">Title</label>
                <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Daily test run" style={{ width: '100%' }} />
              </div>
              <div>
                <label className="section-title">Repo Path</label>
                <input value={repoPath} onChange={e => setRepoPath(e.target.value)} placeholder="/path/to/repo" style={{ width: '100%' }} />
              </div>
            </div>
            <div>
              <label className="section-title">Task Prompt</label>
              <textarea value={taskPrompt} onChange={e => setTaskPrompt(e.target.value)} rows={2} placeholder="Run tests and fix failures" style={{ width: '100%' }} />
            </div>

            <MissionProviderFields
              provider={provider}
              onProviderChange={handleProviderChange}
              model={model}
              onModelChange={setModel}
              memoryMode={memoryMode}
              onMemoryModeChange={setMemoryMode}
              runtimeMode={runtimeMode}
              onRuntimeModeChange={setRuntimeMode}
              providers={providers}
              providerStatuses={providerStatuses}
            />

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.75rem' }}>
              <div>
                <label className="section-title">Schedule</label>
                <select value={scheduleType} onChange={e => setScheduleType(e.target.value)} style={{ width: '100%' }}>
                  <option value="one_shot">One-shot</option>
                  <option value="interval">Interval</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="cron">Cron</option>
                </select>
              </div>
              <div>
                <label className="section-title">Expression</label>
                <input value={scheduleExpr} onChange={e => setScheduleExpr(e.target.value)} style={{ width: '100%' }} />
              </div>
              <div>
                <label className="section-title">Timezone</label>
                <input value={tz} onChange={e => setTz(e.target.value)} placeholder="America/Los_Angeles" style={{ width: '100%' }} />
              </div>
            </div>
            {error && <div className="launch-alert launch-alert--error">{error}</div>}
            <PixelButton onClick={handleCreate} disabled={!title || !taskPrompt || !repoPath || !tz.trim()}>Create Task</PixelButton>
          </div>
        </PixelPanel>
      )}

      {tasks.length === 0 && !showForm && (
        <PixelPanel>
          <div className="empty-state" style={{ marginTop: 0 }}>
            <p className="empty-state__body">No scheduled tasks. Click "+ Schedule Task" to create one.</p>
          </div>
        </PixelPanel>
      )}

      {tasks.map(task => {
        const taskHistory = history[task.id] ?? []
        const badge = taskBadgeStatus(task, taskHistory)
        const completedOneShot = isCompletedOneShot(task)

        return (
          <PixelPanel key={task.id} style={{ marginBottom: '0.75rem' }}>
            <div className="queue-card__header">
              <div>
                <div className="queue-card__title">{task.title}</div>
                <div className="queue-card__sub">
                  {task.schedule_type} · {task.provider}{task.model ? ` / ${task.model}` : ''} · {task.runtime_mode} · {task.memory_mode} · {task.repo_path}
                </div>
              </div>
              <StatusBadge status={badge} />
            </div>

            <div className="queue-card__timing">
              <span><strong>Status:</strong> {taskStatusLabel(task, taskHistory)}</span>
              <span><strong>Last:</strong> {formatRunWhen(task.last_run_at)}</span>
              <span><strong>Next:</strong> {formatNextRun(task)}</span>
            </div>

            <div className="queue-card__actions">
              {task.enabled && task.status === 'active' && (
                <PixelButton variant="ghost" onClick={() => handlePause(task.id)}>Pause</PixelButton>
              )}
              {!task.enabled && task.status === 'paused' && !completedOneShot && (
                <PixelButton variant="ghost" onClick={() => handleResume(task.id)}>Resume</PixelButton>
              )}
              {completedOneShot ? (
                <PixelButton variant="secondary" onClick={() => handleRunNow(task)} title="Creates a new immediate run from this task config">
                  Re-run Now
                </PixelButton>
              ) : (
                <PixelButton variant="secondary" onClick={() => handleRunNow(task)} title="Creates a new immediate run from this scheduled task config">
                  Run Now
                </PixelButton>
              )}
              <PixelButton variant="ghost" onClick={() => loadHistory(task.id)}>Refresh History</PixelButton>
              <PixelButton variant="danger" onClick={() => handleDelete(task.id)}>Delete</PixelButton>
            </div>

            {taskHistory.length > 0 && (
              <div className="queue-history">
                <div className="section-title">Run History</div>
                {[...taskHistory].reverse().map(h => (
                  <div key={h.id} className="queue-history__row">
                    <div className="queue-history__meta">
                      <span>{formatRunWhen(h.started_at)}</span>
                      <span className={`queue-history__status queue-history__status--${h.status}`}>{h.status}</span>
                      {h.run_id && <span className="mono" title={h.run_id}>Run {shortRunId(h.run_id)}</span>}
                      {(h.provider || h.runtime_mode) && (
                        <span className="queue-history__msg">
                          {h.provider ?? 'mock'}{h.model ? ` / ${h.model}` : ''} · {h.runtime_mode ?? 'local'}
                        </span>
                      )}
                      {h.message && <span className="queue-history__msg">{h.message}</span>}
                    </div>
                    {h.run_id && (
                      <RunActionButtons
                        runId={h.run_id}
                        onOpenConsole={onOpenConsole}
                        onViewReport={onViewReport}
                        compact
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </PixelPanel>
        )
      })}
    </div>
  )
}
