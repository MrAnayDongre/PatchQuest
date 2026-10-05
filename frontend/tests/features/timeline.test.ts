import { describe, expect, it } from 'vitest'
import { reduceEvents } from '../../src/lib/runState'
import { countByCategory, filterTimeline, rowHeight, ROW_HEIGHT, ROW_HEIGHT_WITH_DETAIL, toRows } from '../../src/lib/timeline'
import type { EventCategory } from '../../src/lib/eventCopy'
import { ev, happyRun } from './helpers'

const none = new Set<EventCategory>()

describe('timeline filtering', () => {
  const items = reduceEvents(happyRun()).timeline

  it('shows everything with no filter', () => {
    expect(filterTimeline(items, { categories: none, query: '' })).toHaveLength(items.length)
  })

  it('filters by category', () => {
    const only = filterTimeline(items, { categories: new Set<EventCategory>(['approval']), query: '' })
    expect(only.map(i => i.type)).toEqual(['approval_requested', 'approval_decided'])
  })

  it('combines several categories', () => {
    const r = filterTimeline(items, { categories: new Set<EventCategory>(['command', 'validation']), query: '' })
    expect(new Set(r.map(i => i.category))).toEqual(new Set(['command', 'validation']))
  })

  it('searches titles, details and commands', () => {
    expect(filterTimeline(items, { categories: none, query: 'pytest' }).length).toBeGreaterThan(0)
    expect(filterTimeline(items, { categories: none, query: 'zzzz-no-match' })).toHaveLength(0)
  })

  it('counts events per category', () => {
    const c = countByCategory(items)
    expect(c.approval).toBe(2)
    expect(c.model).toBe(1)
  })
})

describe('collapsing repeats', () => {
  const calls = reduceEvents([
    ev(1, 'model_call', { payload: { role: 'coder', duration_ms: 100 } }),
    ev(2, 'model_call', { payload: { role: 'coder', duration_ms: 900 } }),
    ev(3, 'model_call', { payload: { role: 'coder', duration_ms: 300 } }),
    ev(4, 'model_call', { payload: { role: 'planner', duration_ms: 300 } }),
    ev(5, 'model_call', { payload: { role: 'coder', duration_ms: 300 } }),
  ]).timeline

  it('merges consecutive identical events only', () => {
    const rows = toRows(calls, true)
    expect(rows.map(r => r.count)).toEqual([3, 1, 1])
    expect(rows[0].items.map(i => i.id)).toEqual([1, 2, 3])
  })

  it('keeps every row when collapsing is off', () => {
    expect(toRows(calls, false)).toHaveLength(5)
  })

  it('gives rows with details extra height', () => {
    const rows = toRows(calls, false)
    expect(rowHeight(rows[0])).toBe(ROW_HEIGHT_WITH_DETAIL)
    const bare = toRows(reduceEvents([ev(1, 'workspace_created')]).timeline, false)
    expect(rowHeight(bare[0])).toBe(ROW_HEIGHT)
  })
})
