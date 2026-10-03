import type { Explanation } from '../../api/platform'
import { Badge, Card, CardHeader } from '../../design/primitives'
import { humanize } from '../../lib/format'
import { explain } from '../../lib/platform'

/** Plain-language reasons behind what the run did: choices, assumptions that stopped holding, and what it remembered. */
export function WhyPanel({ items }: { items: Explanation[] }) {
  return (
    <Card>
      <CardHeader title="Why this happened" subtitle="The reasons PatchQuest recorded for its choices in this run." />
      <ol className="why-list">
        {items.map(e => {
          const w = explain(e)
          return (
            <li key={w.id} className="why-list__item">
              <div className="why-list__head">
                <span className="why-list__title ui-wrap">{w.title}</span>
                {w.phase && <Badge tone={w.tone === 'warning' ? 'warning' : 'neutral'}>{humanize(w.phase)}</Badge>}
              </div>
              {w.details.length > 0 && <ul className="why-list__details">{w.details.map((d, i) => <li key={i} className="ui-wrap">{d}</li>)}</ul>}
            </li>
          )
        })}
      </ol>
    </Card>
  )
}
