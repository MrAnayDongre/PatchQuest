import { useState, useEffect } from 'react'
import PixelPanel from '../components/PixelPanel'
import StatusBadge from '../components/StatusBadge'
import RunActionButtons from '../components/RunActionButtons'
import { listRuns, getMemory } from '../api/client'
import { formatRunWhen } from '../lib/runId'
import type { MemoryRecord, Run } from '../api/types'

interface RepoIntelStatus {
  tree_sitter_available: boolean
  languages: Record<string, boolean>
  supported_extensions: string[]
}

interface Props {
  onOpenConsole: (runId: string) => void
  onViewReport: (runId: string) => void
}

function runStatusBadge(status: string): 'complete' | 'failed' | 'running' | 'pending' {
  if (status === 'completed') return 'complete'
  if (status === 'failed') return 'failed'
  if (status === 'created') return 'running'
  return 'pending'
}

export default function MemoryPage({ onOpenConsole, onViewReport }: Props) {
  const [records, setRecords] = useState<MemoryRecord[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [filter, setFilter] = useState('')
  const [tsStatus, setTsStatus] = useState<RepoIntelStatus | null>(null)

  useEffect(() => {
    getMemory().then(setRecords).catch(() => {})
    listRuns()
      .then(all => {
        const sorted = [...all].sort((a, b) => {
          const at = a.completed_at || a.updated_at || a.created_at
          const bt = b.completed_at || b.updated_at || b.created_at
          return bt.localeCompare(at)
        })
        setRuns(sorted)
      })
      .catch(() => {})
    fetch('/api/repo/intelligence-status').then(r => r.ok ? r.json() : null).then(setTsStatus).catch(() => {})
  }, [])

  const filtered = records.filter(r =>
    r.key.toLowerCase().includes(filter.toLowerCase()) ||
    r.record_type.toLowerCase().includes(filter.toLowerCase())
  )

  return (
    <div className="launch-panel" style={{ maxWidth: '960px' }}>
      <h1 className="page-title">Archive</h1>
      <p className="page-subtitle">All missions and repo memory — open any run report or console from here.</p>

      <PixelPanel title="All Runs" glow>
        {runs.length === 0 ? (
          <p className="empty-state__body" style={{ textAlign: 'center', padding: '1.5rem' }}>No runs yet.</p>
        ) : (
          <div className="run-list">
            {runs.map(run => (
              <div key={run.id} className="run-list__item">
                <div className="run-list__main">
                  <div className="run-list__task">{run.task}</div>
                  <div className="run-list__meta">
                    <StatusBadge status={runStatusBadge(run.status)} />
                    <span>{run.provider}{run.model ? ` / ${run.model}` : ''}</span>
                    <span>{run.runtime_mode}</span>
                    <span>{formatRunWhen(run.completed_at || run.updated_at)}</span>
                  </div>
                </div>
                <RunActionButtons
                  runId={run.id}
                  onOpenConsole={onOpenConsole}
                  onViewReport={onViewReport}
                  compact
                  showRunId
                />
              </div>
            ))}
          </div>
        )}
      </PixelPanel>

      {tsStatus && (
        <PixelPanel title="Repo Intelligence" style={{ marginTop: '1rem' }}>
          <div style={{ fontSize: '0.75rem', marginTop: '0.5rem' }}>
            Tree-sitter:{' '}
            <span style={{ color: tsStatus.tree_sitter_available ? 'var(--accent-green)' : 'var(--accent-red)' }}>
              {tsStatus.tree_sitter_available ? 'available' : 'regex fallback'}
            </span>
          </div>
        </PixelPanel>
      )}

      <PixelPanel title="Memory Records" style={{ marginTop: '1rem' }}>
        <input
          type="text"
          placeholder="Filter records…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ width: '100%', marginBottom: '1rem' }}
        />
        {filtered.length === 0 && (
          <p className="empty-state__body" style={{ textAlign: 'center', padding: '1rem' }}>
            No memory records. Run a mission to populate the archive.
          </p>
        )}
        {filtered.map((r) => (
          <div key={r.id} style={{ padding: '0.5rem', borderBottom: '1px solid var(--border-color)', fontSize: '0.8rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>{r.key}</span>
              <StatusBadge status={r.status === 'fresh' ? 'complete' : r.status === 'stale' ? 'blocked' : 'failed'} />
            </div>
            <div style={{ color: 'var(--text-dim)', marginTop: '0.2rem' }}>
              {r.record_type} · {r.scope} · {r.source_path || '—'}
            </div>
          </div>
        ))}
      </PixelPanel>
    </div>
  )
}
