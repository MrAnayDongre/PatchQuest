import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as RKeyboardEvent, type PointerEvent as RPointerEvent } from 'react'
import { Icon } from '../../design/icons'
import { IconButton } from '../../design/primitives'
import { labelText } from '../../lib/workflow/edgeRules'
import { NODE_COPY } from '../../lib/workflow/copy'
import type { ActionMeta } from '../../lib/workflow/clientCheck'
import { alignGuides, boundsOf, edgeMid, edgePath, GRID, inPort, NODE_H, NODE_W, outPort, rectOf, snapPos, type Guide } from '../../lib/workflow/layout'
import { VISUAL_COPY, type NodeVisual } from '../../lib/workflow/run'
import { isNodeType, type NodeType, type Pos, type Problem, type WfDef } from '../../lib/workflow/types'
import { fitToRect, marqueeHits, panBy, pinch, screenToWorld, wheelFactor, zoomAt, type Viewport } from '../../lib/workflow/viewport'
import { actionOf, nodeSummary } from './summary'

export const edgeKey = (e: { from: string; to: string; when?: string | null }): string => `${e.from}->${e.to}:${e.when ?? ''}`

interface CanvasProps {
  def: WfDef
  positions: Record<string, Pos>
  selection: ReadonlySet<string>
  selectedEdge?: string | null
  readOnly?: boolean
  problems?: Map<string, Problem[]>
  visuals?: Record<string, NodeVisual>
  highlight?: ReadonlySet<string>
  actions?: Map<string, ActionMeta>
  viewport: Viewport
  onViewport: (vp: Viewport) => void
  /** bumped by the parent to ask for "fit to screen" */
  fitSignal?: number
  onSelect?: (ids: string[], additive: boolean) => void
  onSelectEdge?: (key: string | null) => void
  onMoveEnd?: (positions: Record<string, Pos>) => void
  onConnect?: (from: string, to: string) => void
  onOpenConfig?: (id: string) => void
  onDropNew?: (type: NodeType, at: Pos) => void
  onKeyDown?: (e: RKeyboardEvent<HTMLDivElement>) => void
  label?: string
}

interface DragState {
  id: string
  startClient: Pos
  starts: Record<string, Pos>
  moved: boolean
  additive: boolean
  wasSelected: boolean
}

const tone = (v: NodeVisual | undefined) => (v ? `wf-node--${v}` : '')

