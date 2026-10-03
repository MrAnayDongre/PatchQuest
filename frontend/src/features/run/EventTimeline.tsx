import { memo, useCallback, useMemo, useRef, useState } from 'react'
import { CodeBlock, VirtualList, type VirtualListHandle } from '../../design/data'
import { Button, Input, StatusDot, Switch } from '../../design/primitives'
import { Dialog } from '../../design/overlay'
import { CATEGORY_LABELS, CATEGORY_ORDER, type EventCategory } from '../../lib/eventCopy'
import { relativeTime } from '../../lib/format'
import { phaseLabel } from '../../lib/phases'
import type { TimelineItem } from '../../lib/runState'
import { countByCategory, filterTimeline, rowHeight, toRows, type TimelineRow } from '../../lib/timeline'

const clock = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function Row({ row, onOpen }: { row: TimelineRow; onOpen: (row: TimelineRow) => void }) {
  const { item, count } = row
  return (
    <button type="button" className="tl-row" onClick={() => onOpen(row)} aria-label={`${item.title}${count > 1 ? `, repeated ${count} times` : ''}. Open details.`}>
      <StatusDot tone={item.tone} />
      <span className="tl-row__main">
        <span className="tl-row__title ui-truncate">
          {item.title}
          {count > 1 && <span className="tl-row__count"> ×{count}</span>}
        </span>
        {item.code ? (
          <code className="tl-row__sub ui-truncate">{item.code}</code>
        ) : item.detail ? (
          <span className="tl-row__sub ui-truncate">{item.detail}</span>
        ) : null}
      </span>
      <span className="tl-row__time">{clock(row.lastAt)}</span>
    </button>
  )
}

const MemoRow = memo(Row)
const getKey = (r: TimelineRow) => r.item.id

export const EventTimeline = memo(function EventTimeline({ items }: { items: TimelineItem[] }) {
  const [cats, setCats] = useState<ReadonlySet<EventCategory>>(new Set())
  const [query, setQuery] = useState('')
  const [collapse, setCollapse] = useState(true)
  const [following, setFollowing] = useState(true)
  const [open, setOpen] = useState<TimelineRow | null>(null)
  const list = useRef<VirtualListHandle>(null)

  const counts = useMemo(() => countByCategory(items), [items])
  const rows = useMemo(() => toRows(filterTimeline(items, { categories: cats, query }), collapse), [items, cats, query, collapse])
  const openRow = useCallback((r: TimelineRow) => setOpen(r), [])
  const renderRow = useCallback((r: TimelineRow) => <MemoRow row={r} onOpen={openRow} />, [openRow])

  const toggle = (c: EventCategory) =>
    setCats(prev => {
      const next = new Set(prev)
      if (next.has(c)) next.delete(c)
      else next.add(c)
      return next
    })

  return (
    <div className="timeline">
      <div className="timeline__controls">
        <div className="timeline__chips" role="group" aria-label="Filter by type">
          {CATEGORY_ORDER.filter(c => counts[c] > 0).map(c => (
            <button key={c} type="button" className="chip" aria-pressed={cats.has(c)} onClick={() => toggle(c)}>
              {CATEGORY_LABELS[c]} <span className="chip__count">{counts[c]}</span>
            </button>
          ))}
          {cats.size > 0 && (
            <button type="button" className="chip chip--clear" onClick={() => setCats(new Set())}>
              Clear
            </button>
          )}
        </div>
        <div className="timeline__tools">
          <Input type="search" aria-label="Search events" placeholder="Search events" value={query} onChange={e => setQuery(e.target.value)} />
          <Switch checked={collapse} onChange={setCollapse} label="Group repeats" />
        </div>
      </div>

      {items.length === 0 ? (
        <p className="ui-muted timeline__empty">No events yet. They appear here as the run works.</p>
      ) : rows.length === 0 ? (
        <p className="ui-muted timeline__empty">No events match those filters.</p>
      ) : (
        <div className="timeline__frame">
          <VirtualList
            ref={list}
            items={rows}
            getHeight={rowHeight}
            getKey={getKey}
            renderRow={renderRow}
            label={`Run activity, ${rows.length} entries`}
            follow
            onFollowChange={setFollowing}
            className="timeline__list"
          />
          {!following && (
            <Button className="timeline__latest" size="sm" variant="primary" onClick={() => list.current?.scrollToEnd()}>
              Jump to latest
            </Button>
          )}
        </div>
      )}

      <Dialog open={!!open} onClose={() => setOpen(null)} title={open?.item.title ?? 'Event'} size="lg">
        {open && (
          <div className="form-stack">
            {open.item.detail && <p>{open.item.detail}</p>}
            {open.item.code && <CodeBlock code={open.item.code} label="Command" wrap />}
            <dl className="kv">
              <dt>When</dt>
              <dd>{new Date(open.item.at).toLocaleString()} ({relativeTime(open.item.at)})</dd>
              {open.item.phase && (
                <>
                  <dt>Phase</dt>
                  <dd>{phaseLabel(open.item.phase)}</dd>
                </>
              )}
              <dt>Event</dt>
              <dd>
                <code>{open.item.type}</code> #{open.item.id}
                {open.count > 1 ? `, and ${open.count - 1} similar` : ''}
              </dd>
            </dl>
            <CodeBlock code={JSON.stringify(open.item.event.payload ?? {}, null, 2)} label="Raw data" maxHeight={260} />
          </div>
        )}
      </Dialog>
    </div>
  )
})
