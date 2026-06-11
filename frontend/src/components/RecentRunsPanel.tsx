import { useEffect, useState } from 'react'
import PixelPanel from './PixelPanel'
import StatusBadge from './StatusBadge'
import RunActionButtons from './RunActionButtons'
import { listRuns } from '../api/client'
import { formatRunWhen } from '../lib/runId'
import type { Run } from '../api/types'

interface RecentRunsPanelProps {
  onOpenConsole: (runId: string) => void
  onViewReport: (runId: string) => void
  limit?: number
  title?: string
}

function statusBadge(run: Run): 'complete' | 'failed' | 'running' | 'pending' | 'blocked' {
  if (run.status === 'completed') return 'complete'
  if (run.status === 'failed') return 'failed'
  if (run.status === 'created') return 'running'
  return 'pending'
}

export default function RecentRunsPanel({
  onOpenConsole,
  onViewReport,
  limit = 5,
  title = 'Recent Missions',
}: RecentRunsPanelProps) {
  const [runs, setRuns] = useState<Run[]>([])

  useEffect(() => {
    listRuns()
      .then(all => {
        const sorted = [...all].sort((a, b) => {
          const at = a.completed_at || a.updated_at || a.created_at
          const bt = b.completed_at || b.updated_at || b.created_at
          return bt.localeCompare(at)
        })
        setRuns(sorted.slice(0, limit))
      })
      .catch(() => setRuns([]))
  }, [limit])

  if (runs.length === 0) return null

  return (
    <PixelPanel title={title} glow>
      <div className="run-list">
        {runs.map(run => (
          <div key={run.id} className="run-list__item">
            <div className="run-list__main">
              <div className="run-list__task">{run.task}</div>
              <div className="run-list__meta">
                <StatusBadge status={statusBadge(run)} />
                <span>{run.provider}{run.model ? ` / ${run.model}` : ''}</span>
                <span>{run.runtime_mode}</span>
                {run.completed_at && <span>Completed {formatRunWhen(run.completed_at)}</span>}
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
    </PixelPanel>
  )
}