function CanvasImpl(p: CanvasProps) {
  const { def, positions, selection, readOnly, viewport, onViewport } = p
  const box = useRef<HTMLDivElement>(null)
  const [preview, setPreview] = useState<Record<string, Pos> | null>(null)
  const [guides, setGuides] = useState<Guide[]>([])
  const [link, setLink] = useState<{ from: string; to: Pos } | null>(null)
  const [marquee, setMarquee] = useState<{ a: Pos; b: Pos } | null>(null)
  const drag = useRef<DragState | null>(null)
  const pointers = useRef(new Map<number, Pos>())
  const pan = useRef<{ last: Pos } | null>(null)
  const vpRef = useRef(viewport)
  vpRef.current = viewport

  const pos = useCallback((id: string): Pos => preview?.[id] ?? positions[id] ?? { x: 0, y: 0 }, [preview, positions])

  const local = (e: { clientX: number; clientY: number }): Pos => {
    const r = box.current?.getBoundingClientRect()
    return { x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) }
  }

  // wheel zoom needs a non-passive listener to stop the page from scrolling
  useEffect(() => {
    const el = box.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect()
      const anchor = { x: e.clientX - r.left, y: e.clientY - r.top }
      if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) >= Math.abs(e.deltaX)) onViewport(zoomAt(vpRef.current, wheelFactor(e.deltaY, e.deltaMode), anchor))
      else onViewport(panBy(vpRef.current, -e.deltaX, 0))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [onViewport])

  const bounds = useMemo(() => boundsOf(def.nodes.map(n => rectOf(positions[n.id] ?? { x: 0, y: 0 }))), [def.nodes, positions])
  const fit = useCallback(() => {
    const r = box.current?.getBoundingClientRect()
    onViewport(fitToRect(bounds, { w: r?.width || 800, h: r?.height || 500 }))
  }, [bounds, onViewport])
  const lastFit = useRef(p.fitSignal)
  useLayoutEffect(() => {
    if (p.fitSignal !== lastFit.current) {
      lastFit.current = p.fitSignal
      fit()
    }
  }, [p.fitSignal, fit])

  // ---- background: pan, pinch, marquee
  const onBgDown = (e: RPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('[data-node-id], [data-canvas-control]')) return
    box.current?.focus({ preventScroll: true })
    pointers.current.set(e.pointerId, local(e))
    e.currentTarget.setPointerCapture?.(e.pointerId)
    if (pointers.current.size === 1) {
      if (e.shiftKey && !readOnly) setMarquee({ a: local(e), b: local(e) })
      else pan.current = { last: local(e) }
      p.onSelectEdge?.(null)
    }
  }
  const onBgMove = (e: RPointerEvent<HTMLDivElement>) => {
    const now = local(e)
    if (pointers.current.size >= 2 && pointers.current.has(e.pointerId)) {
      const prev = [...pointers.current.values()]
      const nextMap = new Map(pointers.current).set(e.pointerId, now)
      const next = [...nextMap.values()]
      pointers.current = nextMap
      onViewport(pinch(vpRef.current, [prev[0], prev[1]], [next[0], next[1]]))
      return
    }
    if (marquee) setMarquee({ ...marquee, b: now })
    else if (pan.current && pointers.current.has(e.pointerId)) {
      pointers.current.set(e.pointerId, now)
      onViewport(panBy(vpRef.current, now.x - pan.current.last.x, now.y - pan.current.last.y))
      pan.current = { last: now }
    }
  }
  const onBgUp = (e: RPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId)
    if (marquee) {
      const hits = def.nodes.filter(n => marqueeHits(vpRef.current, marquee.a, marquee.b, rectOf(pos(n.id)))).map(n => n.id)
      const tiny = Math.abs(marquee.a.x - marquee.b.x) < 3 && Math.abs(marquee.a.y - marquee.b.y) < 3
      p.onSelect?.(tiny ? [] : hits, e.ctrlKey || e.metaKey)
      setMarquee(null)
    }
    if (!pointers.current.size) pan.current = null
  }

  // ---- node drag
  const onNodeDown = (e: RPointerEvent<HTMLDivElement>, id: string) => {
    if ((e.target as HTMLElement).closest('[data-port]')) return
    e.stopPropagation()
    const additive = e.shiftKey || e.ctrlKey || e.metaKey
    const wasSelected = selection.has(id)
    box.current?.focus({ preventScroll: true })
    if (readOnly) {
      p.onSelect?.([id], false)
      return
    }
    const ids = wasSelected ? [...selection] : [id]
    if (!wasSelected) p.onSelect?.([id], additive)
    const starts: Record<string, Pos> = {}
    for (const sid of additive && !wasSelected ? [...selection, id] : ids) starts[sid] = pos(sid)
    drag.current = { id, startClient: { x: e.clientX, y: e.clientY }, starts, moved: false, additive, wasSelected }
    e.currentTarget.setPointerCapture?.(e.pointerId)
  }
  const onNodeMove = (e: RPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    if (!d) return
    const dx = (e.clientX - d.startClient.x) / vpRef.current.zoom
    const dy = (e.clientY - d.startClient.y) / vpRef.current.zoom
    if (!d.moved && Math.hypot(dx, dy) * vpRef.current.zoom < 4) return
    d.moved = true
    const lead = d.starts[d.id]
    const moving = Object.keys(d.starts)
    const others = def.nodes.filter(n => !moving.includes(n.id)).map(n => rectOf(positions[n.id] ?? { x: 0, y: 0 }))
    const snapped = snapPos({ x: lead.x + dx, y: lead.y + dy })
    const g = alignGuides({ x: snapped.x, y: snapped.y, w: NODE_W, h: NODE_H }, others, 6 / vpRef.current.zoom)
    const adj = { x: snapped.x + g.dx - lead.x, y: snapped.y + g.dy - lead.y }
    setGuides(g.guides)
    setPreview(Object.fromEntries(moving.map(id => [id, { x: d.starts[id].x + adj.x, y: d.starts[id].y + adj.y }])))
  }
  const onNodeUp = (e: RPointerEvent<HTMLDivElement>) => {
    const d = drag.current
    drag.current = null
    if (!d) return
    if (d.moved && preview) p.onMoveEnd?.({ ...positions, ...preview })
    else if (!d.additive && d.wasSelected && selection.size > 1) p.onSelect?.([d.id], false)
    else if (d.additive && d.wasSelected) p.onSelect?.([d.id], true) // toggles off
    setPreview(null)
    setGuides([])
    e.currentTarget.releasePointerCapture?.(e.pointerId)
  }

  // ---- connect from an output port
  const onPortDown = (e: RPointerEvent<Element>, id: string) => {
    if (readOnly) return
    e.stopPropagation()
    e.preventDefault()
    e.currentTarget.setPointerCapture?.(e.pointerId)
    setLink({ from: id, to: screenToWorld(vpRef.current, local(e)) })
  }
  const onPortMove = (e: RPointerEvent<Element>) => {
    if (link) setLink({ ...link, to: screenToWorld(vpRef.current, local(e)) })
  }
  const onPortUp = (e: RPointerEvent<Element>) => {
    if (!link) return
    const hit = typeof document.elementFromPoint === 'function' ? (document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null)?.closest<HTMLElement>('[data-node-id]') : null
    const to = hit?.dataset.nodeId
    if (to) p.onConnect?.(link.from, to)
    setLink(null)
  }

  // ---- dropping a palette item
  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    const type = e.dataTransfer.getData('application/x-patchquest-node')
    if (!type || !isNodeType(type) || readOnly) return
    e.preventDefault()
    p.onDropNew?.(type, screenToWorld(vpRef.current, local(e)))
  }

  const edges = def.edges.filter(e => positions[e.from] && positions[e.to])
  const zoomBy = (f: number) => {
    const r = box.current?.getBoundingClientRect()
    onViewport(zoomAt(vpRef.current, f, { x: (r?.width ?? 600) / 2, y: (r?.height ?? 400) / 2 }))
  }

  return (
    <div
      ref={box}
      className={`wf-canvas${readOnly ? ' wf-canvas--readonly' : ''}`}
      tabIndex={0}
      role="application"
      aria-label={p.label ?? 'Workflow canvas. Tab moves between steps, Enter edits the focused step, arrow keys move selected steps.'}
      onPointerDown={onBgDown}
      onPointerMove={onBgMove}
      onPointerUp={onBgUp}
      onPointerCancel={onBgUp}
      onKeyDown={p.onKeyDown}
      onDragOver={e => !readOnly && e.preventDefault()}
      onDrop={onDrop}
      style={{ backgroundSize: `${GRID * viewport.zoom * 2}px ${GRID * viewport.zoom * 2}px`, backgroundPosition: `${viewport.x}px ${viewport.y}px` }}
    >
      <div className="wf-world" style={{ transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})` }}>
        <svg className="wf-edges" width="1" height="1" aria-hidden="true">
          <defs>
            <marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" className="wf-arrow" />
            </marker>
          </defs>
          {guides.map(g => (g.axis === 'x' ? <line key={`x${g.at}`} x1={g.at} x2={g.at} y1={-4000} y2={4000} className="wf-guide" /> : <line key={`y${g.at}`} y1={g.at} y2={g.at} x1={-4000} x2={4000} className="wf-guide" />))}
          {edges.map(e => {
            const a = outPort(pos(e.from))
            const b = inPort(pos(e.to))
            const k = edgeKey(e)
            const mid = edgeMid(a, b)
            const text = labelText(e.when)
            return (
              <g key={k} className={`wf-edge${p.selectedEdge === k ? ' wf-edge--selected' : ''}${e.when === 'failed' || e.when === 'denied' || e.when === 'false' ? ' wf-edge--alt' : ''}`}>
                <path d={edgePath(a, b)} className="wf-edge__hit" onPointerDown={ev => { ev.stopPropagation(); p.onSelectEdge?.(k) }} />
                <path d={edgePath(a, b)} className="wf-edge__line" markerEnd="url(#wf-arrow)" />
                {text && (
                  <g transform={`translate(${mid.x}, ${mid.y})`}>
                    <rect x={-(text.length * 3.4 + 7)} y={-10} width={text.length * 6.8 + 14} height={20} rx={10} className="wf-edge__chip" />
                    <text textAnchor="middle" dy="4" className="wf-edge__text">{text}</text>
                  </g>
                )}
              </g>
            )
          })}
          {link && <path d={edgePath(outPort(pos(link.from)), link.to)} className="wf-edge__line wf-edge__line--draft" />}
        </svg>

        {def.nodes.map(n => {
          const at = pos(n.id)
          const selected = selection.has(n.id)
          const probs = p.problems?.get(n.id) ?? []
          const visual = p.visuals?.[n.id]
          const action = actionOf(n, p.actions)
          const copy = isNodeType(n.type) ? NODE_COPY[n.type] : { label: n.type, hint: '' }
          return (
            <div
              key={n.id}
              data-node-id={n.id}
              role="button"
              tabIndex={0}
              aria-pressed={selected}
              aria-label={`${copy.label} step ${n.id}. ${nodeSummary(n, p.actions)}.${probs.length ? ` ${probs.length} problem${probs.length > 1 ? 's' : ''}.` : ''}${visual ? ` ${VISUAL_COPY[visual].label}.` : ''}`}
              className={`wf-node wf-node--${n.type} ${tone(visual)}${selected ? ' wf-node--selected' : ''}${probs.length ? ' wf-node--problem' : ''}${p.highlight?.has(n.id) ? ' wf-node--attention' : ''}`}
              style={{ left: at.x, top: at.y, width: NODE_W, height: NODE_H }}
              onPointerDown={e => onNodeDown(e, n.id)}
              onPointerMove={onNodeMove}
              onPointerUp={onNodeUp}
              onPointerCancel={onNodeUp}
              onDoubleClick={() => p.onOpenConfig?.(n.id)}
              onFocus={e => {
                if (e.target === e.currentTarget && !selection.has(n.id) && selection.size <= 1) p.onSelect?.([n.id], false)
              }}
              onKeyDown={e => {
                if (e.key === 'Enter' && e.target === e.currentTarget) {
                  e.preventDefault()
                  e.stopPropagation()
                  p.onOpenConfig?.(n.id)
                } else if (e.key === ' ' && e.target === e.currentTarget) {
                  e.preventDefault()
                  p.onSelect?.([n.id], e.shiftKey)
                }
              }}
            >
              <span className="wf-port wf-port--in" data-port="in" aria-hidden="true" />
              <span className="wf-node__type">{copy.label}{n.max_visits && n.max_visits > 1 ? ` · up to ${n.max_visits}×` : ''}</span>
              <span className="wf-node__id ui-truncate">{n.id}</span>
              <span className="wf-node__sum ui-truncate">{nodeSummary(n, p.actions)}</span>
              {action?.requires_approval && <span className="wf-node__chip wf-node__chip--gate" title="This step needs a person's approval earlier on every path">needs approval gate</span>}
              {probs.length > 0 && <span className="wf-node__badge" title={probs.map(x => x.message).join('\n')}><Icon name="warning" size={12} />{probs.length}</span>}
              {visual && visual !== 'idle' && <span className={`wf-node__state wf-node__state--${visual}`}>{VISUAL_COPY[visual].label}</span>}
              {n.type !== 'end' && (
                <span className="wf-port wf-port--out" data-port="out" aria-hidden="true" onPointerDown={e => onPortDown(e, n.id)} onPointerMove={onPortMove} onPointerUp={onPortUp} onPointerCancel={() => setLink(null)} />
              )}
            </div>
          )
        })}
      </div>

      {marquee && <div className="wf-marquee" style={{ left: Math.min(marquee.a.x, marquee.b.x), top: Math.min(marquee.a.y, marquee.b.y), width: Math.abs(marquee.a.x - marquee.b.x), height: Math.abs(marquee.a.y - marquee.b.y) }} />}

      {def.nodes.length === 0 && !readOnly && (
        <div className="wf-canvas__empty">
          <strong>Start by adding a step</strong>
          <span>Choose a step type from the palette. You can also drag it onto the canvas.</span>
        </div>
      )}

      <div className="wf-zoom" data-canvas-control>
        <IconButton icon="plus" label="Zoom in" size="sm" onClick={() => zoomBy(1.2)} />
        <span className="wf-zoom__value" aria-live="polite">{Math.round(viewport.zoom * 100)}%</span>
        <IconButton icon="minus" label="Zoom out" size="sm" onClick={() => zoomBy(1 / 1.2)} className="wf-zoom__out" />
        <IconButton icon="extras" label="Fit to screen" size="sm" onClick={fit} />
      </div>
    </div>
  )
}

export const Canvas = memo(CanvasImpl)
