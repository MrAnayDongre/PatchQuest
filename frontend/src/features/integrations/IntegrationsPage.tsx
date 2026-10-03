import { useState } from 'react'
import { deleteIntegration, getIntegrationEvents, getIntegrationKinds, listIntegrations, setIntegrationEnabled, testIntegration, type Integration } from '../../api/platform'
import { friendlyError } from '../../api/errors'
import { useApp } from '../../app/AppContext'
import { DataTable } from '../../design/data'
import { Callout, Dialog, Drawer, useToast } from '../../design/overlay'
import { Badge, Button, Card, EmptyState, IconButton, Skeleton } from '../../design/primitives'
import { useAsync } from '../../hooks/useAsync'
import { humanize, relativeTime } from '../../lib/format'
import { configSummary, describeSecret, isSimulated, webhookUrl } from '../../lib/platform'
import { runHash } from '../../lib/router'
import { ErrorNotice, PageHeader } from '../common'
import { WorkspaceGate, WorkspaceSelect } from '../workspace'
import { ConnectDialog } from './ConnectDialog'

const STATUS_TONE = { connected: 'success', error: 'danger', disabled: 'neutral' } as const
const STATUS_LABEL = { connected: 'Connected', error: 'Error', disabled: 'Disabled' } as const

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

function Deliveries({ workspaceId, integration, onClose }: { workspaceId: string; integration: Integration | null; onClose: () => void }) {
  const q = useAsync(s => getIntegrationEvents(workspaceId, integration!.id, s), [workspaceId, integration?.id], !!integration)
  return (
    <Drawer open={!!integration} onClose={onClose} side="bottom" size="lg" title={integration ? `Recent deliveries: ${integration.name}` : 'Recent deliveries'} description="Events that arrived from this kind of integration, newest first, and what became of them.">
      {q.error ? (
        <ErrorNotice error={q.error} onRetry={q.refresh} subject="the deliveries" />
      ) : !q.data ? (
        <Skeleton width="100%" height={120} />
      ) : (
        <DataTable
          caption="Recent deliveries"
          rows={q.data}
          rowKey={e => `${e.external_id}-${e.received_at}`}
          empty={<EmptyState icon="clock" title="Nothing has arrived yet">When this integration sends an event to its webhook address, it shows up here.</EmptyState>}
          columns={[
            { key: 'when', header: 'Received', render: e => <time dateTime={e.received_at} title={new Date(e.received_at).toLocaleString()}>{relativeTime(e.received_at)}</time> },
            { key: 'id', header: 'Event', render: e => <span className="ui-mono ui-wrap">{e.external_id}</span> },
            { key: 'status', header: 'Result', render: e => <Badge tone={/fail|reject|error|denied/i.test(e.status) ? 'danger' : /ignored|duplicate/i.test(e.status) ? 'neutral' : 'success'}>{humanize(e.status)}</Badge> },
            { key: 'run', header: 'Run', render: e => (e.run_id ? <a href={runHash(e.run_id)}>Open run</a> : <span className="ui-muted">None started</span>) },
          ]}
        />
      )}
    </Drawer>
  )
}

