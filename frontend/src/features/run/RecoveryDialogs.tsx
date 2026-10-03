import { useEffect, useState } from 'react'
import {
  ApiError,
  forkRun,
  getCheckpoints,
  getReplayComparison,
  getResumePlan,
  replayRun,
  resumeRun,
  type ReplayResult,
} from '../../api/client'
import { friendlyError } from '../../api/errors'
import type { CheckpointSummary, ReplayComparison, ResumePlan, Run } from '../../api/types'
import { Badge, Button, Field, Input, Select, Skeleton, Textarea } from '../../design/primitives'
import { Callout, Dialog } from '../../design/overlay'
import { Timeline } from '../../design/data'
import { phaseLabel } from '../../lib/phases'
import { CATEGORY_COPY, CERTAINTY_COPY, DRIFT_COPY, parseOverrides, resumeBody, resumeRequirements } from '../../lib/recoveryCopy'
import { runHash } from '../../lib/router'
import { ErrorNotice } from '../common'

/* ---------------------------------------------------------------- Resume */
export function ResumeDialog({ runId, open, onClose, onResumed }: { runId: string; open: boolean; onClose: () => void; onResumed: () => void }) {
  const [plan, setPlan] = useState<ResumePlan | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    let live = true
    setPlan(null)
    setError(null)
    setConfirmed(false)
    setLoading(true)
    getResumePlan(runId)
      .then(p => live && setPlan(p))
      .catch(e => live && setError(e))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [open, runId])

  const req = plan ? resumeRequirements(plan) : null
  const needsConfirm = req?.confirm != null
  const submit = async () => {
    if (!req) return
    setBusy(true)
    setError(null)
    try {
      await resumeRun(runId, resumeBody(req.confirm, confirmed))
      onResumed()
      onClose()
    } catch (err) {
      // The server may have a newer plan than the one we displayed: show it.
      if (err instanceof ApiError && err.data && typeof err.data.plan === 'object' && err.data.plan) setPlan(err.data.plan as ResumePlan)
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  const drift = plan ? DRIFT_COPY[plan.REPO_DRIFT] ?? { text: plan.REPO_DRIFT, tone: 'neutral' as const } : null
  const category = plan ? CATEGORY_COPY[plan.CATEGORY] : null

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Resume this run"
      description="Here is what PatchQuest will do before anything runs."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Not now</Button>
          <Button variant="primary" icon="play" loading={busy} disabled={!req?.canResume || (needsConfirm && !confirmed)} onClick={submit}>
            Resume run
          </Button>
        </>
      }
    >
      {loading && <Skeleton width="100%" height={120} />}
      {!!error && !loading && <ErrorNotice error={error} subject="this run" />}
      {plan && category && (
        <div className="recovery-plan">
          <Callout tone={category.tone} title={category.title}>
            {plan.RECOVERY_ACTION}
          </Callout>
          <dl className="kv">
            <dt>Last checkpoint</dt>
            <dd>{plan.LAST_CHECKPOINT}</dd>
            <dt>Interrupted work</dt>
            <dd>{plan.INTERRUPTED_OPERATION}</dd>
            <dt>Your repository</dt>
            <dd>{drift?.text}</dd>
            <dt>Side effects</dt>
            <dd>{CERTAINTY_COPY[plan.SIDE_EFFECT_CERTAINTY] ?? plan.SIDE_EFFECT_CERTAINTY}</dd>
          </dl>
          {plan.REASONS.length > 0 && (
            <ul className="recovery-plan__reasons">
              {plan.REASONS.map(r => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          {req?.confirm === 'drift' && (
            <label className="confirm">
              <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
              <span>I understand the repository changed. Continue anyway. The patch still won&apos;t overwrite files you edited.</span>
            </label>
          )}
          {req?.confirm === 'rollback' && (
            <label className="confirm">
              <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
              <span>Restore the partly written files to their original contents first, then continue.</span>
            </label>
          )}
        </div>
      )}
    </Dialog>
  )
}

/* ------------------------------------------------------------------ Fork */
export function ForkDialog({ run, open, onClose, onForked, initialCheckpoint }: { run: Run; open: boolean; onClose: () => void; onForked: (child: Run) => void; initialCheckpoint?: number }) {
  const [checkpoints, setCheckpoints] = useState<CheckpointSummary[]>([])
  const [from, setFrom] = useState<string>('')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [overrideText, setOverrideText] = useState('')
  const [acceptDrift, setAcceptDrift] = useState(false)
  const [needsDriftConfirm, setNeedsDriftConfirm] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const parsed = parseOverrides(overrideText)

  useEffect(() => {
    if (!open) return
    setError(null)
    setNeedsDriftConfirm(false)
    setAcceptDrift(false)
    setFrom(initialCheckpoint !== undefined ? String(initialCheckpoint) : '')
    getCheckpoints(run.id).then(setCheckpoints).catch(() => setCheckpoints([]))
  }, [open, run.id, initialCheckpoint])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const child = await forkRun(run.id, {
        from_checkpoint: from ? Number(from) : undefined,
        provider: provider.trim() || undefined,
        model: model.trim() || undefined,
        overrides: Object.keys(parsed.overrides).length ? parsed.overrides : undefined,
        accept_drift: acceptDrift,
      })
      onForked(child)
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.code === 'confirmation_required') setNeedsDriftConfirm(true)
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  const good = checkpoints.filter(c => c.status === 'ok')

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Fork this run"
      description="Start a new run from one of this run's checkpoints, with a different model or limits. The original stays as it is."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon="fork" loading={busy} disabled={parsed.errors.length > 0 || (needsDriftConfirm && !acceptDrift)} onClick={submit}>
            Create fork
          </Button>
        </>
      }
    >
      <div className="form-stack">
        <Field label="Start from">
          {p => (
            <Select {...p} value={from} onChange={e => setFrom(e.target.value)}>
              <option value="">Latest valid checkpoint</option>
              {good.map(c => (
                <option key={c.seq} value={c.seq}>
                  Checkpoint {c.seq}, after {phaseLabel(c.phase).toLowerCase()}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <div className="form-row">
          <Field label="Provider" optional hint={`Currently ${run.provider}`}>{p => <Input {...p} value={provider} onChange={e => setProvider(e.target.value)} placeholder={run.provider} />}</Field>
          <Field label="Model" optional hint={`Currently ${run.model ?? 'the default'}`}>{p => <Input {...p} value={model} onChange={e => setModel(e.target.value)} placeholder={run.model ?? ''} />}</Field>
        </div>
        <Field label="Setting overrides" optional error={parsed.errors[0] ?? null} hint="One per line, for example agent.max_model_calls=80. Safety settings can't be changed.">
          {p => <Textarea {...p} value={overrideText} onChange={e => setOverrideText(e.target.value)} rows={3} spellCheck={false} className="ui-mono" />}
        </Field>
        {needsDriftConfirm && (
          <label className="confirm">
            <input type="checkbox" checked={acceptDrift} onChange={e => setAcceptDrift(e.target.checked)} />
            <span>The repository changed since that checkpoint. Fork anyway.</span>
          </label>
        )}
        {!!error && <ErrorNotice error={error} subject="this run" />}
      </div>
    </Dialog>
  )
}

/* ---------------------------------------------------------------- Replay */
const REPLAY_MODES = [
  { id: 'state', label: 'Check the record', text: 'Rebuilds what happened from the ledger and checks it is consistent. Touches nothing.' },
  { id: 'model', label: 'Re-run with recorded answers', text: "Re-runs PatchQuest's steps using the model answers recorded the first time. Works in an isolated workspace and never writes your repository." },
  { id: 'live', label: 'Re-run with the model', text: 'Same, but asks the model again. Uses model calls. Never writes your repository.' },
] as const

export function ReplayDialog({ run, open, onClose }: { run: Run; open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<'state' | 'model' | 'live'>('state')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<ReplayResult | null>(null)
  const [comparison, setComparison] = useState<ReplayComparison | null>(null)
  const [comparing, setComparing] = useState(false)

  useEffect(() => {
    if (open) {
      setResult(null)
      setComparison(null)
      setError(null)
    }
  }, [open])

  const start = async () => {
    setBusy(true)
    setError(null)
    setComparison(null)
    try {
      setResult(await replayRun(run.id, mode))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }
  const compare = async (childId: string) => {
    setComparing(true)
    setError(null)
    try {
      setComparison(await getReplayComparison(run.id, childId))
    } catch (err) {
      setError(err)
    } finally {
      setComparing(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Replay this run"
      description="Replays create a separate run and never change the original."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Close</Button>
          <Button variant="primary" icon="replay" loading={busy} onClick={start}>Start replay</Button>
        </>
      }
    >
      <fieldset className="radio-group">
        <legend className="ui-sr-only">Replay mode</legend>
        {REPLAY_MODES.map(m => (
          <label key={m.id} className="radio">
            <input type="radio" name="replay-mode" checked={mode === m.id} onChange={() => setMode(m.id)} />
            <span>
              <strong>{m.label}</strong>
              <span className="radio__text">{m.text}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {!!error && <ErrorNotice error={error} subject="this replay" />}
      {result && result.mode === 'state' && (
        <div className="replay-result">
          <Callout tone={result.ok ? 'success' : 'warning'} title={result.ok ? 'The record is consistent' : 'The record has problems'}>
            {result.ok ? 'Status changes, checkpoints and phases all line up with the event history.' : 'These findings need a look:'}
          </Callout>
          {result.findings.length > 0 && (
            <ul className="recovery-plan__reasons">
              {result.findings.map(f => (
                <li key={f}>{String(f)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {result && result.mode !== 'state' && (
        <div className="replay-result">
          <Callout tone="info" title="Replay started" action={<a className="ui-btn ui-btn--secondary ui-btn--sm" href={runHash(result.run.id)} onClick={onClose}>Open replay</a>}>
            It runs as its own run. When it finishes you can compare it with this one.
          </Callout>
          <Button loading={comparing} onClick={() => compare(result.run.id)}>Compare with the original</Button>
          {comparison && <ComparisonView comparison={comparison} />}
        </div>
      )}
    </Dialog>
  )
}

function show(v: unknown): string {
  if (v === null || v === undefined) return 'none'
  return typeof v === 'string' ? v : JSON.stringify(v)
}

export function ComparisonView({ comparison }: { comparison: ReplayComparison }) {
  if (comparison.matched) {
    return <Callout tone="success" title="The replay matches the original" />
  }
  return (
    <div>
      <Callout tone="warning" title="The replay differs from the original" />
      <Timeline
        label="Differences"
        entries={comparison.divergences.map((d, i) => ({
          id: i,
          tone: 'warning',
          title: d.aspect.replace(/_/g, ' '),
          detail: (
            <span>
              Original: <code>{show(d.original)}</code> · Replay: <code>{show(d.replay)}</code>
            </span>
          ),
        }))}
      />
    </div>
  )
}

export { Badge }
