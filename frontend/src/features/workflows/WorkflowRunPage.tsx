import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, friendlyError } from '../../api/errors'
import { cancelWorkflowRun, decideStep, getWorkflow, getWorkflowRun, resolveStep } from '../../api/workflows'
import { CodeBlock, Timeline } from '../../design/data'
import { Callout, Dialog, useToast } from '../../design/overlay'
import { Badge, Button, Card, CardHeader, Skeleton, StatusDot } from '../../design/primitives'
import { usePolling } from '../../hooks/useAsync'
import { relativeTime } from '../../lib/format'
import { PERMISSION_COPY } from '../../lib/workflow/copy'
import { ensureLayout, layoutFrom } from '../../lib/workflow/layout'
import { approvalMessage, describeWfEvent, isRunTerminal, nodeVisuals, pendingApprovals, runStatusLabel, runStatusTone, uncertainSteps, VISUAL_COPY, visualOf, waitingExplanation, type WfRun, type WfStep } from '../../lib/workflow/run'
import type { WfDef } from '../../lib/workflow/types'
import type { Viewport } from '../../lib/workflow/viewport'
import { ErrorNotice } from '../common'
import { Canvas } from './Canvas'

const NONE = new Set<string>()

function StepRow({ step }: { step: WfStep }) {
  const visual = visualOf(step)
  const tone = VISUAL_COPY[visual].tone
  const out = step.output && Object.keys(step.output as object).length ? JSON.stringify(step.output, null, 2) : null
  return (
    <li className="wf-step">
      <div className="wf-step__head">
        <StatusDot tone={tone} />
        <strong>{step.node_id}</strong>
        {step.visit > 1 && <span className="ui-muted">visit {step.visit}</span>}
        <Badge tone={tone}>{VISUAL_COPY[visual].label}</Badge>
        {step.child_run_id && <a href={`#/runs/${encodeURIComponent(step.child_run_id)}`}>Open agent run</a>}
      </div>
      {step.status === 'waiting' && <p className="ui-muted">{waitingExplanation(step)}{step.wake_at ? ` It gives up ${relativeTime(step.wake_at)}.` : ''}</p>}
      {step.decision && <p className="ui-muted">Answered: {step.decision}{step.decided_by ? ` by ${step.decided_by}` : ''}.</p>}
      {step.error && <p className="ui-text--danger ui-wrap">{step.error}</p>}
      {out && (
        <details>
          <summary>Output</summary>
          <CodeBlock code={out} maxHeight={200} />
        </details>
      )}
    </li>
  )
}

