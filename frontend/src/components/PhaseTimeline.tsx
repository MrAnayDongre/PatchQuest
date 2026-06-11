import { PHASE_GLYPHS, PHASE_LABELS, type MissionPhase } from '../lib/phases'

interface Phase {
  phase: string
  status: string
}

interface PhaseTimelineProps {
  phases: Phase[]
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Pending',
  running: 'Running',
  complete: 'Complete',
  skipped: 'Skipped',
  failed: 'Failed',
}

export default function PhaseTimeline({ phases }: PhaseTimelineProps) {
  return (
    <div className="phase-rail" role="list" aria-label="Mission phase timeline">
      {phases.map((p, index) => {
        const key = p.phase as MissionPhase
        const label = PHASE_LABELS[key] || p.phase
        const glyph = PHASE_GLYPHS[key] || '·'
        const isRunning = p.status === 'running'
        const isTerminal = p.status === 'complete' || p.status === 'skipped'
        return (
          <div key={p.phase} role="listitem">
            {index > 0 && (
              <div
                className="phase-rail__connector"
                aria-hidden
                style={isTerminal || isRunning ? { background: 'linear-gradient(180deg, var(--accent-cyan-dim), var(--border-color))' } : undefined}
              />
            )}
            <div
              className={`phase-rail__node phase-rail__node--${p.status}`}
              aria-current={isRunning ? 'step' : undefined}
            >
              <span className="phase-rail__glyph" aria-hidden>{glyph}</span>
              <div className="phase-rail__body">
                <div className="phase-rail__label">{label}</div>
                <div className="phase-rail__key">{p.phase}</div>
              </div>
              <span className={`status-badge status-badge--${p.status}`}>
                {STATUS_LABELS[p.status] || p.status}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
