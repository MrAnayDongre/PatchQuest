import { describe, expect, it } from 'vitest'
import { buildLayout, indexAtOffset, isNearBottom, visibleRange } from '../../src/lib/virtual'

describe('virtual windowing', () => {
  const heights = [40, 62, 40, 40, 62, 40, 40, 40, 40, 40]
  const layout = buildLayout(heights)

  it('builds prefix offsets', () => {
    expect(layout.offsets.slice(0, 4)).toEqual([0, 40, 102, 142])
    expect(layout.total).toBe(444)
  })

  it('finds the row at an offset, including row boundaries and clamping', () => {
    expect(indexAtOffset(layout, 0)).toBe(0)
    expect(indexAtOffset(layout, 39)).toBe(0)
    expect(indexAtOffset(layout, 40)).toBe(1)
    expect(indexAtOffset(layout, 101)).toBe(1)
    expect(indexAtOffset(layout, 102)).toBe(2)
    expect(indexAtOffset(layout, 99999)).toBe(9)
    expect(indexAtOffset(layout, -5)).toBe(0)
  })

  it('returns only the visible rows plus overscan', () => {
    expect(visibleRange(layout, 0, 80, 0)).toEqual({ start: 0, end: 2 })
    expect(visibleRange(layout, 150, 100, 1)).toEqual({ start: 2, end: 7 })
  })

  it('clamps overscan at both ends', () => {
    expect(visibleRange(layout, 0, 50, 10)).toEqual({ start: 0, end: 10 })
    expect(visibleRange(layout, 10_000, 100, 2)).toEqual({ start: 7, end: 10 })
  })

  it('handles an empty list', () => {
    expect(visibleRange(buildLayout([]), 0, 400)).toEqual({ start: 0, end: 0 })
  })

  it('renders a small window for a very large list', () => {
    const big = buildLayout(new Array(50_000).fill(40))
    const r = visibleRange(big, 40 * 25_000, 600, 6)
    expect(r.end - r.start).toBeLessThanOrEqual(15 + 12)
    expect(r.start).toBeGreaterThan(24_000)
  })

  it('detects when the viewport sits at the bottom', () => {
    expect(isNearBottom(344, 100, 444)).toBe(true)
    expect(isNearBottom(100, 100, 444)).toBe(false)
  })
})
