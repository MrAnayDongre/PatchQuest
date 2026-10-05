import { useEffect, useState } from 'react'
import type { Run } from '../../api/types'
import { Badge, Button, StatusIndicator } from '../../design/primitives'
import type { Connection } from '../../hooks/useRunStream'
import { friendlyStatusLine } from '../../lib/eventCopy'
import { baseName, elapsed, formatDuration, modelLabel } from '../../lib/format'
import { isActiveStatus, type RunState } from '../../lib/runState'
import { LineageBadge } from '../common'

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [active])
  return now
}

interface Props {
  run: Run
  state: RunState
  connection: Connection
  onCancel: () => void
  onResume: () => void
  onFork: () => void
  onReplay: () => void
}

export function RunHeader({ run, state, connection, onCancel, onResume, onFork, onReplay }: Props) {
  const active = isActiveStatus(run.status)
  const now = useNow(active)
  const ms = elapsed(run.created_at, run.completed_at, now)
  const reason = state.statusReason && run.status !== 'completed' ? state.statusReason : null
  const attempt = Math.max(run.attempt ?? 1, state.attempt)
  return (
    <header className="run-header">
      <div className="run-header__top">
        <h1 className="run-header__task">{run.task}</h1>
        <div className="run-header__actions">
          {run.status === 'interrupted' && <Button variant="primary" icon="play" onClick={onResume}>Resume</Button>}
          {!active && run.status !== 'interrupted' && (
            <>
              <Button icon="fork" onClick={onFork}>Fork</Button>
              <Button icon="replay" onClick={onReplay}>Replay</Button>
            </>
          )}
          {(run.status === 'running' || run.status === 'waiting_approval' || run.status === 'created' || run.status === 'queued') && (
            <Button variant="danger" icon="stop" onClick={onCancel}>Cancel run</Button>
          )}
        </div>
      </div>
      <div className="run-header__status" aria-live="polite">
        <StatusIndicator status={run.status} label={friendlyStatusLine(run)} />
        {reason && <span className="ui-muted run-header__reason">{reason}</span>}
        {(connection === 'reconnecting' || connection === 'loading') && <Badge tone="warning">{connection === 'loading' ? 'Loading…' : 'Reconnecting…'}</Badge>}
        {connection === 'error' && <Badge tone="danger">Disconnected</Badge>}
      </div>
      <ul className="run-header__meta">
        <li title={run.repo_path}>{baseName(run.repo_path)}</li>
        <li>{run.model ? `${run.provider} / ${modelLabel(run.model)}` : run.provider}</li>
        <li>{run.runtime_mode === 'docker' ? 'Docker' : 'Local'}</li>
        {run.dry_run && <li>Dry run</li>}
        {ms !== null && <li>{active ? 'Running for ' : 'Took '}{formatDuration(ms)}</li>}
        {attempt > 1 && <li>Attempt {attempt}</li>}
        <li><LineageBadge run={run} /></li>
      </ul>
    </header>
  )
}
