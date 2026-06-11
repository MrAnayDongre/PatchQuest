import { useState, useEffect } from 'react'
import PixelButton from '../components/PixelButton'
import PixelPanel from '../components/PixelPanel'
import { createRun, healthCheck, listRuns, listProviders, getProviderStatus } from '../api/client'
import type { Run, ProviderInfo, ProviderStatusInfo } from '../api/types'

interface HomePageProps {
  onRunCreated: (runId: string) => void
  onViewConsole: () => void
  onViewReports: () => void
  hasActiveRun: boolean
}

export default function HomePage({ onRunCreated, onViewConsole, onViewReports, hasActiveRun }: HomePageProps) {
  const [repoPath, setRepoPath] = useState('')
  const [task, setTask] = useState('')
  const [provider, setProvider] = useState('mock')
  const [model, setModel] = useState('')
  const [memoryMode, setMemoryMode] = useState('repo')
  const [runtimeMode, setRuntimeMode] = useState('local')
  const [loading, setLoading] = useState(false)
  const [recentRuns, setRecentRuns] = useState<Run[]>([])
  const [error, setError] = useState<string | null>(null)
  const [backendStatus, setBackendStatus] = useState<'checking' | 'online' | 'offline'>('checking')
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [providerStatuses, setProviderStatuses] = useState<ProviderStatusInfo[]>([])

  useEffect(() => {
    healthCheck()
      .then(() => {
        setBackendStatus('online')
        listProviders().then(setProviders).catch(() => {})
        getProviderStatus().then(setProviderStatuses).catch(() => {})
      })
      .catch(() => setBackendStatus('offline'))
    listRuns().then(setRecentRuns).catch(() => {})
  }, [])

  const selectedProvider = providers.find(p => p.name === provider)
  const selectedStatus = providerStatuses.find(s => s.name === provider)

  useEffect(() => {
    if (selectedProvider && !model) {
      setModel(selectedProvider.default_model)
    }
  }, [provider, selectedProvider, model])

  const handleProviderChange = (name: string) => {
    setProvider(name)
    const p = providers.find(pr => pr.name === name)
    setModel(p?.default_model ?? '')
  }

  const handleStart = async () => {
    if (!repoPath || !task) return
    setLoading(true)
    setError(null)
    try {
      const run = await createRun({
        repo_path: repoPath,
        task,
        provider,
        model: model || undefined,
        runtime_mode: runtimeMode,
        memory_mode: memoryMode,
      })
      onRunCreated(run.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create run. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  const providerKeyMissing = selectedStatus && !selectedStatus.key_set && provider !== 'mock' && provider !== 'ollama'

  return (
    <>
      <section className="hero">
        <div className="hero__badge">Local-first agentic coding harness</div>
        <h1 className="hero__title">
          Agentic Coding That <em>Connects</em> Your <em>Repo</em>, Models, Tools, and <em>Runtime</em>
        </h1>
        <p className="hero__subtitle">
          Deterministic orchestration, repo memory, safety gates, sandbox execution, and final engineering reports — not just a chatbot wrapper.
        </p>
        <div className="hero__actions">
          <PixelButton onClick={() => document.getElementById('mission-form')?.scrollIntoView({ behavior: 'smooth' })}>
            Start Mission
          </PixelButton>
          {hasActiveRun && (
            <PixelButton variant="secondary" onClick={onViewConsole}>View Console</PixelButton>
          )}
          <PixelButton variant="secondary" onClick={onViewReports}>Explore Reports</PixelButton>
        </div>
      </section>

      <hr className="glow-divider" />

      {backendStatus === 'offline' && (
        <div className="launch-alert launch-alert--error">
          <strong>Backend offline.</strong>{' '}
          Start it with: <code className="mono">cd backend && uvicorn patchquest.main:app --reload --port 8000</code>
        </div>
      )}

      {backendStatus === 'online' && (
        <div className="launch-alert launch-alert--online">
          Backend connected — {provider === 'mock' ? 'Mock provider (demo mode)' : `${selectedProvider?.display_name ?? provider}`}
          {selectedStatus?.key_set === true && provider !== 'mock' ? ' — API key configured' : ''}
        </div>
      )}

      <div className="launch-panel" id="mission-form">
        <PixelPanel glow>
          <div className="launch-form">
            <div>
              <label className="section-title" htmlFor="repo-path">Repository Path</label>
              <input
                id="repo-path"
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                placeholder="/path/to/your/repo"
                style={{ width: '100%', marginTop: '0.25rem' }}
              />
            </div>

            <div>
              <label className="section-title" htmlFor="task">Task Description</label>
              <textarea
                id="task"
                value={task}
                onChange={(e) => setTask(e.target.value)}
                placeholder="Inspect this repo and give a concise 3 bullet architecture summary. Do not modify files."
                rows={4}
                style={{ width: '100%', marginTop: '0.25rem', resize: 'vertical' }}
              />
            </div>

            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: '140px' }}>
                <label className="section-title" htmlFor="provider">LLM Provider</label>
                <select id="provider" value={provider} onChange={(e) => handleProviderChange(e.target.value)} style={{ width: '100%', marginTop: '0.25rem' }}>
                  {providers.length > 0 ? (
                    providers.map(p => {
                      const st = providerStatuses.find(s => s.name === p.name)
                      const suffix = p.name !== 'mock' && st && !st.key_set ? ' (no key)' : ''
                      return <option key={p.name} value={p.name}>{p.display_name}{suffix}</option>
                    })
                  ) : (
                    <>
                      <option value="mock">Mock (Demo)</option>
                      <option value="groq">Groq</option>
                      <option value="openai">OpenAI</option>
                    </>
                  )}
                </select>
                {providerKeyMissing && (
                  <div style={{ fontSize: '0.7rem', color: 'var(--accent-red)', marginTop: '0.25rem' }}>
                    Set <code>{selectedProvider?.api_key_env}</code> env var to use this provider.
                  </div>
                )}
              </div>
              <div style={{ flex: 1, minWidth: '140px' }}>
                <label className="section-title" htmlFor="model">Model</label>
                {selectedProvider && selectedProvider.models.length > 0 ? (
                  <select id="model" value={model} onChange={(e) => setModel(e.target.value)} style={{ width: '100%', marginTop: '0.25rem' }}>
                    {selectedProvider.models.map(m => <option key={m} value={m}>{m}</option>)}
                  </select>
                ) : (
                  <input id="model" type="text" value={model} onChange={(e) => setModel(e.target.value)} placeholder="model name" style={{ width: '100%', marginTop: '0.25rem' }} />
                )}
              </div>
            </div>

            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ flex: 1, minWidth: '140px' }}>
                <label className="section-title" htmlFor="memory-mode">Memory Mode</label>
                <select id="memory-mode" value={memoryMode} onChange={(e) => setMemoryMode(e.target.value)} style={{ width: '100%', marginTop: '0.25rem' }}>
                  <option value="off">Off</option>
                  <option value="session">Session</option>
                  <option value="repo">Repo</option>
                  <option value="user">User</option>
                </select>
              </div>
              <div style={{ flex: 1, minWidth: '140px' }}>
                <label className="section-title" htmlFor="runtime-mode">Runtime</label>
                <select id="runtime-mode" value={runtimeMode} onChange={(e) => setRuntimeMode(e.target.value)} style={{ width: '100%', marginTop: '0.25rem' }}>
                  <option value="local">Local</option>
                  <option value="docker">Docker Sandbox</option>
                </select>
              </div>
              <PixelButton onClick={handleStart} disabled={loading || !repoPath || !task || backendStatus === 'offline'}>
                {loading ? 'Launching…' : 'Start Mission'}
              </PixelButton>
            </div>

            {error && <div className="launch-alert launch-alert--error">{error}</div>}
          </div>
        </PixelPanel>

        {recentRuns.length > 0 && (
          <PixelPanel title="Recent Missions" style={{ marginTop: '1.25rem' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', marginTop: '0.35rem' }}>
              {recentRuns.slice(0, 5).map((run) => (
                <div key={run.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.78rem', padding: '0.55rem 0.65rem', background: 'var(--bg-elevated)', borderRadius: '6px', border: '1px solid var(--border-color)' }}>
                  <span style={{ color: 'var(--text-secondary)', flex: 1 }}>{run.task.slice(0, 60)}{run.task.length > 60 ? '…' : ''}</span>
                  <span className={`status-badge status-badge--${run.status === 'completed' ? 'complete' : run.status === 'failed' ? 'failed' : 'pending'}`}>{run.status}</span>
                </div>
              ))}
            </div>
          </PixelPanel>
        )}
      </div>
    </>
  )
}
