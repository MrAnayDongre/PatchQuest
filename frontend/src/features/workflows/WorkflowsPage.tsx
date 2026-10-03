import { useEffect, useState } from 'react'
import { ApiError } from '../../api/errors'
import { listTemplates, listVersions, listWorkflowRuns, listWorkflows, type WorkflowRunSummary, type WorkflowVersion } from '../../api/workflows'
import { Dialog } from '../../design/overlay'
import { Callout } from '../../design/overlay'
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton } from '../../design/primitives'
import { StatusDot } from '../../design/primitives'
import { useAsync } from '../../hooks/useAsync'
import { relativeTime } from '../../lib/format'
import { connectorName, PERMISSION_COPY, triggerLabel } from '../../lib/workflow/copy'
import { runStatusLabel, runStatusTone } from '../../lib/workflow/run'
import { ErrorNotice, PageHeader } from '../common'

function Gallery({ open, onClose }: { open: boolean; onClose: () => void }) {
  const templates = useAsync(s => listTemplates(s), [], open)
  return (
    <Dialog open={open} onClose={onClose} title="Start from a template" description="Each one is checked against the available actions. You can change anything before saving." size="lg">
      <Callout tone="muted" title="About connections">
        PatchQuest can't yet show whether a connection such as GitHub is set up. A step that needs one fails with a clear message if it isn't.
      </Callout>
      {templates.loading && !templates.data ? (
        <Skeleton width="100%" height={160} />
      ) : templates.error ? (
        <ErrorNotice error={templates.error} onRetry={templates.refresh} subject="the templates" />
      ) : (
        <ul className="wf-gallery">
          {(templates.data ?? []).map(t => (
            <li key={t.name} className="wf-gallery__card">
              <h3>{t.name}</h3>
              <p>{t.description}</p>
              <div className="wf-gallery__meta">
                <Badge>{triggerLabel(t.trigger)}</Badge>
                {t.requires.map(r => (
                  <Badge key={r} tone="warning" title={`This template has steps that use ${connectorName(r)}.`}>needs: {r}</Badge>
                ))}
              </div>
              <a className="ui-btn ui-btn--primary ui-btn--sm" href={`#/workflows/new?template=${encodeURIComponent(t.name)}`} onClick={onClose}>Use this template</a>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}

export default function WorkflowsPage() {
  const flows = useAsync(s => listWorkflows(s), [])
  const runs = useAsync(s => listWorkflowRuns(s), [])
  const [gallery, setGallery] = useState(false)
  // every version of each workflow, so a run on an older version still shows under its workflow
  const [versions, setVersions] = useState<Record<string, WorkflowVersion[]>>({})
  const listed = flows.data
  useEffect(() => {
    if (!listed) return
    let live = true
    listed.slice(0, 30).forEach(w => {
      listVersions(w.id)
        .then(v => live && setVersions(prev => ({ ...prev, [w.id]: v })))
        .catch(() => {})
    })
    return () => {
      live = false
    }
  }, [listed])
  const versionOf = (workflowId: string): { name: string; version: number } | undefined => {
    for (const w of listed ?? []) {
      const v = versions[w.id]?.find(x => x.id === workflowId) ?? (w.id === workflowId ? { version: w.version } : undefined)
      if (v) return { name: w.name, version: v.version }
    }
    return undefined
  }
  const forbidden = flows.error instanceof ApiError && flows.error.status === 403
  const lastRun = (id: string): WorkflowRunSummary | undefined => {
    const ids = new Set((versions[id] ?? []).map(v => v.id).concat(id))
    return runs.data?.find(r => ids.has(r.workflow_id)) // the API lists newest first
  }
  const actions = (
    <>
      <Button icon="plus" onClick={() => setGallery(true)}>From template</Button>
      <a className="ui-btn ui-btn--primary" href="#/workflows/new">New workflow</a>
    </>
  )
  return (
    <div className="page">
      <PageHeader title="Workflows" subtitle="Chain agents, actions and approvals into something that survives restarts." actions={actions} />
      {forbidden ? (
        <Callout tone="warning" title={PERMISSION_COPY.view.title}>{PERMISSION_COPY.view.message}</Callout>
      ) : flows.error ? (
        <ErrorNotice error={flows.error} onRetry={flows.refresh} subject="your workflows" />
      ) : flows.loading && !flows.data ? (
        <Skeleton width="100%" height={160} />
      ) : (flows.data ?? []).length === 0 ? (
        <Card>
          <EmptyState icon="runs" title="No workflows yet" action={<><Button onClick={() => setGallery(true)}>Start from a template</Button><a className="ui-btn ui-btn--primary" href="#/workflows/new">Build one from scratch</a></>}>
            A workflow starts from a trigger, runs agents and actions, and stops for a person wherever it matters.
          </EmptyState>
        </Card>
      ) : (
        <Card padded={false}>
          <ul className="wf-rows" aria-label="Workflows">
            {(flows.data ?? []).map(w => {
              const r = lastRun(w.id)
              return (
                <li key={w.id}>
                  <a className="wf-row" href={`#/workflows/${encodeURIComponent(w.id)}`}>
                    <span className="wf-row__main">
                      <strong className="ui-truncate">{w.name}</strong>
                      <span className="ui-muted ui-truncate">{w.description || triggerLabel(w.trigger_type)}</span>
                    </span>
                    <Badge>{triggerLabel(w.trigger_type)}</Badge>
                    <Badge tone="muted">Version {w.version}</Badge>
                    <span className="wf-row__last ui-muted">
                      {r ? <><StatusDot tone={runStatusTone(r.status)} /> {runStatusLabel(r.status)} {relativeTime(r.created_at)}{versionOf(r.workflow_id) && versionOf(r.workflow_id)!.version !== w.version ? ` (version ${versionOf(r.workflow_id)!.version})` : ''}</> : 'No runs yet'}
                    </span>
                  </a>
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      <Card>
        <CardHeader title="Recent runs" subtitle="Across all workflows you can see" />
        {runs.error ? (
          runs.error instanceof ApiError && runs.error.status === 403 ? <p className="ui-muted">{PERMISSION_COPY.view.message}</p> : <ErrorNotice error={runs.error} onRetry={runs.refresh} subject="workflow runs" />
        ) : !runs.data?.length ? (
          <p className="ui-muted">{runs.loading ? 'Loading…' : 'No workflow runs yet.'}</p>
        ) : (
          <ul className="wf-rows">
            {runs.data.slice(0, 10).map(r => (
              <li key={r.id}>
                <a className="wf-row" href={`#/workflows/runs/${encodeURIComponent(r.id)}`}>
                  <span className="wf-row__main">
                    <strong className="ui-truncate">{versionOf(r.workflow_id)?.name ?? 'Workflow'}</strong>
                    <span className="ui-muted ui-truncate">{versionOf(r.workflow_id) ? `Version ${versionOf(r.workflow_id)!.version}` : r.id}</span>
                  </span>
                  <span className="wf-row__last"><StatusDot tone={runStatusTone(r.status)} /> {runStatusLabel(r.status)}</span>
                  <span className="ui-muted">{relativeTime(r.created_at)}</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Gallery open={gallery} onClose={() => setGallery(false)} />
    </div>
  )
}
