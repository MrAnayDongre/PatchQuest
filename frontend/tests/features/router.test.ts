import { describe, expect, it } from 'vitest'
import { buildHash, parseHash } from '../../src/lib/router'

describe('hash router', () => {
  it('parses the main routes', () => {
    expect(parseHash('').name).toBe('home')
    expect(parseHash('#/').name).toBe('home')
    expect(parseHash('#/runs').name).toBe('runs')
    expect(parseHash('#/engines').name).toBe('engines')
    expect(parseHash('#/settings').name).toBe('settings')
    expect(parseHash('#/metrics').name).toBe('metrics')
    expect(parseHash('#/extras').name).toBe('extras')
  })

  it('parses a run id and query', () => {
    const r = parseHash('#/runs/abc-123?tab=changes')
    expect(r).toEqual({ name: 'run', params: { id: 'abc-123' }, query: { tab: 'changes' } })
  })

  it('parses extras sections', () => {
    expect(parseHash('#/extras/calendar').params.section).toBe('calendar')
  })

  it('decodes encoded segments and survives malformed ones', () => {
    expect(parseHash('#/runs/a%20b').params.id).toBe('a b')
    expect(parseHash('#/runs/%E0%A4%A').params.id).toBe('%E0%A4%A')
  })

  it('falls back to not-found for unknown paths', () => {
    expect(parseHash('#/nope').name).toBe('not-found')
    expect(parseHash('#/runs/a/b').name).toBe('not-found')
  })

  it('builds hashes that parse back to the same route', () => {
    const h = buildHash('run', { id: 'x/y' }, { tab: 'recovery' })
    expect(parseHash(h)).toEqual({ name: 'run', params: { id: 'x/y' }, query: { tab: 'recovery' } })
    expect(buildHash('home')).toBe('#/')
    expect(buildHash('extras', { section: 'games' })).toBe('#/extras/games')
  })
})
