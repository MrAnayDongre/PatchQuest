import { getEngines, getProviderHealth } from '../api/client'
import { DataTable } from '../design/data'
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from '../design/primitives'
import { useAsync } from '../hooks/useAsync'
import { formatCount, humanize } from '../lib/format'
import type { Tone } from '../lib/eventCopy'
import { ErrorNotice, PageHeader } from './common'

const HEALTH_TONE: Record<string, Tone> = { healthy: 'success', ok: 'success', degraded: 'warning', down: 'danger', unknown: 'muted' }

export default function EnginesPage() {
  const engines = useAsync(s => getEngines(s), [])
  const health = useAsync(s => getProviderHealth(s), [])
  const refresh = () => {
    void engines.refresh()
    void health.refresh()
  }
  const busy = engines.loading || health.loading
  return (
    <div className="page">
      <PageHeader title="Engines and providers" subtitle="Local model engines on this machine, and how each provider has behaved recently." actions={<Button icon="refresh" loading={busy} onClick={refresh}>Refresh</Button>} />
      <Card>
        <CardHeader title="Local engines" subtitle="Checked just now at their default addresses" />
        {engines.loading && !engines.data ? (
          <Skeleton width="100%" height={100} />
        ) : engines.error ? (
          <ErrorNotice error={engines.error} onRetry={engines.refresh} subject="the engines" />
        ) : (
          <DataTable
            caption="Local engines"
            rows={engines.data ?? []}
            rowKey={e => e.engine}
            empty={<EmptyState title="No local engines configured" icon="engine">Local engines such as Ollama or LM Studio appear here when they are set up on the server.</EmptyState>}
            columns={[
              { key: 'engine', header: 'Engine', render: e => <div><strong>{e.engine}</strong><div className="ui-muted ui-mono">{e.url}</div></div> },
              { key: 'status', header: 'Status', render: e => <Badge tone={e.healthy ? 'success' : e.available ? 'warning' : 'danger'}>{e.healthy ? 'Healthy' : e.available ? 'Degraded' : 'Not running'}</Badge> },
              { key: 'models', header: 'Models', render: e => (e.model_loaded ? `${e.models.length} available` : e.available ? 'None loaded' : '—'), secondary: true },
              { key: 'ctx', header: 'Context', render: e => (e.context_limit ? `${formatCount(e.context_limit)} tokens` : '—'), secondary: true },
              { key: 'lat', header: 'Latency', render: e => (e.latency_ms != null ? `${Math.round(e.latency_ms)} ms` : '—'), secondary: true },
              {
                key: 'cap',
                header: 'Capabilities',
                secondary: true,
                render: e => {
                  const on = Object.entries(e.capabilities ?? {}).filter(([, v]) => v === true).map(([k]) => humanize(k))
                  return on.length ? <span className="chips-inline">{on.map(c => <Badge key={c}>{c}</Badge>)}</span> : '—'
                },
              },
              { key: 'err', header: 'Last problem', render: e => (e.last_error ? <span className="ui-text--danger ui-wrap">{e.last_error}</span> : <span className="ui-muted">None</span>) },
            ]}
          />
        )}
      </Card>
      <Card>
        <CardHeader title="Provider health" subtitle="Based on calls this server has made since it started" />
        {health.loading && !health.data ? (
          <Skeleton width="100%" height={60} />
        ) : health.error ? (
          <ErrorNotice error={health.error} onRetry={health.refresh} subject="provider health" />
        ) : (
          <DataTable
            caption="Provider health"
            rows={health.data ?? []}
            rowKey={h => String(h.provider) + String(h.model ?? '')}
            empty={<p className="ui-muted">No provider calls yet. Health appears after a run uses a provider.</p>}
            columns={[
              { key: 'p', header: 'Provider', render: h => <strong>{String(h.provider)}{h.model ? <span className="ui-muted"> / {String(h.model)}</span> : null}</strong> },
              { key: 's', header: 'Status', render: h => <Badge tone={HEALTH_TONE[h.status] ?? 'neutral'}>{humanize(h.status)}</Badge> },
              { key: 'l', header: 'Latency', secondary: true, render: h => (typeof h.latency_ms === 'number' ? `${Math.round(h.latency_ms)} ms` : typeof h.avg_latency_ms === 'number' ? `${Math.round(h.avg_latency_ms)} ms` : '—') },
              { key: 'e', header: 'Last problem', render: h => (h.last_error ? <span className="ui-text--danger ui-wrap">{String(h.last_error)}</span> : <span className="ui-muted">None</span>) },
            ]}
          />
        )}
      </Card>
    </div>
  )
}
