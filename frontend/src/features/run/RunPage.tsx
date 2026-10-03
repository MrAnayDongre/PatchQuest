import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, cancelRun } from '../../api/client'
import { friendlyError } from '../../api/errors'
import type { Run } from '../../api/types'
import { useApp } from '../../app/AppContext'
import { Button, Card, CardHeader, Skeleton, TabPanel, Tabs } from '../../design/primitives'
import { Callout, Dialog, useToast } from '../../design/overlay'
import { useRunStream } from '../../hooks/useRunStream'
import type { PaletteCommand } from '../../lib/palette'
import { navigate, runHash } from '../../lib/router'
import { isActiveStatus } from '../../lib/runState'
import { ErrorNotice } from '../common'
import { ApprovalsPanel } from './ApprovalsPanel'
import { BudgetPanel } from './BudgetPanel'
import { ChangesPanel } from './ChangesPanel'
import { EventTimeline } from './EventTimeline'
import { FailurePanel } from './FailurePanel'
import { PhaseStepper } from './PhaseStepper'
import { ForkDialog, ReplayDialog, ResumeDialog } from './RecoveryDialogs'
import { LineagePanel, RecoveryPanel } from './RecoveryPanel'
import { RunHeader } from './RunHeader'

const TABS = ['activity', 'changes', 'recovery']

