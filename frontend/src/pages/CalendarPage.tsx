import { useState, useEffect } from 'react'
import PixelPanel from '../components/PixelPanel'
import PixelButton from '../components/PixelButton'
import StatusBadge from '../components/StatusBadge'
import RunActionButtons from '../components/RunActionButtons'
import { extractRunId, formatRunWhen } from '../lib/runId'

interface CalendarProvider {
  name: string
  available: boolean
  message: string
}

interface CalendarEvent {
  id: string
  calendar_id: string
  title: string
  description: string
  start_at: string
  end_at: string
  timezone: string
  location: string | null
  source_provider: string
  scheduled_task_id: number | null
  reminder_minutes: number | null
  patchquest_run_id?: string | null
}

interface CalendarStatus {
  enabled: boolean
  default_provider: string
  create_events_for_tasks: boolean
  avoid_busy_times: boolean
  providers: CalendarProvider[]
}

interface Props {
  onOpenConsole: (runId: string) => void
  onViewReport: (runId: string) => void
}

export default function CalendarPage({ onOpenConsole, onViewReport }: Props) {
  const [status, setStatus] = useState<CalendarStatus | null>(null)
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [title, setTitle] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [description, setDescription] = useState('')

  const fetchStatus = () => {
    fetch('/api/calendar/status').then(r => r.json()).then(setStatus).catch(() => {})
  }

  const fetchEvents = () => {
    const now = new Date()
    const start = new Date(now.getTime() - 30 * 86400000).toISOString()
    const end = new Date(now.getTime() + 365 * 86400000).toISOString()
    fetch(`/api/calendar/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`)
      .then(r => r.json()).then(setEvents).catch(() => {})
  }

  useEffect(() => { fetchStatus(); fetchEvents() }, [])

  const handleCreate = async () => {
    setError(null)
    try {
      const res = await fetch('/api/calendar/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, start_at: startAt, end_at: endAt, description }),
      })
      if (!res.ok) throw new Error(await res.text())
      setShowForm(false)
      setTitle(''); setStartAt(''); setEndAt(''); setDescription('')
      fetchEvents()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    }
  }

  const handleDelete = async (eventId: string) => {
    await fetch(`/api/calendar/events/${eventId}`, { method: 'DELETE' })
    fetchEvents()
  }

  const handleExport = () => {
    window.open('/api/calendar/export-ics', '_blank')
  }

  const resolveRunId = (ev: CalendarEvent): string | null =>
    ev.patchquest_run_id || extractRunId(ev.description, ev.title)

  return (
    <div className="launch-panel" style={{ maxWidth: '960px' }}>
      <h1 className="page-title">Calendar Deck</h1>
      <p className="page-subtitle">Scheduled PatchQuest runs appear here — open the linked mission or report directly.</p>

      <PixelPanel title="Calendar Providers">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '0.5rem', marginTop: '0.5rem' }}>
          {status?.providers.map(p => (
            <div key={p.name} style={{ padding: '0.5rem', border: '1px solid var(--border-color)', borderRadius: '6px', fontSize: '0.75rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{p.name}</span>
                <StatusBadge status={p.available ? 'complete' : 'failed'} />
              </div>
              {!p.available && <div style={{ color: 'var(--accent-amber)', marginTop: '0.2rem', fontSize: '0.7rem' }}>{p.message}</div>}
            </div>
          ))}
        </div>
      </PixelPanel>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1rem', marginBottom: '0.5rem' }}>
        <h2 className="section-title" style={{ margin: 0 }}>Events</h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <PixelButton variant="ghost" onClick={fetchEvents}>Refresh</PixelButton>
          <PixelButton variant="secondary" onClick={handleExport}>Export ICS</PixelButton>
          <PixelButton onClick={() => setShowForm(!showForm)}>{showForm ? 'Cancel' : '+ Event'}</PixelButton>
        </div>
      </div>

      {showForm && (
        <PixelPanel glow style={{ marginBottom: '1rem' }}>
          <div className="launch-form">
            <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Event title" />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
              <input type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} />
              <input type="datetime-local" value={endAt} onChange={e => setEndAt(e.target.value)} />
            </div>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} placeholder="Description" />
            {error && <div className="launch-alert launch-alert--error">{error}</div>}
            <PixelButton onClick={handleCreate} disabled={!title || !startAt || !endAt}>Create</PixelButton>
          </div>
        </PixelPanel>
      )}

      <PixelPanel>
        {events.length === 0 && (
          <div className="empty-state" style={{ marginTop: 0 }}>
            <p className="empty-state__body">No calendar events. Schedule a task with calendar events enabled.</p>
          </div>
        )}
        {events.map(ev => {
          const runId = resolveRunId(ev)
          return (
            <div key={ev.id} className="calendar-event">
              <div className="calendar-event__header">
                <span className="calendar-event__title">{ev.title}</span>
                <PixelButton variant="danger" className="pixel-btn--sm" onClick={() => handleDelete(ev.id)}>Delete</PixelButton>
              </div>
              <div className="calendar-event__when">
                {formatRunWhen(ev.start_at)} — {formatRunWhen(ev.end_at)}
              </div>
              {ev.description && <div className="calendar-event__desc">{ev.description}</div>}
              <div className="calendar-event__meta">
                via {ev.source_provider}
                {ev.scheduled_task_id != null && ` · task #${ev.scheduled_task_id}`}
                {ev.reminder_minutes != null && ` · reminder ${ev.reminder_minutes}m`}
              </div>
              {runId && (
                <div className="calendar-event__actions">
                  <RunActionButtons
                    runId={runId}
                    onOpenConsole={onOpenConsole}
                    onViewReport={onViewReport}
                    compact
                    showRunId
                  />
                </div>
              )}
            </div>
          )
        })}
      </PixelPanel>
    </div>
  )
}
