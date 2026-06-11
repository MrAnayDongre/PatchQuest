import { useState, useEffect } from 'react'
import PixelPanel from '../components/PixelPanel'
import { getRunEvents } from '../api/client'
import type { RunEvent } from '../api/types'

interface Props {
  runId: string | null
}

export default function SafetyQueuePage({ runId }: Props) {
  const [events, setEvents] = useState<RunEvent[]>([])

  useEffect(() => {
    if (!runId) return
    getRunEvents(runId).then(setEvents).catch(() => {})
  }, [runId])

  const approvalEvents = events.filter(e =>
    e.type.includes('permission') || e.type.includes('secret') || e.type.includes('blocked')
  )

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <h1 className="page-title">Warden — Safety Queue</h1>

      <PixelPanel title="Security Events">
        {approvalEvents.length === 0 && (
          <div style={{ color: 'var(--text-dim)', textAlign: 'center', padding: '2rem' }}>
            No security events recorded.
          </div>
        )}
        {approvalEvents.map((e, i) => (
          <div key={i} style={{ padding: '0.5rem', borderBottom: '1px solid var(--border-color)', fontSize: '0.8rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: e.type.includes('secret') ? 'var(--accent-red)' : 'var(--accent-yellow)' }}>
                {e.type.replace(/_/g, ' ')}
              </span>
              <span style={{ color: 'var(--text-dim)', fontSize: '0.7rem' }}>{e.created_at}</span>
            </div>
            <div style={{ color: 'var(--text-secondary)', marginTop: '0.2rem' }}>{e.message}</div>
          </div>
        ))}
      </PixelPanel>
    </div>
  )
}