export default function RunPage({ runId, tab }: { runId: string; tab?: string }) {
  const { run, state, connection, error, refreshRun, reconnect } = useRunStream(runId)
  const { setRunCommands, refreshRuns } = useApp()
  const toast = useToast()
  const [dialog, setDialog] = useState<null | 'resume' | 'fork' | 'replay' | 'cancel'>(null)
  const [forkFrom, setForkFrom] = useState<number | undefined>()
  const [cancelling, setCancelling] = useState(false)
  const current = TABS.includes(tab ?? '') ? (tab as string) : 'activity'
  const setTab = (t: string) => window.location.replace(runHash(runId, t === 'activity' ? undefined : t))

  const status = run?.status
  const focusApprovals = useCallback(() => {
    const el = document.getElementById('approvals')
    el?.scrollIntoView?.({ block: 'center' })
    el?.querySelector<HTMLElement>('button')?.focus()
  }, [])

  // Make this run's actions available in the command palette while it is on screen.
  const pending = state.pendingApprovals.length
  useEffect(() => {
    if (!status) return
    const cmds: PaletteCommand[] = []
    if (pending) cmds.push({ id: 'run:approve', title: `Review pending approval${pending > 1 ? 's' : ''}`, group: 'This run', keywords: ['approve', 'deny'], run: focusApprovals })
    if (status === 'running' || status === 'waiting_approval' || status === 'created') cmds.push({ id: 'run:cancel', title: 'Cancel this run', group: 'This run', run: () => setDialog('cancel') })
    if (status === 'interrupted') cmds.push({ id: 'run:resume', title: 'Resume this run', group: 'This run', run: () => setDialog('resume') })
    if (!isActiveStatus(status)) {
      cmds.push({ id: 'run:fork', title: 'Fork this run', group: 'This run', run: () => { setForkFrom(undefined); setDialog('fork') } })
      cmds.push({ id: 'run:replay', title: 'Replay this run', group: 'This run', run: () => setDialog('replay') })
    }
    setRunCommands(cmds)
    return () => setRunCommands([])
  }, [status, pending, setRunCommands, focusApprovals])

  const doCancel = async () => {
    setCancelling(true)
    try {
      await cancelRun(runId)
      toast({ title: 'Cancelling', message: 'The run will stop shortly. A step already writing to your repository finishes first.', tone: 'info' })
      setDialog(null)
      void refreshRun()
      void refreshRuns()
    } catch (err) {
      const f = friendlyError(err, 'this run')
      toast({ title: f.title, message: f.message, tone: 'danger' })
      setDialog(null)
      void refreshRun()
    } finally {
      setCancelling(false)
    }
  }

  const afterResume = () => {
    toast({ title: 'Run resumed', message: 'Picking up from the last checkpoint.', tone: 'success' })
    void refreshRun()
    reconnect()
    void refreshRuns()
  }

  if (error && !run) {
    const notFound = error instanceof ApiError && error.status === 404
    return (
      <div className="page">
        <ErrorNotice error={error} subject="this run" onRetry={notFound ? undefined : () => window.location.reload()} />
        <p><a href="#/runs">Back to all runs</a></p>
      </div>
    )
  }
  if (!run) {
    return (
      <div className="page" aria-busy="true" aria-label="Loading run">
        <Skeleton width="60%" height={28} />
        <div style={{ height: 12 }} />
        <Skeleton width="40%" />
        <div style={{ height: 24 }} />
        <Skeleton width="100%" height={220} />
      </div>
    )
  }

  const failureActions = run.status === 'failed' ? (
    <Button size="sm" icon="fork" onClick={() => { setForkFrom(undefined); setDialog('fork') }}>Fork from a checkpoint</Button>
  ) : undefined

  return (
    <div className="page run-page">
      <RunHeader run={run} state={state} connection={connection} onCancel={() => setDialog('cancel')} onResume={() => setDialog('resume')} onFork={() => { setForkFrom(undefined); setDialog('fork') }} onReplay={() => setDialog('replay')} />

      {run.status === 'interrupted' && (
        <Callout tone="warning" title="This run was interrupted" action={<Button variant="primary" size="sm" icon="play" onClick={() => setDialog('resume')}>Review and resume</Button>}>
          {state.statusReason ?? 'The worker stopped while it was in progress.'} Progress up to the last checkpoint is saved.
        </Callout>
      )}
      {state.failure && (run.status === 'failed' || run.status === 'cancelled') && <FailurePanel failure={state.failure} actions={failureActions} />}

      <ApprovalsPanel runId={run.id} approvals={state.pendingApprovals} onStale={() => void refreshRun()} />

      <Card padded>
        <PhaseStepper phases={state.phases} />
      </Card>

      <div className="run-grid">
        <div className="run-grid__main">
          <Tabs
            label="Run details"
            idPrefix="run"
            value={current}
            onChange={setTab}
            tabs={[
              { id: 'activity', label: 'Activity', count: undefined },
              { id: 'changes', label: 'Changes', count: state.proposedFiles.length },
              { id: 'recovery', label: 'Recovery', count: state.checkpoints.length },
            ]}
          />
          <TabPanel idPrefix="run" id="activity" active={current === 'activity'}>
            <EventTimeline items={state.timeline} />
          </TabPanel>
          <TabPanel idPrefix="run" id="changes" active={current === 'changes'}>
            <ChangesPanel run={run as Run} state={state} />
          </TabPanel>
          <TabPanel idPrefix="run" id="recovery" active={current === 'recovery'}>
            <RecoveryPanel run={run} state={state} onResume={() => setDialog('resume')} onFork={seq => { setForkFrom(seq); setDialog('fork') }} onReplay={() => setDialog('replay')} />
          </TabPanel>
        </div>
        <aside className="run-grid__side" aria-label="Run summary">
          <BudgetPanel budget={state.budget} />
          <LineagePanel run={run} notes={state.lineageNotes.length} />
          <Card>
            <CardHeader title="Details" />
            <dl className="kv">
              <dt>Repository</dt>
              <dd className="ui-mono ui-wrap">{run.repo_path}</dd>
              <dt>Run ID</dt>
              <dd className="ui-mono ui-wrap">{run.id}</dd>
              <dt>Started</dt>
              <dd>{new Date(run.created_at).toLocaleString()}</dd>
            </dl>
          </Card>
        </aside>
      </div>

      {pending > 0 && (
        <div className="approval-bar">
          <span>{pending === 1 ? '1 approval is waiting for you' : `${pending} approvals are waiting for you`}</span>
          <Button variant="primary" onClick={focusApprovals}>Review</Button>
        </div>
      )}

      <ResumeDialog runId={run.id} open={dialog === 'resume'} onClose={() => setDialog(null)} onResumed={afterResume} />
      <ForkDialog run={run} open={dialog === 'fork'} initialCheckpoint={forkFrom} onClose={() => setDialog(null)} onForked={child => { toast({ title: 'Fork created', tone: 'success' }); void refreshRuns(); navigate(runHash(child.id)) }} />
      <ReplayDialog run={run} open={dialog === 'replay'} onClose={() => setDialog(null)} />
      <Dialog
        open={dialog === 'cancel'}
        onClose={() => setDialog(null)}
        title="Cancel this run?"
        description="It stops whatever is running. Work saved in checkpoints is kept, and your repository is only changed if the patch had already landed."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDialog(null)}>Keep running</Button>
            <Button variant="danger" loading={cancelling} onClick={doCancel}>Cancel run</Button>
          </>
        }
      >
        <span />
      </Dialog>
    </div>
  )
}