export default function WorkflowRunPage({ id }: { id: string }) {
  const toast = useToast()
  const [run, setRun] = useState<WfRun | null>(null)
  const [def, setDef] = useState<WfDef | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const [viewport, setViewport] = useState<Viewport>({ x: 40, y: 40, zoom: 1 })
  const [fit, setFit] = useState(0)
  const defLoaded = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await getWorkflowRun(id)
      setRun(r)
      setError(null)
      if (defLoaded.current !== r.workflow_id) {
        defLoaded.current = r.workflow_id
        try {
          const w = await getWorkflow(r.workflow_id)
          setDef(w.definition)
          setName(`${w.name} · version ${w.version}`)
          setFit(n => n + 1)
        } catch {
          // the graph is a nicety; steps and events still show
        }
      }
    } catch (e) {
      setError(e)
    }
  }, [id])

  useEffect(() => {
    defLoaded.current = null
    setRun(null)
    void refresh()
  }, [id, refresh])

  const terminal = run ? isRunTerminal(run.status) : false
  // no push channel exists for workflows: poll while the run is live and the tab is visible
  usePolling(() => void refresh(), 2000, !!run && !terminal)

  const act = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key)
    try {
      await fn()
      toast({ title: done, tone: 'success' })
    } catch (e) {
      const forbidden = e instanceof ApiError && e.status === 403
      const f = friendlyError(e, 'this step')
      toast({
        title: forbidden ? (key.startsWith('d:') ? PERMISSION_COPY.decide.title : PERMISSION_COPY.control.title) : e instanceof ApiError && (e.code === 'not_pending' || e.code === 'not_resolvable') ? 'Already settled' : f.title,
        message: forbidden ? (key.startsWith('d:') ? PERMISSION_COPY.decide.message : PERMISSION_COPY.control.message) : f.message,
        tone: 'danger',
      })
    } finally {
      setBusy(null)
      void refresh()
    }
  }

  const visuals = useMemo(() => (def && run ? nodeVisuals(def, run.steps) : undefined), [def, run])
  const positions = useMemo(() => (def ? ensureLayout(def, layoutFrom(def)) : {}), [def])
  const approvals = run ? pendingApprovals(run.steps) : []
  const uncertain = run ? uncertainSteps(run.steps) : []
  const attention = useMemo(() => new Set([...approvals, ...uncertain].map(s => s.node_id)), [approvals, uncertain])
  const selected = useRef(new Set<string>())

  if (error && !run) {
    return (
      <div className="page">
        {error instanceof ApiError && error.status === 403 ? <Callout tone="warning" title={PERMISSION_COPY.view.title}>{PERMISSION_COPY.view.message}</Callout> : <ErrorNotice error={error} subject="this workflow run" onRetry={() => void refresh()} />}
        <p><a href="#/workflows">Back to workflows</a></p>
      </div>
    )
  }
  if (!run) return <div className="page"><Skeleton width="100%" height={300} /></div>

  return (
    <div className="page wf-run">
      <header className="run-header">
        <div className="run-header__top">
          <h1 className="run-header__task">{name || 'Workflow run'}</h1>
          <div className="run-header__actions">
            {!terminal && <Button variant="danger" icon="stop" onClick={() => setConfirmCancel(true)}>Cancel run</Button>}
          </div>
        </div>
        <div className="run-header__status" aria-live="polite">
          <StatusDot tone={runStatusTone(run.status)} pulse={!terminal} />
          <span className="run-header__line">{runStatusLabel(run.status)}</span>
          {!terminal && <span className="ui-muted">Updating every 2 seconds</span>}
        </div>
        <ul className="run-header__meta">
          <li>Started {relativeTime(run.created_at)}</li>
          {run.created_by && <li>by {run.created_by}</li>}
          {run.completed_at && <li>Finished {relativeTime(run.completed_at)}</li>}
          {def && <li><a href={`#/workflows/${encodeURIComponent(run.workflow_id)}`}>Open the workflow</a></li>}
        </ul>
      </header>

      {run.status === 'failed' && <Callout tone="danger" title="This run failed">{run.error ?? 'A step failed and the workflow stopped.'}</Callout>}
      {run.status === 'cancelled' && <Callout tone="muted" title="This run was cancelled">Anything still in flight was stopped.</Callout>}
      {error != null && <Callout tone="warning" title="Couldn't refresh">Showing the last known state. Trying again.</Callout>}

      {approvals.length > 0 && (
        <Card className="approvals-panel" aria-labelledby="wf-approvals">
          <CardHeader id="wf-approvals" title={approvals.length === 1 ? 'Waiting for your approval' : `${approvals.length} approvals waiting`} subtitle="The workflow is paused here until a person answers." />
          <div className="approvals-panel__list">
            {approvals.map(s => (
              <div key={s.node_id} className="approval" role="group" aria-label={`Approval for ${s.node_id}`}>
                <p className="approval__reason"><strong>{s.node_id}</strong>: {approvalMessage(run.events, s.node_id) ?? 'Approve this step?'}</p>
                {s.wake_at && <p className="ui-muted">No answer {relativeTime(s.wake_at)} counts as denied.</p>}
                <div className="approval__actions">
                  <Button variant="primary" icon="check" loading={busy === `d:${s.node_id}:approve`} disabled={busy !== null} onClick={() => act(`d:${s.node_id}:approve`, () => decideStep(run.id, s.node_id, 'approve'), 'Approved')}>Approve</Button>
                  <Button variant="danger" icon="x" loading={busy === `d:${s.node_id}:deny`} disabled={busy !== null} onClick={() => act(`d:${s.node_id}:deny`, () => decideStep(run.id, s.node_id, 'deny'), 'Denied')}>Deny</Button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {uncertain.map(s => (
        <Callout key={s.node_id} tone="danger" title={`Check "${s.node_id}" before continuing`}>
          <p>PatchQuest stopped while this action was running and can't tell whether it took effect. Repeating an action that already happened could do it twice, for example post a second comment or send a second message.</p>
          <div className="approval__actions">
            <Button loading={busy === `r:${s.node_id}:happened`} disabled={busy !== null} onClick={() => act(`r:${s.node_id}:happened`, () => resolveStep(run.id, s.node_id, 'happened'), 'Marked as done')}>It already happened</Button>
            <Button loading={busy === `r:${s.node_id}:retry`} disabled={busy !== null} onClick={() => act(`r:${s.node_id}:retry`, () => resolveStep(run.id, s.node_id, 'retry'), 'Trying again')}>Try again</Button>
            <Button variant="danger" loading={busy === `r:${s.node_id}:failed`} disabled={busy !== null} onClick={() => act(`r:${s.node_id}:failed`, () => resolveStep(run.id, s.node_id, 'failed'), 'Marked as failed')}>It did not happen: fail the step</Button>
          </div>
        </Callout>
      ))}

      {def ? (
        <Card padded={false} className="wf-run__graph">
          <Canvas def={def} positions={positions} selection={NONE} readOnly visuals={visuals} highlight={attention} viewport={viewport} onViewport={setViewport} fitSignal={fit} label="Workflow graph. Colours show each step's progress." onSelect={ids => { selected.current = new Set(ids) }} />
        </Card>
      ) : (
        <Skeleton width="100%" height={200} />
      )}

      <div className="run-grid">
        <div className="run-grid__main">
          <Card>
            <CardHeader title="Steps" />
            {run.steps.length === 0 ? <p className="ui-muted">Nothing has run yet.</p> : <ul className="wf-steps">{run.steps.map(s => <StepRow key={`${s.node_id}-${s.visit}`} step={s} />)}</ul>}
          </Card>
        </div>
        <aside className="run-grid__side">
          <Card>
            <CardHeader title="What happened" />
            <Timeline
              label="Workflow events"
              entries={run.events.slice(-60).reverse().map(e => {
                const d = describeWfEvent(e)
                return { id: e.id, title: d.title, tone: d.tone, meta: `${relativeTime(e.created_at)}${e.actor ? ` · ${e.actor}` : ''}`, detail: d.detail }
              })}
            />
          </Card>
        </aside>
      </div>

      <Dialog open={confirmCancel} onClose={() => setConfirmCancel(false)} title="Cancel this workflow run?" description="Waiting steps stop and anything still in flight is cancelled. Steps that already finished are not undone." size="sm" footer={<><Button variant="ghost" onClick={() => setConfirmCancel(false)}>Keep running</Button><Button variant="danger" onClick={() => { setConfirmCancel(false); void act('c:run', () => cancelWorkflowRun(run.id), 'Run cancelled') }}>Cancel run</Button></>}>
        <span />
      </Dialog>
    </div>
  )
}
