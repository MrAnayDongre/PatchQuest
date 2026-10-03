// Node >= 25 defines an experimental global `localStorage` that is `undefined` unless
// --localstorage-file is passed, which shadows jsdom's implementation. Install a real
// in-memory Storage whenever the global is not usable so tests behave the same on every Node.
class MemoryStorage implements Storage {
  private data = new Map<string, string>()
  get length(): number {
    return this.data.size
  }
  clear(): void {
    this.data.clear()
  }
  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null
  }
  key(index: number): string | null {
    return Array.from(this.data.keys())[index] ?? null
  }
  removeItem(key: string): void {
    this.data.delete(key)
  }
  setItem(key: string, value: string): void {
    this.data.set(key, String(value))
  }
  [name: string]: unknown
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  const current = (globalThis as Record<string, unknown>)[name] as Storage | undefined
  if (!current || typeof current.clear !== 'function') {
    Object.defineProperty(globalThis, name, { value: new MemoryStorage(), configurable: true, writable: true })
  }
}

// jsdom does not implement scrolling; the app calls it on navigation.
if (typeof window !== 'undefined') window.scrollTo = (() => {}) as typeof window.scrollTo

// jsdom has no matchMedia; the theme provider asks for it.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}
