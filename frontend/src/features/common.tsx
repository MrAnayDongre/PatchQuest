import type { ReactNode } from 'react'
import { friendlyError } from '../api/errors'
import type { Run } from '../api/types'
import { Badge, Button, StatusIndicator } from '../design/primitives'
import { Callout } from '../design/overlay'
import { friendlyStatusLine } from '../lib/eventCopy'
import { baseName, modelLabel, relativeTime } from '../lib/format'
import { runHash } from '../lib/router'

export function ErrorNotice({ error, onRetry, subject }: { error: unknown; onRetry?: () => void; subject?: string }) {
  const f = friendlyError(error, subject)
  return (
    <Callout tone="danger" title={f.title} action={onRetry ? <Button size="sm" onClick={onRetry}>Try again</Button> : undefined}>
      {f.message} {f.hint}
    </Callout>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1 className="page-header__title">{title}</h1>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  )
}

export function LineageBadge({ run }: { run: Pick<Run, 'lineage_kind' | 'parent_run_id'> }) {
  if (!run.lineage_kind) return null
  return <Badge tone="info">{run.lineage_kind === 'replay' ? 'Replay' : run.lineage_kind === 'fork' ? 'Fork' : run.lineage_kind}</Badge>
}

/** One run as a link row: task, where, how it stands, when. */
export function RunRow({ run, selected, now, id }: { run: Run; selected?: boolean; now?: number; id?: string }) {
  return (
    <a id={id} href={runHash(run.id)} className="run-row" aria-current={selected ? 'true' : undefined} data-run-id={run.id}>
      <StatusIndicator status={run.status} label="" />
      <span className="ui-sr-only">{friendlyStatusLine(run)}.</span>
      <span className="run-row__main">
        <span className="run-row__task ui-truncate">{run.task}</span>
        <span className="run-row__meta ui-truncate">
          {baseName(run.repo_path)} · {modelLabel(run.model) || run.provider} · {friendlyStatusLine(run)}
        </span>
      </span>
      <span className="run-row__side">
        <LineageBadge run={run} />
        <time dateTime={run.updated_at} className="run-row__time">{relativeTime(run.updated_at, now)}</time>
      </span>
    </a>
  )
}
