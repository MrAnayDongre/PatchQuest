import { memo, useEffect } from 'react'
import { getCheckpoints, getLineage } from '../../api/client'
import type { Run } from '../../api/types'
import { DataTable } from '../../design/data'
import { Badge, Button, Card, CardHeader, Skeleton } from '../../design/primitives'
import { useAsync } from '../../hooks/useAsync'
import { formatBytes, relativeTime } from '../../lib/format'
import { phaseLabel } from '../../lib/phases'
import { isActiveStatus, type RunState } from '../../lib/runState'
import { runHash } from '../../lib/router'
import { ErrorNotice, LineageBadge } from '../common'

interface Props {
  run: Run
  state: RunState
  onResume: () => void
  onFork: (checkpoint?: number) => void
  onReplay: () => void
}

export const RecoveryPanel = memo(function RecoveryPanel({ run, state, onResume, onFork, onReplay }: Props) {
  const cps = useAsync(signal => getCheckpoints(run.id, signal), [run.id])
  const count = state.checkpoints.length
  const { refresh } = cps
  useEffect(() => {
    if (count) void refresh()
  }, [count, refresh])

  const active = isActiveStatus(run.status)
  const rows = cps.data ?? []
  const hasGood = rows.some(c => c.status === 'ok')

  return (
    <div className="form-stack">
      <Card aria-labelledby="cp-title">
        <CardHeader
          id="cp-title"
          title="Checkpoints"
          subtitle="Saved after each finished step. A run can restart from any verified one."
          action={
            <>
              {run.status === 'interrupted' && <Button variant="primary" icon="play" onClick={onResume}>Resume</Button>}
              <Button icon="fork" disabled={active || !hasGood} onClick={() => onFork()} title={active ? 'Available once the run has stopped' : undefined}>Fork</Button>
              <Button icon="replay" disabled={active} onClick={onReplay} title={active ? 'Available once the run has stopped' : undefined}>Replay</Button>
            </>
          }
        />
        {cps.loading && !cps.data ? (
          <Skeleton width="100%" height={80} />
        ) : cps.error ? (
          <ErrorNotice error={cps.error} onRetry={cps.refresh} subject="the checkpoints" />
        ) : (
          <DataTable
            caption="Checkpoints"
            rows={rows}
            rowKey={c => String(c.seq)}
            empty={<p className="ui-muted">No checkpoints yet. The first one is saved when a step finishes.</p>}
            columns={[
              { key: 'seq', header: '#', render: c => <strong>{c.seq}</strong>, width: '48px' },
              { key: 'phase', header: 'After', render: c => phaseLabel(c.phase) },
              {
                key: 'status',
                header: 'Integrity',
                render: c =>
                  c.status === 'ok' ? <Badge tone="success">Verified</Badge> : <Badge tone="danger" title={c.status}>Damaged, skipped on resume</Badge>,
              },
              { key: 'when', header: 'Saved', secondary: true, render: c => relativeTime(c.created_at) },
              { key: 'size', header: 'Size', secondary: true, render: c => formatBytes(c.bytes) },
              {
                key: 'act',
                header: '',
                align: 'right',
                render: c => (c.status === 'ok' && !active ? <Button size="sm" variant="ghost" onClick={() => onFork(c.seq)} aria-label={`Fork from checkpoint ${c.seq}`}>Fork from here</Button> : null),
              },
            ]}
          />
        )}
      </Card>
    </div>
  )
})

export const LineagePanel = memo(function LineagePanel({ run, notes }: { run: Run; notes: number }) {
  const lineage = useAsync(signal => getLineage(run.id, signal), [run.id])
  const { refresh } = lineage
  useEffect(() => {
    if (notes) void refresh()
  }, [notes, refresh])
  const parents = (lineage.data?.ancestry ?? []).filter(r => r.id !== run.id)
  const children = lineage.data?.children ?? []
  return (
    <Card aria-labelledby="lineage-title">
      <CardHeader id="lineage-title" title="Related runs" subtitle="Forks and replays point back to the run they came from" />
      {parents.length === 0 && children.length === 0 ? (
        <p className="ui-muted">{lineage.loading ? 'Loading…' : 'This run has no forks or replays yet.'}</p>
      ) : (
        <ul className="lineage">
          {parents.map(p => (
            <li key={p.id}>
              <span className="ui-muted">Came from</span> <a href={runHash(p.id)} className="ui-truncate">{p.task}</a>
            </li>
          ))}
          {children.map(c => (
            <li key={c.id}>
              <LineageBadge run={c} /> <a href={runHash(c.id)} className="ui-truncate">{c.task}</a>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
})
