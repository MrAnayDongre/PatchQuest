import { useState, useEffect } from 'react'
import PixelPanel from '../components/PixelPanel'
import PixelButton from '../components/PixelButton'
import { getReport, getRun } from '../api/client'
import type { FinalReport, Run } from '../api/types'

interface Props {
  runId: string | null
  onOpenConsole?: (runId: string) => void
}

function renderMarkdown(md: string): string {
  return md
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
    .replace(/\n\n/g, '</p><p>')
}

export default function ReportPage({ runId, onOpenConsole }: Props) {
  const [report, setReport] = useState<FinalReport | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [tab, setTab] = useState<'report' | 'diff' | 'commands'>('report')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId) return
    getRun(runId).then(setRun).catch(() => {})
    getReport(runId).then(setReport).catch(() => setError('Report not available yet.'))
  }, [runId])

  if (!runId) {
    return (
      <div className="empty-state">
        <p className="empty-state__title">No run selected</p>
        <p className="empty-state__body">Complete a mission to view its engineering report.</p>
      </div>
    )
  }

  if (error) {
    return <div className="empty-state"><p>{error}</p></div>
  }

  const copy = (text: string) => navigator.clipboard.writeText(text)

  return (
    <div className="launch-panel" style={{ maxWidth: '960px' }}>
      <section className="hero" style={{ paddingTop: 0 }}>
        <h1 className="hero__title" style={{ fontSize: 'clamp(1.5rem, 3vw, 2.2rem)' }}>
          Mission <em>Report</em>
        </h1>
        <p className="hero__subtitle">Final orchestration artifact — analysis, diffs, and execution log.</p>
      </section>

      {run && (
        <div className="report-meta">
          <div className="report-meta__item"><div className="report-meta__label">Provider</div><div className="report-meta__value">{run.provider}</div></div>
          <div className="report-meta__item"><div className="report-meta__label">Model</div><div className="report-meta__value">{run.model || '—'}</div></div>
          <div className="report-meta__item"><div className="report-meta__label">Runtime</div><div className="report-meta__value">{run.runtime_mode}</div></div>
          <div className="report-meta__item"><div className="report-meta__label">Status</div><div className="report-meta__value">{run.status}</div></div>
        </div>
      )}

      <div className="report-tabs">
        {(['report', 'diff', 'commands'] as const).map(t => (
          <button
            key={t}
            type="button"
            className={`report-tab ${tab === t ? 'report-tab--active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <PixelPanel glow>
        {tab === 'report' && report?.report_md && (
          <div className="report-markdown" dangerouslySetInnerHTML={{ __html: `<p>${renderMarkdown(report.report_md)}</p>` }} />
        )}
        {tab === 'report' && !report?.report_md && <p style={{ color: 'var(--text-dim)' }}>Loading report…</p>}
        {tab === 'diff' && (
          <pre className="mono" style={{ whiteSpace: 'pre-wrap', color: 'var(--accent-cyan)', fontSize: '0.78rem' }}>
            {report?.diff_patch || 'No diff generated.'}
          </pre>
        )}
        {tab === 'commands' && (
          <pre className="mono" style={{ whiteSpace: 'pre-wrap', fontSize: '0.78rem' }}>
            {report?.commands_log || 'No commands logged.'}
          </pre>
        )}
      </PixelPanel>

      <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {onOpenConsole && runId && (
          <PixelButton variant="secondary" onClick={() => onOpenConsole(runId)}>Open Console</PixelButton>
        )}
        {report?.report_md && (
          <PixelButton variant="secondary" onClick={() => copy(report.report_md || '')}>Copy Report</PixelButton>
        )}
        <PixelButton variant="ghost" onClick={() => copy(runId)}>Copy Run ID</PixelButton>
      </div>
    </div>
  )
}
