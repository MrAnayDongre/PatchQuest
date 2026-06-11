import { useState, useEffect } from 'react'
import PixelPanel from '../components/PixelPanel'
import PixelButton from '../components/PixelButton'
import StatusBadge from '../components/StatusBadge'

interface SearchProvider {
  name: string
  available: boolean
  message: string
  requires_api_key: boolean
}

interface SearchResult {
  id: string
  title: string
  url: string
  snippet: string
  source: string
  score: number | null
}

interface SearchStatus {
  enabled: boolean
  default_provider: string
  allow_network: string
  providers: SearchProvider[]
  cache: { total: number; expired: number; active: number }
}

export default function SearchPage() {
  const [status, setStatus] = useState<SearchStatus | null>(null)
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [answer, setAnswer] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchStatus = () => {
    fetch('/api/search/status').then(r => r.json()).then(setStatus).catch(() => {})
  }

  useEffect(() => { fetchStatus() }, [])

  const handleSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    setError(null)
    setResults([])
    setAnswer(null)
    try {
      const res = await fetch('/api/search/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, provider: provider || undefined, max_results: 8 }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setResults(data.results || [])
      setAnswer(data.answer || null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  const handleClearCache = async () => {
    await fetch('/api/search/cache', { method: 'DELETE' })
    fetchStatus()
  }

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto' }}>
      <h1 className="page-title">Research Lab</h1>

      <PixelPanel title="Search Providers">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.5rem', marginTop: '0.5rem' }}>
          {status?.providers.map(p => (
            <div key={p.name} style={{ padding: '0.5rem', border: '1px solid var(--border-color)', fontSize: '0.75rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: 'var(--accent-cyan)', textTransform: 'uppercase' }}>{p.name}</span>
                <StatusBadge status={p.available ? 'complete' : 'failed'} />
              </div>
              <div style={{ color: 'var(--text-dim)', marginTop: '0.25rem' }}>
                {p.requires_api_key ? 'API key required' : 'No key needed'}
              </div>
              {!p.available && <div style={{ color: 'var(--accent-yellow)', marginTop: '0.2rem' }}>{p.message}</div>}
              {p.name === status?.default_provider && (
                <div style={{ color: 'var(--accent-green)', marginTop: '0.2rem' }}>Default</div>
              )}
            </div>
          ))}
        </div>
        {status && (
          <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            Network: <span style={{ color: 'var(--accent-cyan)' }}>{status.allow_network}</span>
            {' | Cache: '}
            <span style={{ color: 'var(--accent-green)' }}>{status.cache.active} active</span>
            {' / '}{status.cache.total} total
            {status.cache.total > 0 && (
              <PixelButton variant="ghost" onClick={handleClearCache} style={{ marginLeft: '0.5rem' }}>
                Clear Cache
              </PixelButton>
            )}
          </div>
        )}
      </PixelPanel>

      <PixelPanel title="Test Search" style={{ marginTop: '1rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search query..."
            style={{ flex: 1 }}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
          />
          <select value={provider} onChange={e => setProvider(e.target.value)} style={{ width: '150px' }}>
            <option value="">Default</option>
            {status?.providers.filter(p => p.available).map(p => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
          <PixelButton onClick={handleSearch} disabled={searching || !query.trim()}>
            {searching ? 'Searching...' : 'Search'}
          </PixelButton>
        </div>

        {error && <div style={{ color: 'var(--accent-red)', marginTop: '0.5rem', fontSize: '0.8rem' }}>{error}</div>}

        {answer && (
          <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: 'rgba(0,255,136,0.05)', border: '1px solid var(--accent-green)', fontSize: '0.8rem' }}>
            <div style={{ color: 'var(--accent-green)', fontWeight: 'bold', marginBottom: '0.25rem' }}>Answer</div>
            {answer}
          </div>
        )}

        {results.length > 0 && (
          <div style={{ marginTop: '0.75rem' }}>
            {results.map((r, i) => (
              <div key={r.id || i} style={{ padding: '0.5rem', borderBottom: '1px solid var(--border-color)', fontSize: '0.8rem' }}>
                <a href={r.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-cyan)', textDecoration: 'none' }}>
                  {r.title}
                </a>
                <div style={{ color: 'var(--text-dim)', marginTop: '0.2rem', fontSize: '0.7rem' }}>{r.url}</div>
                <div style={{ color: 'var(--text-secondary)', marginTop: '0.2rem' }}>{r.snippet}</div>
                <div style={{ color: 'var(--text-dim)', marginTop: '0.15rem', fontSize: '0.65rem' }}>
                  via {r.source}{r.score != null ? ` | score: ${r.score}` : ''}
                </div>
              </div>
            ))}
          </div>
        )}
      </PixelPanel>

      <PixelPanel title="Configuration" style={{ marginTop: '1rem' }}>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
          <p>Configure search providers in <code>config.yaml</code> under the <code>search</code> section.</p>
          <p style={{ marginTop: '0.5rem' }}>Set API keys as environment variables (never stored in config):</p>
          <ul style={{ paddingLeft: '1rem', marginTop: '0.25rem' }}>
            <li><code>BRAVE_SEARCH_API_KEY</code> - Brave Search</li>
            <li><code>TAVILY_API_KEY</code> - Tavily</li>
            <li><code>SERPER_API_KEY</code> - Serper</li>
            <li><code>SERPAPI_API_KEY</code> - SerpApi</li>
            <li><code>GOOGLE_SEARCH_API_KEY</code> + <code>GOOGLE_SEARCH_ENGINE_ID</code> - Google</li>
          </ul>
          <p style={{ marginTop: '0.5rem', color: 'var(--accent-yellow)' }}>
            DuckDuckGo works without an API key (instant answers only, best-effort).
          </p>
        </div>
      </PixelPanel>
    </div>
  )
}
