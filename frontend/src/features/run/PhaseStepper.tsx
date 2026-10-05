import { memo } from 'react'
import { Icon } from '../../design/icons'
import { phaseLabel } from '../../lib/phases'
import { phaseProgress, type PhaseInfo } from '../../lib/runState'

const STATUS_TEXT: Record<PhaseInfo['status'], string> = {
  pending: 'not started',
  running: 'in progress',
  complete: 'done',
  skipped: 'skipped',
  blocked: 'blocked',
  failed: 'failed',
}

export const PhaseStepper = memo(function PhaseStepper({ phases }: { phases: PhaseInfo[] }) {
  const { done, total } = phaseProgress(phases)
  return (
    <div className="stepper">
      <p className="stepper__summary">
        {done} of {total} steps done
      </p>
      <ol className="stepper__list" aria-label="Run phases">
        {phases.map(p => (
          <li key={p.phase} className={`step step--${p.status}`} aria-current={p.status === 'running' ? 'step' : undefined} title={p.note ?? undefined}>
            <span className="step__mark" aria-hidden="true">
              {p.status === 'complete' ? <Icon name="check" size={12} /> : p.status === 'failed' ? <Icon name="x" size={12} /> : p.status === 'blocked' ? <Icon name="pause" size={12} /> : null}
            </span>
            <span className="step__label">{phaseLabel(p.phase)}</span>
            <span className="ui-sr-only">, {STATUS_TEXT[p.status]}</span>
          </li>
        ))}
      </ol>
    </div>
  )
})
