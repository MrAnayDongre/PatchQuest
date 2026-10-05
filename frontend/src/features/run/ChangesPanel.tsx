import { memo, useMemo, useState } from 'react'
import { getReport } from '../../api/client'
import type { Run } from '../../api/types'
import { CodeBlock } from '../../design/data'
import { DiffViewer } from '../../design/DiffViewer'
import { Callout } from '../../design/overlay'
import { Badge, Skeleton, TabPanel, Tabs } from '../../design/primitives'
import { useAsync } from '../../hooks/useAsync'
import { appliedExplanation } from '../../lib/changes'
import { diffTotals, filterFiles, parseUnifiedDiff } from '../../lib/diff'
import { verdictLabel, verdictTone } from '../../lib/eventCopy'
import { formatDuration, humanize } from '../../lib/format'
import { isActiveStatus, type RunState } from '../../lib/runState'
import { ErrorNotice } from '../common'

export const ChangesPanel = memo(function ChangesPanel({ run, state }: { run: Run; state: RunState }) {
  const [tab, setTab] = useState('proposed')
  const finished = !isActiveStatus(run.status)
  const wantReport = finished || state.reportReady
  const report = useAsync(signal => getReport(run.id, signal), [run.id, run.status, state.reportReady], wantReport)
  const files = useMemo(() => parseUnifiedDiff(report.data?.diff_patch), [report.data?.diff_patch])
  const applied = useMemo(() => filterFiles(files, new Set(state.promotedFiles)), [files, state.promotedFiles])
  const verdict = run.verdict ?? state.verdict
  const outcome = run.outcome ?? state.outcome
  const explain = appliedExplanation({ status: run.status, outcome, verdict, rejectionReason: state.rejectionReason })
  const totals = diffTotals(files)
  const noReportYet = report.error?.status === 404

  return (
    <div className="changes">
      <div className="changes__summary">
        {verdict ? <Badge tone={verdictTone(verdict)}>{verdictLabel(verdict)}</Badge> : <Badge tone="muted">No checks yet</Badge>}
        {state.proposedFiles.length > 0 || files.length > 0 ? (
          <span className="ui-muted">
            {files.length ? `${totals.files} files, +${totals.additions} −${totals.deletions}` : `${state.proposedFiles.length} files proposed`}
          </span>
        ) : null}
      </div>
      <Tabs
        label="Changes"
        idPrefix="changes"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'proposed', label: 'Proposed', count: files.length || state.proposedFiles.length },
          { id: 'applied', label: 'Applied to repo', count: state.promotedFiles.length },
          { id: 'checks', label: 'Checks', count: state.commands.length },
        ]}
      />
      <TabPanel idPrefix="changes" id="proposed" active={tab === 'proposed'}>
        {report.loading && wantReport && !report.data ? (
          <Skeleton width="100%" height={160} />
        ) : report.error && !noReportYet ? (
          <ErrorNotice error={report.error} onRetry={report.refresh} subject="the report" />
        ) : files.length ? (
          <DiffViewer files={files} />
        ) : state.proposedFiles.length ? (
          <div className="form-stack">
            <p>The model proposed changes to:</p>
            <ul className="file-list">
              {state.proposedFiles.map(f => (
                <li key={f.path}><code>{f.path}</code></li>
              ))}
            </ul>
            <p className="ui-muted">The full diff appears when the run finishes.</p>
          </div>
        ) : (
          <p className="ui-muted">{finished ? 'This run did not propose any changes.' : 'No patch yet. The diff shows up here once the model proposes one.'}</p>
        )}
      </TabPanel>
      <TabPanel idPrefix="changes" id="applied" active={tab === 'applied'}>
        <Callout tone={explain.tone} title={explain.title}>{explain.body}</Callout>
        {applied.length > 0 && (
          <div className="changes__applied">
            <DiffViewer files={applied} />
          </div>
        )}
        {applied.length === 0 && state.promotedFiles.length > 0 && (
          <ul className="file-list">
            {state.promotedFiles.map(f => (
              <li key={f}><code>{f}</code></li>
            ))}
          </ul>
        )}
      </TabPanel>
      <TabPanel idPrefix="changes" id="checks" active={tab === 'checks'}>
        {state.commands.length === 0 ? (
          <p className="ui-muted">No commands have run yet.</p>
        ) : (
          <ul className="cmd-list">
            {state.commands.map(c => (
              <li key={c.eventId} className="cmd">
                <code className="cmd__text">{c.command}</code>
                <span className="cmd__result">
                  {c.status === 'running' && <Badge tone="info">Running</Badge>}
                  {c.status === 'denied' && <Badge tone="warning">Not approved</Badge>}
                  {c.status === 'blocked' && <Badge tone="danger">Blocked by policy</Badge>}
                  {c.status === 'finished' && (
                    <Badge tone={c.timedOut ? 'warning' : c.returncode === 0 ? 'success' : 'danger'}>
                      {c.timedOut ? 'Timed out' : c.returncode === 0 ? 'Passed' : `Exit ${c.returncode ?? '?'}`}
                    </Badge>
                  )}
                  {c.durationS !== null && <span className="ui-muted">{formatDuration(c.durationS * 1000)}</span>}
                  {c.sideEffect && <span className="ui-muted">{humanize(c.sideEffect)}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {report.data?.commands_log && <CodeBlock code={report.data.commands_log} label="Full command log" maxHeight={280} />}
      </TabPanel>
    </div>
  )
})
