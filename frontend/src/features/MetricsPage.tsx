import { useState } from 'react'
import { getMetrics, type MetricsBlock } from '../api/client'
import { DataTable } from '../design/data'
import { Button, Card, CardHeader, EmptyState, Field, MetricCard, Select, Skeleton } from '../design/primitives'
import { useAsync } from '../hooks/useAsync'
import { humanize } from '../lib/format'
import { count, GROUPINGS, millis, money, pair, rate, seconds, WINDOWS } from '../lib/metricsView'
import { OperationsSection } from './OperationsSection'
import { ErrorNotice, PageHeader } from './common'

function Block({ m }: { m: MetricsBlock }) {
  const failures = Object.entries(m.failure_distribution ?? {})
  return (
    <>
      <section aria-label="Key rates" className="metrics metrics--wide">
        <MetricCard label="Task success" value={rate(m.task_success_rate)} hint={`${m.finished} finished of ${m.runs} runs`} />
        <MetricCard label="Checks passed" value={rate(m.validation_pass_rate)} hint="Runs whose validation passed" />
        <MetricCard label="Right the first time" value={rate(m.first_pass_success_rate)} hint="Succeeded with no repair or retry" />
        <MetricCard label="Resumes that finished" value={rate(m.resume_success_rate)} hint={m.runs_resumed ? `${m.runs_resumed} resumed runs` : 'No resumed runs'} />
      </section>
      <section aria-label="Time and cost" className="metrics metrics--wide">
        <MetricCard label="Time to finish" value={seconds(m.time_to_completion_s?.p50)} hint={pair(m.time_to_completion_s, seconds)} />
        <MetricCard label="Wait for approval" value={seconds(m.approval_latency_s?.p50)} hint={m.approval_latency_s?.count ? `${pair(m.approval_latency_s, seconds)} · ${m.approval_latency_s.count} requests` : 'No approvals yet'} />
        <MetricCard label="Tokens" value={count(m.tokens?.total)} hint={m.tokens?.total ? `${count(m.tokens.prompt)} in · ${count(m.tokens.completion)} out${m.tokens.per_success ? ` · ${count(m.tokens.per_success)} per success` : ''}` : 'No model calls'} />
        <MetricCard label="Cost" value={money(m.cost?.total, m.cost?.currency)} hint={m.cost?.total === null || m.cost?.total === undefined ? 'Shown only when every model used has a price in the config' : undefined} />
      </section>
      <Card>
        <CardHeader title="Why runs failed" subtitle="Failed runs by cause" />
        {failures.length ? (
          <DataTable caption="Failures by cause" rows={failures} rowKey={([k]) => k} columns={[
            { key: 'k', header: 'Cause', render: ([k]) => humanize(k) },
            { key: 'n', header: 'Runs', align: 'right', render: ([, n]) => n },
          ]} />
        ) : (
          <p className="ui-muted">No failures in this window.</p>
        )}
      </Card>
    </>
  )
}

export default function MetricsPage() {
  const [window, setWindow] = useState('7d')
  const [group, setGroup] = useState('')
  const q = useAsync(s => getMetrics(window, group || null, s), [window, group])
  const data = q.data
  const groups = data?.groups ? Object.entries(data.groups) : []
  return (
    <div className="page">
      <PageHeader title="Metrics" subtitle="Measured from this server's runs. Anything without data shows —." actions={<Button icon="refresh" loading={q.loading} onClick={q.refresh}>Refresh</Button>} />
      <div className="filters filters--two">
        <Field label="Time window">{p => <Select {...p} value={window} onChange={e => setWindow(e.target.value)}>{WINDOWS.map(w => <option key={w.id} value={w.id}>{w.label}</option>)}</Select>}</Field>
        <Field label="Compare">{p => <Select {...p} value={group} onChange={e => setGroup(e.target.value)}>{GROUPINGS.map(g => <option key={g.id} value={g.id}>{g.label}</option>)}</Select>}</Field>
      </div>
      {q.error ? (
        <ErrorNotice error={q.error} onRetry={q.refresh} subject="the metrics" />
      ) : !data ? (
        <Skeleton width="100%" height={220} />
      ) : data.totals.runs === 0 ? (
        <Card><EmptyState icon="clock" title="No runs in this window">Try a longer window, or start a run.</EmptyState></Card>
      ) : (
        <>
          <Block m={data.totals} />
          {groups.length > 0 && (
            <Card>
              <CardHeader title="Comparison" />
              <DataTable caption="Comparison" rows={groups} rowKey={([k]) => k} columns={[
                { key: 'g', header: GROUPINGS.find(g => g.id === group)?.label.replace('By ', '') ?? 'Group', render: ([k]) => <strong className="ui-wrap">{k}</strong> },
                { key: 'r', header: 'Runs', align: 'right', render: ([, b]) => b.runs },
                { key: 's', header: 'Success', align: 'right', render: ([, b]) => rate(b.task_success_rate) },
                { key: 'v', header: 'Checks passed', align: 'right', secondary: true, render: ([, b]) => rate(b.validation_pass_rate) },
                { key: 't', header: 'Typical time', align: 'right', secondary: true, render: ([, b]) => seconds(b.time_to_completion_s?.p50) },
                { key: 'k', header: 'Tokens', align: 'right', secondary: true, render: ([, b]) => count(b.tokens?.total) },
              ]} />
            </Card>
          )}
          <Card>
            <CardHeader title="Model response time" subtitle="Per model call in this window" />
            <DataTable caption="Model latency" rows={data.models} rowKey={m => `${m.provider}/${m.model}`}
              empty={<p className="ui-muted">No model calls in this window.</p>}
              columns={[
                { key: 'm', header: 'Model', render: m => <span className="ui-wrap"><strong>{m.model || 'unknown'}</strong> <span className="ui-muted">{m.provider}</span></span> },
                { key: 'c', header: 'Calls', align: 'right', render: m => m.calls },
                { key: 'e', header: 'Errors', align: 'right', render: m => rate(m.error_rate) },
                { key: 'p50', header: 'Typical', align: 'right', secondary: true, render: m => millis(m.latency_ms?.p50) },
                { key: 'p95', header: 'Slowest 5%', align: 'right', secondary: true, render: m => millis(m.latency_ms?.p95) },
              ]} />
          </Card>
        </>
      )}
      <OperationsSection window={window} />
    </div>
  )
}
