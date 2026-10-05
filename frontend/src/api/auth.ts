/** API token storage. The token lives in localStorage; local-mode servers never need one. */

const KEY = 'patchquest-token'

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY) || null
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(KEY, token)
    else localStorage.removeItem(KEY)
  } catch {
    // storage unavailable (private mode): the token only lasts for this page load
    memoryToken = token
  }
}

let memoryToken: string | null = null
export function currentToken(): string | null {
  return getToken() ?? memoryToken
}

/** Called by the API client whenever the server answers 401. Returns an unsubscribe function. */
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn)
  return () => unauthorizedListeners.delete(fn)
}

export function notifyUnauthorized(): void {
  unauthorizedListeners.forEach(fn => fn())
}

/**
 * Older pages call `fetch('/api/...')` directly. Wrap fetch once so those requests carry the token and a 401
 * opens the same sign-in prompt as everywhere else.
 */
export function installAuthFetch(): void {
  const w = window as unknown as { __pqFetchPatched?: boolean }
  if (w.__pqFetchPatched) return
  w.__pqFetchPatched = true
  const original = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
    if (!url.startsWith('/api')) return original(input, init)
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
    const token = currentToken()
    if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`)
    const res = await original(input, { ...init, headers })
    if (res.status === 401) notifyUnauthorized()
    return res
  }
}
