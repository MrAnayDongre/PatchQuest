import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/errors'
import { validateWorkflow } from '../api/workflows'
import type { Problem, WfDef } from '../lib/workflow/types'

export interface ServerVerdict {
  /** the exact definition object that was checked */
  def: WfDef
  ok: boolean
  problems: Problem[]
}

/**
 * Ask the server whether `def` is valid, a moment after the last edit. Never blocks editing. `verdict` always
 * belongs to an older or equal definition; compare `verdict.def === def` before trusting it for the current one.
 */
export function useServerValidation(def: WfDef, enabled = true, delay = 450): { verdict: ServerVerdict | null; checking: boolean; error: ApiError | null } {
  const [verdict, setVerdict] = useState<ServerVerdict | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const ticket = useRef(0)
  useEffect(() => {
    if (!enabled) return
    const mine = ++ticket.current
    setChecking(true)
    const ctl = new AbortController()
    const t = window.setTimeout(() => {
      validateWorkflow(def, ctl.signal)
        .then(r => {
          if (mine !== ticket.current) return
          setVerdict({ def, ok: r.ok, problems: r.problems.map(p => ({ ...p, source: 'server' as const })) })
          setError(null)
        })
        .catch(e => {
          if (mine !== ticket.current || (e instanceof DOMException && e.name === 'AbortError')) return
          setError(e instanceof ApiError ? e : new ApiError(0, String(e)))
        })
        .finally(() => {
          if (mine === ticket.current) setChecking(false)
        })
    }, delay)
    return () => {
      window.clearTimeout(t)
      ctl.abort()
    }
  }, [def, enabled, delay])
  return { verdict, checking, error }
}

export function useNarrow(query = '(max-width: 639px)'): boolean {
  const get = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false)
  const [narrow, setNarrow] = useState(get)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(query)
    const on = () => setNarrow(mq.matches)
    on()
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [query])
  return narrow
}