function IntegrationCard({ workspaceId, item, inbound, title, canManage, onChanged, onDeliveries, onRemove }: {
  workspaceId: string
  item: Integration
  inbound: boolean
  title: string
  canManage: boolean
  onChanged: () => void
  onDeliveries: () => void
  onRemove: () => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState<null | 'test' | 'toggle'>(null)
  const status = (item.status in STATUS_TONE ? item.status : 'error') as keyof typeof STATUS_TONE
  const disabled = item.status === 'disabled'
  const url = webhookUrl(item.webhook_path, window.location.origin)
  const why = canManage ? undefined : 'You need the connector.manage permission for this.'

  const test = async () => {
    setBusy('test')
    try {
      const r = await testIntegration(workspaceId, item.id)
      toast(r.ok ? { title: 'Connection works', message: item.name, tone: 'success' } : { title: 'The check failed', message: r.error ?? 'The service did not accept the connection.', tone: 'danger' })
      onChanged()
    } catch (err) {
      const f = friendlyError(err, 'this integration')
      toast({ title: f.title, message: f.message, tone: 'danger' })
    } finally {
      setBusy(null)
    }
  }
  const toggle = async () => {
    setBusy('toggle')
    try {
      await setIntegrationEnabled(workspaceId, item.id, disabled)
      toast({ title: disabled ? 'Integration enabled' : 'Integration disabled', message: disabled ? 'Events are accepted again.' : 'Events from it are ignored until you enable it again.', tone: 'success' })
      onChanged()
    } catch (err) {
      const f = friendlyError(err, 'this integration')
      toast({ title: f.title, message: f.message, tone: 'danger' })
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card as="article" aria-label={item.name} className="integration">
      <div className="integration__head">
        <h2 className="integration__name ui-wrap">{item.name}</h2>
        <div className="integration__badges">
          <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
          {isSimulated(item.name) && <Badge tone="info" title="Talks to a built-in stand-in, not the real service">Simulated</Badge>}
          <Badge>{title}</Badge>
        </div>
      </div>
      <dl className="kv integration__kv">
        {inbound && (
          <>
            <dt>Webhook</dt>
            <dd className="integration__hook">
              <code className="ui-mono ui-wrap">{url}</code>
              <IconButton icon="copy" size="sm" label="Copy webhook address" onClick={async () => toast((await copyText(url)) ? { title: 'Webhook address copied', tone: 'success' } : { title: "Couldn't copy", message: 'Select the address and copy it by hand.', tone: 'warning' })} />
            </dd>
          </>
        )}
        {Object.keys(item.config).length > 0 && (<><dt>Settings</dt><dd>{configSummary(item.config)}</dd></>)}
        <dt>Secrets</dt>
        <dd>
          {Object.keys(item.secrets).length === 0 ? <span className="ui-muted">None</span> : (
            <ul className="integration__secrets">
              {Object.entries(item.secrets).map(([name, ref]) => <li key={name}><span>{humanize(name)}</span> <code className="ui-mono">{describeSecret(ref)}</code></li>)}
            </ul>
          )}
        </dd>
        <dt>Last check</dt>
        <dd>{item.last_checked_at ? <time dateTime={item.last_checked_at}>{relativeTime(item.last_checked_at)}</time> : <span className="ui-muted">Never checked. Use Test to try it.</span>}</dd>
      </dl>
      {item.last_error && <Callout tone="danger" title="The last check failed">{item.last_error}</Callout>}
      <div className="integration__actions">
        <Button size="sm" icon="check" loading={busy === 'test'} disabled={!canManage || disabled} title={why ?? (disabled ? 'Enable it first to run a check.' : undefined)} onClick={test}>Test</Button>
        <Button size="sm" loading={busy === 'toggle'} disabled={!canManage} title={why} onClick={toggle}>{disabled ? 'Enable' : 'Disable'}</Button>
        {inbound && <Button size="sm" icon="list" onClick={onDeliveries}>Recent deliveries</Button>}
        <Button size="sm" variant="danger" icon="trash" disabled={!canManage} title={why} onClick={onRemove}>Remove</Button>
      </div>
    </Card>
  )
}

export default function IntegrationsPage() {
  const { can } = useApp()
  const canManage = can('connector.manage')
  const [connect, setConnect] = useState(false)
  return (
    <div className="page">
      <PageHeader
        title="Integrations"
        subtitle="Connect the tools your workflows listen to and act on. Secrets are never shown after saving."
        actions={<><WorkspaceSelect /><Button variant="primary" icon="plus" disabled={!canManage} title={canManage ? undefined : 'You need the connector.manage permission to connect an integration.'} onClick={() => setConnect(true)}>Connect</Button></>}
      />
      <WorkspaceGate>{ws => <Body workspaceId={ws} canManage={canManage} connect={connect} setConnect={setConnect} />}</WorkspaceGate>
    </div>
  )
}

function Body({ workspaceId, canManage, connect, setConnect }: { workspaceId: string; canManage: boolean; connect: boolean; setConnect: (v: boolean) => void }) {
  const toast = useToast()
  const list = useAsync(s => listIntegrations(workspaceId, s), [workspaceId])
  const kinds = useAsync(s => getIntegrationKinds(s), [])
  const [removing, setRemoving] = useState<Integration | null>(null)
  const [removeBusy, setRemoveBusy] = useState(false)
  const [deliveries, setDeliveries] = useState<Integration | null>(null)
  const kindOf = (k: string) => kinds.data?.kinds.find(x => x.kind === k)

  const remove = async () => {
    if (!removing) return
    setRemoveBusy(true)
    try {
      await deleteIntegration(workspaceId, removing.id)
      toast({ title: 'Integration removed', message: `${removing.name} will no longer receive or send anything.`, tone: 'success' })
      setRemoving(null)
      void list.refresh()
    } catch (err) {
      const f = friendlyError(err, 'this integration')
      toast({ title: f.title, message: f.message, tone: 'danger' })
      setRemoving(null)
    } finally {
      setRemoveBusy(false)
    }
  }

  return (
    <>
      {list.error ? (
        <ErrorNotice error={list.error} onRetry={list.refresh} subject="the integrations" />
      ) : !list.data ? (
        <Skeleton width="100%" height={200} />
      ) : list.data.length === 0 ? (
        <Card>
          <EmptyState icon="link" title="Nothing is connected yet" action={<Button variant="primary" icon="plus" disabled={!canManage} onClick={() => setConnect(true)}>Connect an integration</Button>}>
            {canManage ? 'Connect GitHub, Slack, Jira and more so workflows can react to events and post back.' : 'A workspace admin can connect GitHub, Slack, Jira and more here.'}
          </EmptyState>
        </Card>
      ) : (
        <div className="integration-list">
          {list.data.map(item => (
            <IntegrationCard
              key={item.id}
              workspaceId={workspaceId}
              item={item}
              inbound={kindOf(item.kind)?.inbound ?? true}
              title={kindOf(item.kind)?.title ?? humanize(item.kind)}
              canManage={canManage}
              onChanged={() => void list.refresh()}
              onDeliveries={() => setDeliveries(item)}
              onRemove={() => setRemoving(item)}
            />
          ))}
        </div>
      )}

      <ConnectDialog
        open={connect}
        workspaceId={workspaceId}
        kinds={kinds.data}
        onClose={() => setConnect(false)}
        onCreated={i => {
          setConnect(false)
          toast({ title: 'Integration connected', message: `${i.name} is saved. Use Test to check it.`, tone: 'success' })
          void list.refresh()
        }}
      />
      <Deliveries workspaceId={workspaceId} integration={deliveries} onClose={() => setDeliveries(null)} />
      <Dialog
        open={!!removing}
        onClose={() => setRemoving(null)}
        size="sm"
        title="Remove this integration?"
        description={removing ? `${removing.name} stops receiving events and workflows can no longer use it. Stored secrets are deleted; environment variables are not touched.` : undefined}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>Keep it</Button>
            <Button variant="danger" loading={removeBusy} onClick={remove}>Remove</Button>
          </>
        }
      >
        <span />
      </Dialog>
    </>
  )
}
