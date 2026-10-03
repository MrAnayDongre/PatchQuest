import { getOperations } from '../api/platform'
import { DataTable } from '../design/data'
import { Card, CardHeader, MetricCard, Skeleton } from '../design/primitives'
import { useAsync } from '../hooks/useAsync'
import { humanize } from '../lib/format'
import { count, rate, seconds } from '../lib/metricsView'
import { ErrorNotice } from './common'

const num = (v: number | null | undefined): string => (typeof v === 'number' ? (Number.isInteger(v) ? count(v) : String(v)) : '—')

const MEMORY_ROWS: [string, string, 'n' | 'r'][] = [
  ['runs_using_memory', 'Runs that used memory', 'n'], ['share_of_runs', 'Share of runs', 'r'], ['items_considered', 'Facts considered', 'n'], ['items_selected', 'Facts selected', 'n'],
  ['selection_rate', 'Selection rate', 'r'], ['tokens_injected', 'Tokens added to prompts', 'n'], ['mean_tokens_per_run', 'Tokens per run (average)', 'n'],
  ['stale_rejected', 'Left out as out of date', 'n'], ['low_confidence_rejected', 'Left out for low confidence', 'n'], ['duplicates_avoided', 'Duplicates avoided', 'n'], ['not_relevant', 'Not relevant to the task', 'n'],
  ['runs_withheld_by_policy', 'Runs where policy withheld memory', 'n'], ['repository_profile_changes', 'Repository profile changes', 'n'], ['memories_invalidated', 'Facts set aside as stale', 'n'],
]
const POLICY_ROWS: [string, string][] = [
  ['commands_denied', 'Commands denied by policy'], ['commands_blocked_by_command_gate', 'Commands blocked by the command gate'], ['model_use_denied', 'Model calls denied by policy'], ['approvals_requested', 'Approvals requested'],
]

export function OperationsSection({ window }: { window: string }) {
  const q = useAsync(s => getOperations(window, s), [window])
  const d = q.data
  return (
    <section aria-labelledby="ops-title" className="ops">
      <h2 id="ops-title" className="ops__title">Operations</h2>
      {q.error ? (
        <ErrorNotice error={q.error} onRetry={q.refresh} subject="the operations figures" />
      ) : !d ? (
        <Skeleton width="100%" height={160} />
      ) : (
        <>
          <section aria-label="Context quality" className="metrics metrics--wide">
            <MetricCard label="Files given to the model" value={num(d.context.mean_files_selected)} hint={`Average per run${d.context.runs_with_context ? `, ${d.context.runs_with_context} runs` : ''}`} />
            <MetricCard label="Context size" value={num(d.context.mean_context_tokens)} hint="Average tokens per run" />
            <MetricCard label="Context precision" value={rate(d.context.context_precision)} hint="Share of selected files the patch touched" />
            <MetricCard label="Worker recoveries" value={num(d.workers.recoveries)} hint="Runs picked up again after a crash" />
          </section>
          <div className="ops__grid">
            <Card>
              <CardHeader title="Memory" subtitle="What remembered facts cost and contributed" />
              <DataTable caption="Memory use" rows={MEMORY_ROWS} rowKey={r => r[0]} columns={[
                { key: 'l', header: 'Measure', render: r => r[1] },
                { key: 'v', header: 'Value', align: 'right', render: r => (r[2] === 'r' ? rate(d.memory[r[0]]) : num(d.memory[r[0]])) },
              ]} />
            </Card>
            <Card>
              <CardHeader title="Policy" subtitle="What your rules stopped or asked about" />
              <DataTable caption="Policy effects" rows={POLICY_ROWS} rowKey={r => r[0]} columns={[
                { key: 'l', header: 'Measure', render: r => r[1] },
                { key: 'v', header: 'Value', align: 'right', render: r => num(d.policy[r[0]]) },
              ]} />
            </Card>
          </div>
          <Card>
            <CardHeader title="Workflow actions" subtitle="Steps that ran in this window" />
            <DataTable caption="Workflow actions" rows={Object.entries(d.workflows.actions)} rowKey={([k]) => k}
              empty={<p className="ui-muted">No workflow steps ran in this window.</p>}
              columns={[
                { key: 'a', header: 'Action', render: ([k]) => <strong className="ui-wrap">{k}</strong> },
                { key: 's', header: 'Steps', align: 'right', render: ([, a]) => num(a.steps) },
                { key: 'f', header: 'Failed', align: 'right', render: ([, a]) => num(a.failed) },
                { key: 'r', header: 'Failure rate', align: 'right', secondary: true, render: ([, a]) => rate(a.failure_rate) },
                { key: 'l', header: 'Typical time', align: 'right', secondary: true, render: ([, a]) => seconds(a.latency_s) },
              ]} />
          </Card>
          <Card>
            <CardHeader title="Inbound events" subtitle="What integrations delivered, by result" />
            <DataTable caption="Inbound events" rows={Object.entries(d.workflows.inbound)} rowKey={([k]) => k}
              empty={<p className="ui-muted">No events arrived from integrations in this window.</p>}
              columns={[
                { key: 's', header: 'Source', render: ([k]) => <strong>{humanize(k)}</strong> },
                { key: 'r', header: 'Results', render: ([, st]) => Object.entries(st).map(([k, n]) => `${humanize(k)} ${n}`).join(' · ') },
              ]} />
          </Card>
        </>
      )}
    </section>
  )
}
