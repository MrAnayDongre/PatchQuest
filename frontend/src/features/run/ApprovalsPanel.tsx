import { useEffect, useState } from 'react'
import { ApiError, decideApproval } from '../../api/client'
import { friendlyError } from '../../api/errors'
import type { ApprovalDecisionKind } from '../../api/types'
import { Badge, Button, Card, CardHeader, Textarea } from '../../design/primitives'
import { Callout } from '../../design/overlay'
import { riskTone, sideEffectLabel, sideEffectTone } from '../../lib/eventCopy'
import { formatDuration, parseTime } from '../../lib/format'
import type { ApprovalInfo } from '../../lib/runState'

export function useCountdown(expiresAt: string | null): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!expiresAt) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [expiresAt])
  const t = parseTime(expiresAt)
  return t === null ? null : Math.max(0, t - now)
}

export interface ApprovalCardProps {
  runId: string
  approval: ApprovalInfo
  /** Called after the server accepted a decision. */
  onDecided?: (id: string, decision: ApprovalDecisionKind) => void
  /** Called when the server says the request is gone or already answered, so the view can refresh. */
  onStale?: () => void
}

export function ApprovalCard({ runId, approval, onDecided, onStale }: ApprovalCardProps) {
  const [busy, setBusy] = useState<ApprovalDecisionKind | null>(null)
  const [editing, setEditing] = useState(false)
  const [edited, setEdited] = useState(approval.command ?? '')
  const [error, setError] = useState<{ title: string; message: string; hint?: string } | null>(null)
  const [sent, setSent] = useState<ApprovalDecisionKind | null>(null)
  const remaining = useCountdown(approval.expiresAt)
  const expired = remaining === 0
  const isCommand = approval.type === 'command'

  const send = async (decision: ApprovalDecisionKind) => {
    setBusy(decision)
    setError(null)
    try {
      await decideApproval(runId, approval.id, {
        decision,
        modified_command: decision === 'MODIFY' ? edited.trim() : undefined,
      })
      setSent(decision)
      onDecided?.(approval.id, decision)
    } catch (err) {
      setError(friendlyError(err, 'this approval'))
      if (err instanceof ApiError && (err.code === 'approval_already_decided' || err.code === 'approval_not_found')) onStale?.()
    } finally {
      setBusy(null)
    }
  }

  if (sent) {
    return (
      <Callout tone="success" title="Decision sent">
        The run will pick it up in a moment.
      </Callout>
    )
  }

  const disabled = busy !== null
  return (
    <div className="approval" role="group" aria-label="Approval request">
      <div className="approval__top">
        <Badge tone={sideEffectTone(approval.sideEffect)}>{sideEffectLabel(approval.sideEffect)}</Badge>
        <Badge tone={riskTone(approval.risk)}>{approval.risk} risk</Badge>
        {remaining !== null && (
          <span className="approval__timer" role="timer" aria-label={expired ? 'Request expired' : `Expires in ${formatDuration(remaining)}`}>
            {expired ? 'Expired. Treated as denied' : `Expires in ${formatDuration(remaining)}`}
          </span>
        )}
      </div>
      {approval.reason && <p className="approval__reason">{approval.reason}</p>}
      {approval.command && !editing && <pre className="approval__command" tabIndex={0} aria-label="Command to approve">{approval.command}</pre>}
      {editing && (
        <div className="approval__edit">
          <label htmlFor={`edit-${approval.id}`} className="ui-field__label">Edit the command</label>
          <Textarea id={`edit-${approval.id}`} value={edited} onChange={e => setEdited(e.target.value)} rows={3} spellCheck={false} className="ui-mono" />
          <p className="ui-field__hint">The edited command still has to pass the safety policy. A blocked command stays blocked.</p>
        </div>
      )}
      {error && (
        <Callout tone="danger" title={error.title}>
          {error.message} {error.hint}
        </Callout>
      )}
      <div className="approval__actions">
        {editing ? (
          <>
            <Button variant="primary" loading={busy === 'MODIFY'} disabled={disabled || !edited.trim()} onClick={() => send('MODIFY')}>Run edited command</Button>
            <Button variant="ghost" disabled={disabled} onClick={() => setEditing(false)}>Back</Button>
          </>
        ) : (
          <>
            <Button variant="primary" icon="check" loading={busy === 'APPROVE_ONCE'} disabled={disabled} onClick={() => send('APPROVE_ONCE')}>Approve once</Button>
            {approval.grantable && (
              <Button loading={busy === 'APPROVE_FOR_RUN'} disabled={disabled} onClick={() => send('APPROVE_FOR_RUN')} title="Also allow this exact command for the rest of this run">
                Approve for this run
              </Button>
            )}
            {isCommand && (
              <Button icon="edit" disabled={disabled} onClick={() => setEditing(true)}>Modify</Button>
            )}
            <Button variant="danger" icon="x" loading={busy === 'DENY'} disabled={disabled} onClick={() => send('DENY')}>Deny</Button>
            <Button variant="ghost" loading={busy === 'CANCEL_RUN'} disabled={disabled} onClick={() => send('CANCEL_RUN')}>Deny and cancel run</Button>
          </>
        )}
      </div>
    </div>
  )
}

export function ApprovalsPanel({ runId, approvals, onDecided, onStale }: { runId: string; approvals: ApprovalInfo[]; onDecided?: ApprovalCardProps['onDecided']; onStale?: () => void }) {
  if (!approvals.length) return null
  return (
    <Card id="approvals" className="approvals-panel" aria-labelledby="approvals-title">
      <CardHeader id="approvals-title" title={approvals.length === 1 ? 'Waiting for your approval' : `${approvals.length} approvals waiting`} subtitle="The run is paused until you answer. No answer counts as a denial." />
      <div className="approvals-panel__list">
        {approvals.map(a => (
          <ApprovalCard key={a.id} runId={runId} approval={a} onDecided={onDecided} onStale={onStale} />
        ))}
      </div>
    </Card>
  )
}
