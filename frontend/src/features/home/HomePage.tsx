import { useEffect, useMemo, useState } from 'react'
import { getPendingApprovals, getRunEvents } from '../../api/client'
import type { Run } from '../../api/types'
import { useApp } from '../../app/AppContext'
import { Sparkline, StackedBar } from '../../design/data'
import { Badge, Button, Card, CardHeader, EmptyState, MetricCard, Skeleton } from '../../design/primitives'
import { Callout } from '../../design/overlay'
import { outcomeShort } from '../../lib/eventCopy'
import { formatDuration, formatPercent } from '../../lib/format'
import { runHash } from '../../lib/router'
import { applyEvents, initialRunState, pendingToInfo, type ApprovalInfo, type FailureInfo } from '../../lib/runState'
import { bucketRuns, computeMetrics } from '../../lib/runStats'
import { ErrorNotice, PageHeader, RunRow } from '../common'
import { ApprovalCard } from '../run/ApprovalsPanel'
import { FailurePanel } from '../run/FailurePanel'
import { Onboarding } from './Onboarding'

const OUTCOME_TONE: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'muted'> = {
  applied: 'success',
  read_only: 'info',
  no_changes: 'muted',
  rejected: 'warning',
  no_patch: 'warning',
  conflict: 'danger',
}

function PendingFor({ run, onChanged }: { run: Run; onChanged: () => void }) {
  const [items, setItems] = useState<ApprovalInfo[] | null>(null)
  useEffect(() => {
    let live = true
    getPendingApprovals(run.id)
      .then(list => live && setItems(list.map(pendingToInfo)))
      .catch(() => live && setItems([]))
    return () => {
      live = false
    }
  }, [run.id, run.updated_at])
  return (
    <div className="home-approval">
      <a className="home-approval__title ui-truncate" href={runHash(run.id)}>{run.task}</a>
      {items === null ? <Skeleton width="100%" height={60} /> : items.length === 0 ? <p className="ui-muted">No open requests right now.</p> : items.map(a => <ApprovalCard key={a.id} runId={run.id} approval={a} onDecided={onChanged} onStale={onChanged} />)}
    </div>
  )
}

const failureCache = new Map<string, FailureInfo | null>()

function FailureFor({ run }: { run: Run }) {
  const [failure, setFailure] = useState<FailureInfo | null | undefined>(failureCache.get(run.id))
  useEffect(() => {
    if (failureCache.has(run.id)) return
    let live = true
    getRunEvents(run.id)
      .then(ev => {
        const f = applyEvents(initialRunState(), ev).failure
        failureCache.set(run.id, f)
        if (live) setFailure(f)
      })
      .catch(() => live && setFailure(null))
    return () => {
      live = false
    }
  }, [run.id])
  return (
    <div className="home-failure">
      <a href={runHash(run.id)} className="home-failure__task ui-truncate">{run.task}</a>
      {failure ? <FailurePanel failure={failure} actions={<a className="ui-btn ui-btn--secondary ui-btn--sm" href={runHash(run.id, 'recovery')}>Open recovery</a>} /> : failure === undefined ? <Skeleton width="100%" height={48} /> : <p className="ui-muted">Failed. Open the run for details.</p>}
    </div>
  )
}

