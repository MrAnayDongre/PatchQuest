import { useEffect, useMemo, useRef, useState } from 'react'
import type { RunEvent } from '../api/types'

interface EventConsoleProps {
  events: RunEvent[]
}

function eventTone(type: string): string {
  if (type.includes('failed') || type === 'secret_detected') return 'error'
  if (type.includes('skipped')) return 'muted'
  if (type.includes('completed') || type === 'run_completed' || type === 'patch_applied') return 'success'
  if (type.includes('started') || type === 'run_created') return 'info'
  if (type.includes('permission')) return 'warn'
  return 'default'
}

export default function EventConsole({ events }: EventConsoleProps) {
  const [autoScroll, setAutoScroll] = useState(true)
  const [filter, setFilter] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return events
    return events.filter(e =>
      (e.type || '').toLowerCase().includes(q)
      || (e.phase || '').toLowerCase().includes(q)
      || (e.message || '').toLowerCase().includes(q),
    )
  }, [events, filter])

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [filtered, autoScroll])

  const copyEvents = () => {
    const text = filtered.map(e =>
      `[${e.created_at || '—'}] ${e.phase || '—'} ${e.type}: ${e.message || ''}`,
    ).join('\n')
    navigator.clipboard.writeText(text)
  }

  if (events.length === 0) {
    return (
      <div className="event-console event-console--empty">
        <p>Waiting for orchestrator events…</p>
        <p className="event-console__hint">Phase transitions, analysis output, and patch events stream here in real time.</p>
      </div>
    )
  }

  return (
    <div className="event-console">
      <div className="event-console__toolbar">
        <input
          type="search"
          className="event-console__filter"
          placeholder="Filter events…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          aria-label="Filter events"
        />
        <label className="event-console__toggle">
          <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
          Auto-scroll
        </label>
        <button type="button" className="pixel-btn pixel-btn--ghost pixel-btn--sm" onClick={copyEvents}>
          Copy
        </button>
      </div>
      <div className="event-console__stream" role="log" aria-live="polite">
        {filtered.map((e, i) => (
          <div key={e.id ?? i} className={`event-line event-line--${eventTone(e.type)}`}>
            <span className="event-line__time">{e.created_at?.slice(11, 19) || '—'}</span>
            <span className="event-line__phase">{e.phase || '—'}</span>
            <span className="event-line__type">{e.type}</span>
            <span className="event-line__msg">{e.message || ''}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
