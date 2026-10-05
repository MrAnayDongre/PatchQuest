import type { ReactNode } from 'react'
import { Badge } from '../../design/primitives'
import { Callout } from '../../design/overlay'
import { humanize } from '../../lib/format'
import { phaseLabel } from '../../lib/phases'
import type { FailureInfo } from '../../lib/runState'

export function FailurePanel({ failure, actions }: { failure: FailureInfo; actions?: ReactNode }) {
  return (
    <Callout tone="danger" title={failure.message} action={actions}>
      <div className="failure__meta">
        <Badge tone="danger">{humanize(failure.kind)}</Badge>
        {failure.phase && <Badge>During: {phaseLabel(failure.phase).toLowerCase()}</Badge>}
        <Badge tone={failure.retryable ? 'info' : 'neutral'}>{failure.retryable ? 'Safe to retry' : 'Not retried automatically'}</Badge>
      </div>
      {failure.recovery.length > 0 && (
        <>
          <p className="failure__label">What you can do</p>
          <ul className="failure__recovery">
            {failure.recovery.map(r => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </>
      )}
      {failure.detail && (
        <details className="failure__detail">
          <summary>Technical detail</summary>
          <pre>{failure.detail}</pre>
        </details>
      )}
    </Callout>
  )
}
