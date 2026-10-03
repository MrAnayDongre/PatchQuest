/** Windowing math for variable-height virtual lists (heights are known up front, never measured). */

export interface Layout {
  /** offsets[i] is the top of row i; offsets[n] is the total height. */
  offsets: number[]
  total: number
}

export function buildLayout(heights: number[]): Layout {
  const offsets = new Array<number>(heights.length + 1)
  let acc = 0
  for (let i = 0; i < heights.length; i++) {
    offsets[i] = acc
    acc += heights[i]
  }
  offsets[heights.length] = acc
  return { offsets, total: acc }
}

/** Index of the row containing `y` (clamped to the valid range). */
export function indexAtOffset(layout: Layout, y: number): number {
  const n = layout.offsets.length - 1
  if (n <= 0) return 0
  if (y <= 0) return 0
  if (y >= layout.total) return n - 1
  let lo = 0
  let hi = n - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (layout.offsets[mid] <= y) lo = mid
    else hi = mid - 1
  }
  return lo
}

export interface WindowRange {
  start: number
  /** exclusive */
  end: number
}

/** Rows that intersect [scrollTop, scrollTop + viewport), widened by `overscan` rows on both sides. */
export function visibleRange(layout: Layout, scrollTop: number, viewport: number, overscan = 6): WindowRange {
  const n = layout.offsets.length - 1
  if (n <= 0) return { start: 0, end: 0 }
  const first = indexAtOffset(layout, scrollTop)
  let last = indexAtOffset(layout, scrollTop + Math.max(0, viewport) - 1)
  if (last < first) last = first
  return { start: Math.max(0, first - overscan), end: Math.min(n, last + 1 + overscan) }
}

/** True when the viewport is within `threshold` px of the bottom (used for "follow latest"). */
export function isNearBottom(scrollTop: number, viewport: number, total: number, threshold = 48): boolean {
  return total - (scrollTop + viewport) <= threshold
}