export default function HomePage() {
  const { runs, runsLoaded, runsError, refreshRuns, health, openNewRun } = useApp()
  const buckets = useMemo(() => bucketRuns(runs), [runs])
  const metrics = useMemo(() => computeMetrics(runs), [runs])
  const needs = buckets.waitingApproval.length + buckets.interrupted.length

  if (!runsLoaded) {
    return (
      <div className="page">
        <PageHeader title="Mission control" />
        <Skeleton width="100%" height={120} />
      </div>
    )
  }

  const subtitle = needs
    ? `${needs} ${needs === 1 ? 'run needs' : 'runs need'} you.`
    : buckets.active.length
      ? `${buckets.active.length} ${buckets.active.length === 1 ? 'run is' : 'runs are'} working. Nothing needs you.`
      : runs.length
        ? 'Everything is quiet. Nothing needs you.'
        : 'Hand PatchQuest a task and review the result.'

  return (
    <div className="page">
      <PageHeader title="Mission control" subtitle={subtitle} actions={<Button variant="primary" icon="plus" onClick={openNewRun}>New run</Button>} />
      {health === 'down' && (
        <Callout tone="danger" title="Can't reach the PatchQuest server" action={<Button size="sm" onClick={() => void refreshRuns()}>Try again</Button>}>
          Run data below may be out of date. Check that the backend is running.
        </Callout>
      )}
      {!!runsError && health !== 'down' && runsError.status !== 401 && <ErrorNotice error={runsError} onRetry={() => void refreshRuns()} subject="your runs" />}

      {runs.length === 0 && health !== 'down' ? (
        <>
          <Onboarding />
          <Card><EmptyState title="No runs yet" icon="runs" action={<Button onClick={openNewRun}>Start a run your own way</Button>}>Finished runs, approvals and failures will show up here.</EmptyState></Card>
        </>
      ) : (
        <>
          {needs > 0 && (
            <section aria-labelledby="needs-title" className="home-section">
              <h2 id="needs-title" className="home-section__title">Needs you <Badge tone="warning">{needs}</Badge></h2>
              <div className="home-grid">
                {buckets.waitingApproval.slice(0, 4).map(r => (
                  <Card key={r.id}><PendingFor run={r} onChanged={() => void refreshRuns()} /></Card>
                ))}
                {buckets.interrupted.slice(0, 4).map(r => (
                  <Card key={r.id}>
                    <a className="home-approval__title" href={runHash(r.id)}>{r.task}</a>
                    <p className="ui-muted">Interrupted. Progress up to the last checkpoint is saved.</p>
                    <div><a className="ui-btn ui-btn--primary" href={runHash(r.id, 'recovery')}>Review and resume</a></div>
                  </Card>
                ))}
              </div>
            </section>
          )}

          <section aria-label="Metrics" className="metrics">
            <MetricCard
              label="Success rate"
              value={formatPercent(metrics.successRate)}
              hint={metrics.successRate === null ? 'No finished runs yet' : 'Completed vs. failed, latest runs'}
              tone={metrics.successRate === null ? 'muted' : metrics.successRate >= 0.8 ? 'success' : metrics.successRate >= 0.5 ? 'warning' : 'danger'}
            />
            <MetricCard label="Median time" value={metrics.medianDurationMs === null ? '—' : formatDuration(metrics.medianDurationMs)} hint={metrics.medianDurationMs === null ? 'No timed runs yet' : `Across ${metrics.finished} finished runs`}>
              <Sparkline values={metrics.recentDurations} label="Recent run durations" format={formatDuration} />
            </MetricCard>
            <MetricCard label="Runs" value={metrics.total} hint={`${buckets.active.length + buckets.waitingApproval.length} active · ${buckets.failed.length} failed`} />
            <div className="ui-metric metrics__outcomes">
              <div className="ui-metric__label">Outcomes of completed runs</div>
              {Object.keys(metrics.outcomes).length ? (
                <StackedBar label="Outcomes" segments={Object.entries(metrics.outcomes).map(([k, v]) => ({ label: outcomeShort(k), value: v, tone: OUTCOME_TONE[k] ?? 'muted' }))} />
              ) : (
                <p className="ui-muted">None yet.</p>
              )}
            </div>
          </section>

          <div className="home-columns">
            <section aria-labelledby="active-title" className="home-section">
              <h2 id="active-title" className="home-section__title">Working now</h2>
              <Card padded={false}>
                {buckets.active.length ? buckets.active.map(r => <RunRow key={r.id} run={r} />) : <p className="ui-muted home-empty">No runs are in progress.</p>}
              </Card>
            </section>
            <section aria-labelledby="recent-title" className="home-section">
              <h2 id="recent-title" className="home-section__title">Recently completed</h2>
              <Card padded={false}>
                {buckets.completed.length ? buckets.completed.slice(0, 6).map(r => <RunRow key={r.id} run={r} />) : <p className="ui-muted home-empty">Completed runs show up here.</p>}
              </Card>
            </section>
          </div>

          {buckets.failed.length > 0 && (
            <section aria-labelledby="failed-title" className="home-section">
              <h2 id="failed-title" className="home-section__title">Failed <Badge tone="danger">{buckets.failed.length}</Badge></h2>
              <div className="home-grid">
                {buckets.failed.slice(0, 3).map(r => <Card key={r.id}><FailureFor run={r} /></Card>)}
              </div>
            </section>
          )}
          <p className="ui-muted home-note">Numbers reflect the most recent runs the server returns (up to 50).</p>
        </>
      )}
    </div>
  )
}
