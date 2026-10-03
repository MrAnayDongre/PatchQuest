import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/errors'

export interface AsyncState<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  refresh: () => Promise<void>
}

function toApiError(err: unknown): ApiError {
  return err instanceof ApiError ? err : new ApiError(0, err instanceof Error ? err.message : String(err))
}

/** Load something once (and again on `refresh` or when `deps` change). Stale responses are discarded. */
export function useAsync<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[], enabled = true): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(enabled)
  const ticket = useRef(0)
  const loadRef = useRef(load)
  loadRef.current = load
  const controller = useRef<AbortController | null>(null)

  const run = useCallback(async () => {
    controller.current?.abort()
    const ctl = new AbortController()
    controller.current = ctl
    const mine = ++ticket.current
    setLoading(true)
    try {
      const result = await loadRef.current(ctl.signal)
      if (mine !== ticket.current) return
      setData(result)
      setError(null)
    } catch (err) {
      if (mine !== ticket.current || (err instanceof DOMException && err.name === 'AbortError')) return
      setError(toApiError(err))
    } finally {
      if (mine === ticket.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    void run()
    return () => {
      ticket.current++
      controller.current?.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, run, ...deps])

  return { data, error, loading, refresh: run }
}

/** Call `fn` every `ms` while enabled and the tab is visible; also fires when the tab becomes visible again. */
export function usePolling(fn: () => void, ms: number, enabled = true): void {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    if (!enabled) return
    const tick = () => {
      if (document.visibilityState !== 'hidden') ref.current()
    }
    const id = window.setInterval(tick, ms)
    document.addEventListener('visibilitychange', tick)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [ms, enabled])
}
