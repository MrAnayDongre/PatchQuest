import { runStreamUrl } from './client'
import type { RunEvent } from './types'
import { isRunEvent } from '../lib/runState'

export interface RunStreamHandlers {
  onOpen: () => void
  onEvent: (event: RunEvent) => void
  onError: () => void
}

/**
 * Open the run's server-sent stream. Events are unnamed, so `onmessage` sees every type. Heartbeats and
 * malformed frames are ignored. We never rely on EventSource's own reconnect: its URL would keep the stale
 * `after_id`, so the caller closes this and opens a fresh stream from the last id it has applied.
 */
export function openRunStream(runId: string, afterId: number, handlers: RunStreamHandlers): () => void {
  const source = new EventSource(runStreamUrl(runId, afterId))
  let closed = false
  source.onopen = () => {
    if (!closed) handlers.onOpen()
  }
  source.onmessage = msg => {
    if (closed) return
    try {
      const data: unknown = JSON.parse(msg.data)
      if (isRunEvent(data)) handlers.onEvent(data)
    } catch {
      // ignore malformed frame
    }
  }
  source.onerror = () => {
    if (closed) return
    closed = true
    source.close()
    handlers.onError()
  }
  return () => {
    closed = true
    source.close()
  }
}
