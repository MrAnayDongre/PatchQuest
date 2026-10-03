import { useEffect, useState } from 'react'

export type RouteName = 'home' | 'runs' | 'run' | 'engines' | 'settings' | 'extras' | 'not-found'

export interface Route {
  name: RouteName
  /** `id` for a run, `section` for extras. */
  params: Record<string, string>
  query: Record<string, string>
}

/** Parse a location hash such as `#/runs/abc?tab=changes`. Unknown paths resolve to `not-found`. */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '')
  const [pathPart, queryPart = ''] = raw.split('?')
  const segments = pathPart.split('/').filter(Boolean).map(safeDecode)
  const query: Record<string, string> = {}
  for (const [k, v] of new URLSearchParams(queryPart)) query[k] = v

  const [first, second, third] = segments
  if (!first) return { name: 'home', params: {}, query }
  if (first === 'runs' && !second) return { name: 'runs', params: {}, query }
  if (first === 'runs' && second && !third) return { name: 'run', params: { id: second }, query }
  if (first === 'engines' && !second) return { name: 'engines', params: {}, query }
  if (first === 'settings' && !second) return { name: 'settings', params: {}, query }
  if (first === 'extras' && !third) return { name: 'extras', params: second ? { section: second } : {}, query }
  return { name: 'not-found', params: {}, query }
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}

export function buildHash(name: RouteName, params: Record<string, string> = {}, query: Record<string, string> = {}): string {
  let path = '/'
  if (name === 'runs') path = '/runs'
  else if (name === 'run') path = `/runs/${encodeURIComponent(params.id ?? '')}`
  else if (name === 'engines') path = '/engines'
  else if (name === 'settings') path = '/settings'
  else if (name === 'extras') path = params.section ? `/extras/${encodeURIComponent(params.section)}` : '/extras'
  const qs = new URLSearchParams(query).toString()
  return `#${path}${qs ? `?${qs}` : ''}`
}

export function navigate(hash: string): void {
  if (window.location.hash === hash) {
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    return
  }
  window.location.hash = hash
}

export function runHash(id: string, tab?: string): string {
  return buildHash('run', { id }, tab ? { tab } : {})
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    onChange()
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
