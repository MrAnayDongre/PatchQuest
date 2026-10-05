import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, getRun, getRunEvents } from '../api/client'
import { openRunStream } from '../api/stream'
import type { Run } from '../api/types'
import { applyEvents, initialRunState, isTerminalStatus, type RunState } from '../lib/runState'

export type Connection = 'loading' | 'live' | 'reconnecting' | 'ended' | 'error'

export interface RunStream {
  run: Run | null
  state: RunState
  connection: Connection
  error: ApiError | null
  refreshRun: () => Promise<void>
  /** Open the stream again from the last applied event (after a resume, for example). */
  reconnect: () => void
}

const TERMINAL_EVENT_TYPES = new Set(['run_completed', 'run_failed', 'run_interrupted'])
const FLUSH_MS = 40
const BACKOFF = [1000, 2000, 4000, 8000, 15000]

/**
 * Load the persisted ledger first, then follow the live stream from the last id. A page refresh and a dropped
 * connection both end in exactly the state the reducer builds from the full ledger.
 */
export function useRunStream(runId: string): RunStream {
  const [run, setRun] = useState<Run | null>(null)
  const [state, setState] = useState<RunState>(initialRunState)
  const [connection, setConnection] = useState<Connection>('loading')
  const [error, setError] = useState<ApiError | null>(null)

  const lastId = useRef(0)
  // The reducer state lives in a ref as well, so the resume cursor is correct the instant events are applied.
  const stateRef = useRef<RunState>(state)
  const closeStream = useRef<(() => void) | null>(null)
  const generation = useRef(0)
  const timers = useRef<{ flush?: number; retry?: number; poll?: number }>({})
  const buffer = useRef<unknown[]>([])

  const apply = useCallback((events: unknown[]) => {
    const next = applyEvents(stateRef.current, events)
    if (next === stateRef.current) return
    stateRef.current = next
    lastId.current = next.lastId
    setState(next)
  }, [])

  const refreshRun = useCallback(async () => {
    try {
      setRun(await getRun(runId))
    } catch (err) {
      if (err instanceof ApiError) setError(err)
    }
  }, [runId])

  const connect = useCallback(
    (gen: number, attempt = 0) => {
      closeStream.current?.()
      if (gen !== generation.current) return
      const flush = () => {
        timers.current.flush = undefined
        const batch = buffer.current
        buffer.current = []
        if (batch.length) apply(batch)
      }
      const flushNow = () => {
        if (timers.current.flush !== undefined) window.clearTimeout(timers.current.flush)
        flush()
      }
      closeStream.current = openRunStream(runId, lastId.current, {
        onOpen: () => {
          if (gen === generation.current) setConnection('live')
        },
        onEvent: event => {
          if (gen !== generation.current) return
          buffer.current.push(event)
          if (TERMINAL_EVENT_TYPES.has(event.type)) {
            flushNow()
            closeStream.current?.()
            setConnection('ended')
            void (async () => {
              try {
                const fresh = await getRun(runId)
                if (gen !== generation.current) return
                setRun(fresh)
                // A worker can interrupt and resume a run within a moment: follow it again from the last event.
                if (event.type === 'run_interrupted' && fresh.status !== 'interrupted') {
                  setConnection('loading')
                  connect(gen)
                }
              } catch {
                // the page still shows what the ledger held
              }
            })()
            return
          }
          if (event.type === 'run_state_changed') void refreshRun()
          if (timers.current.flush === undefined) timers.current.flush = window.setTimeout(flush, FLUSH_MS)
        },
        onError: () => {
          if (gen !== generation.current) return
          flushNow()
          void (async () => {
            try {
              const fresh = await getRun(runId)
              if (gen !== generation.current) return
              setRun(fresh)
              // The server may simply have finished the stream; nothing more will come.
              if (isTerminalStatus(fresh.status) || fresh.status === 'interrupted') {
                // Pick up anything appended since the stream last delivered.
                const tail = await getRunEvents(runId, lastId.current)
                if (gen !== generation.current) return
                apply(tail)
                setConnection('ended')
                return
              }
            } catch (err) {
              if (gen !== generation.current) return
              if (err instanceof ApiError && err.status === 401) {
                setConnection('error')
                return
              }
            }
            setConnection('reconnecting')
            const delay = BACKOFF[Math.min(attempt, BACKOFF.length - 1)]
            timers.current.retry = window.setTimeout(() => {
              void (async () => {
                try {
                  apply(await getRunEvents(runId, lastId.current))
                } catch {
                  // the stream below will report the failure again
                }
                connect(gen, attempt + 1)
              })()
            }, delay)
          })()
        },
      })
    },
    [apply, refreshRun, runId],
  )

  const reconnect = useCallback(() => {
    const gen = generation.current
    if (timers.current.retry !== undefined) window.clearTimeout(timers.current.retry)
    setConnection('loading')
    connect(gen)
  }, [connect])

  useEffect(() => {
    const gen = ++generation.current
    lastId.current = 0
    buffer.current = []
    setRun(null)
    stateRef.current = initialRunState()
    setState(stateRef.current)
    setConnection('loading')
    setError(null)
    const timerBag = timers.current

    void (async () => {
      try {
        const [fresh, events] = await Promise.all([getRun(runId), getRunEvents(runId, 0)])
        if (gen !== generation.current) return
        setRun(fresh)
        const loaded = applyEvents(initialRunState(), events)
        lastId.current = loaded.lastId
        stateRef.current = loaded
        setState(loaded)
        if (isTerminalStatus(fresh.status) || fresh.status === 'interrupted') {
          setConnection('ended')
          return
        }
        connect(gen)
      } catch (err) {
        if (gen !== generation.current) return
        setError(err instanceof ApiError ? err : new ApiError(0, String(err)))
        setConnection('error')
      }
    })()

    return () => {
      generation.current++
      closeStream.current?.()
      closeStream.current = null
      for (const key of ['flush', 'retry', 'poll'] as const) {
        const id = timerBag[key]
        if (id !== undefined) window.clearTimeout(id)
        timerBag[key] = undefined
      }
    }
  }, [runId, connect])

  // An interrupted run has no stream. If somebody resumes it (here or elsewhere), pick it back up.
  useEffect(() => {
    if (connection !== 'ended' || run?.status !== 'interrupted') return
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const fresh = await getRun(runId)
          setRun(fresh)
          if (fresh.status !== 'interrupted') reconnect()
        } catch {
          // keep polling
        }
      })()
    }, 4000)
    return () => window.clearInterval(id)
  }, [connection, run?.status, runId, reconnect])

  return { run, state, connection, error, refreshRun, reconnect }
}
