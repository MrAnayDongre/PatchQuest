import type { Pos, Rect } from './types'

/** world -> screen: sx = wx * zoom + x */
export interface Viewport {
  x: number
  y: number
  zoom: number
}

export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 2

export const clampZoom = (z: number): number => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z))

export const screenToWorld = (vp: Viewport, p: Pos): Pos => ({ x: (p.x - vp.x) / vp.zoom, y: (p.y - vp.y) / vp.zoom })
export const worldToScreen = (vp: Viewport, p: Pos): Pos => ({ x: p.x * vp.zoom + vp.x, y: p.y * vp.zoom + vp.y })

/** Zoom by `factor` keeping the world point under `anchor` (screen coordinates) fixed. */
export function zoomAt(vp: Viewport, factor: number, anchor: Pos): Viewport {
  const zoom = clampZoom(vp.zoom * factor)
  const k = zoom / vp.zoom
  return { zoom, x: anchor.x - (anchor.x - vp.x) * k, y: anchor.y - (anchor.y - vp.y) * k }
}

export const panBy = (vp: Viewport, dx: number, dy: number): Viewport => ({ ...vp, x: vp.x + dx, y: vp.y + dy })

/** Mouse wheel / trackpad delta to a zoom factor. */
export function wheelFactor(deltaY: number, deltaMode = 0): number {
  const px = deltaMode === 1 ? deltaY * 16 : deltaY
  return Math.exp(-px * 0.0015)
}

export function fitToRect(rect: Rect | null, size: { w: number; h: number }, padding = 48): Viewport {
  if (!rect || size.w <= 0 || size.h <= 0) return { x: padding, y: padding, zoom: 1 }
  const zoom = clampZoom(Math.min((size.w - 2 * padding) / Math.max(rect.w, 1), (size.h - 2 * padding) / Math.max(rect.h, 1), 1))
  return { zoom, x: (size.w - rect.w * zoom) / 2 - rect.x * zoom, y: (size.h - rect.h * zoom) / 2 - rect.y * zoom }
}

/** Two-finger gesture: scale by the change in distance and pan by the movement of the midpoint. */
export function pinch(vp: Viewport, prev: [Pos, Pos], next: [Pos, Pos]): Viewport {
  const d0 = Math.hypot(prev[0].x - prev[1].x, prev[0].y - prev[1].y) || 1
  const d1 = Math.hypot(next[0].x - next[1].x, next[0].y - next[1].y)
  const m0 = { x: (prev[0].x + prev[1].x) / 2, y: (prev[0].y + prev[1].y) / 2 }
  const m1 = { x: (next[0].x + next[1].x) / 2, y: (next[0].y + next[1].y) / 2 }
  const zoomed = zoomAt(vp, d1 / d0, m0)
  return panBy(zoomed, m1.x - m0.x, m1.y - m0.y)
}

/** Does a screen-space marquee (two corners) intersect a world rect? */
export function marqueeHits(vp: Viewport, a: Pos, b: Pos, rect: Rect): boolean {
  const w1 = screenToWorld(vp, { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y) })
  const w2 = screenToWorld(vp, { x: Math.max(a.x, b.x), y: Math.max(a.y, b.y) })
  return rect.x < w2.x && rect.x + rect.w > w1.x && rect.y < w2.y && rect.y + rect.h > w1.y
}
