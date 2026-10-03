import type { BudgetEntry } from '../../api/types'
import { Card, CardHeader, ProgressBar } from '../../design/primitives'
import { formatCount, humanize } from '../../lib/format'

const UNITS: Record<string, string> = { wall_time: 's', time: 's' }

function fmt(kind: string, n: number): string {
  return kind.includes('time') ? `${Math.round(n)} s` : `${formatCount(n)}${UNITS[kind] ?? ''}`
}

const LABELS: Record<string, string> = { wall_time_s: 'Wall time', wall_time: 'Wall time' }
const labelOf = (k: string) => LABELS[k] ?? humanize(k)

export function BudgetPanel({ budget }: { budget: BudgetEntry[] }) {
  return (
    <Card aria-labelledby="budget-title">
      <CardHeader id="budget-title" title="Budget" subtitle="What this run has used so far" />
      {budget.length === 0 ? (
        <p className="ui-muted">Usage appears after the first checkpoint is saved.</p>
      ) : (
        <ul className="budget-list">
          {budget.map(b => {
            const unlimited = b.limit <= 0
            const frac = unlimited ? undefined : Math.min(1, b.used / b.limit)
            return (
              <li key={b.kind} className="budget-list__item">
                <div className="budget-list__row">
                  <span>{labelOf(b.kind)}</span>
                  <span className="ui-muted">
                    {fmt(b.kind, b.used)} {unlimited ? 'used, no limit' : `of ${fmt(b.kind, b.limit)}`}
                  </span>
                </div>
                {unlimited ? null : (
                  <ProgressBar label={`${labelOf(b.kind)} used`} value={frac} size="sm" tone={b.exhausted ? 'danger' : frac !== undefined && frac > 0.8 ? 'warning' : 'info'} />
                )}
                {b.exhausted && <p className="budget-list__warn">Limit reached. Fork this run with a higher limit to continue.</p>}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
