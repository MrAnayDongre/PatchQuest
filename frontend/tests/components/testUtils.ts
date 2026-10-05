import { vi } from 'vitest'

export interface MockRoute {
  match: RegExp
  method?: string
  status?: number
  body: unknown | ((url: string, init?: RequestInit) => unknown)
}

/** Install a fetch mock driven by a route table. Returns the recorded calls. */
export function mockFetch(routes: MockRoute[]) {
  const calls: { url: string; method: string; body: unknown }[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url, method, body })
    const route = routes.find(r => r.match.test(url) && (r.method ?? 'GET') === method)
    if (!route) return new Response(JSON.stringify({ detail: `no mock for ${method} ${url}` }), { status: 599 })
    const payload = typeof route.body === 'function' ? (route.body as (u: string, i?: RequestInit) => unknown)(url, init) : route.body
    return new Response(JSON.stringify(payload), { status: route.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return calls
}

export class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  close() {
    this.closed = true
  }
  open() {
    this.onopen?.()
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }
  fail() {
    this.onerror?.()
  }
  static reset() {
    FakeEventSource.instances = []
  }
  static get last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }
}
