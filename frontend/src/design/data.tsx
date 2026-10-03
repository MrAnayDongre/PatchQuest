import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import type { Tone } from '../lib/eventCopy'
import { buildLayout, isNearBottom, visibleRange } from '../lib/virtual'
import { IconButton, cx } from './primitives'

/* ------------------------------------------------------------- DataTable */
export interface Column<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right'
  /** Hide this column on narrow screens. */
  secondary?: boolean
  width?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  caption: string
  onRowClick?: (row: T) => void
  empty?: ReactNode
}

export function DataTable<T>({ columns, rows, rowKey, caption, onRowClick, empty }: DataTableProps<T>) {
  if (!rows.length && empty) return <>{empty}</>
  return (
    <div className="ui-table-wrap">
      <table className="ui-table">
        <caption className="ui-sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map(c => (
              <th key={c.key} scope="col" className={cx(c.secondary && 'ui-hide-sm', c.align === 'right' && 'ui-align-right')} style={c.width ? { width: c.width } : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr
              key={rowKey(row)}
              className={cx(onRowClick && 'ui-table__row--click')}
              tabIndex={onRowClick ? 0 : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={
                onRowClick
                  ? e => {
                      if (e.key === 'Enter') onRowClick(row)
                    }
                  : undefined
              }
            >
              {columns.map(c => (
                <td key={c.key} className={cx(c.secondary && 'ui-hide-sm', c.align === 'right' && 'ui-align-right')}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ------------------------------------------------------------- CodeBlock */
export function CodeBlock({ code, label, maxHeight = 320, wrap = false }: { code: string; label?: string; maxHeight?: number; wrap?: boolean }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard unavailable: the text is still selectable
    }
  }
  return (
    <div className="ui-code">
      <div className="ui-code__bar">
        <span className="ui-code__label">{label ?? ''}</span>
        <IconButton icon={copied ? 'check' : 'copy'} label={copied ? 'Copied' : 'Copy to clipboard'} size="sm" onClick={copy} />
      </div>
      <pre className={cx('ui-code__pre', wrap && 'ui-code__pre--wrap')} style={{ maxHeight }} tabIndex={0} aria-label={label}>
        <code>{code}</code>
      </pre>
    </div>
  )
}

/* -------------------------------------------------------------- Timeline */
export interface TimelineEntry {
  id: string | number
  title: ReactNode
  meta?: ReactNode
  detail?: ReactNode
  tone?: Tone
}

/** Simple vertical timeline for short lists (status history, lineage). Long event feeds use VirtualList. */
export function Timeline({ entries, label }: { entries: TimelineEntry[]; label: string }) {
  return (
    <ol className="ui-timeline" aria-label={label}>
      {entries.map(e => (
        <li key={e.id} className="ui-timeline__item">
          <span className={cx('ui-timeline__dot', `ui-dot--${e.tone ?? 'neutral'}`)} aria-hidden="true" />
          <div className="ui-timeline__main">
            <div className="ui-timeline__title">{e.title}</div>
            {e.meta && <div className="ui-timeline__meta">{e.meta}</div>}
            {e.detail && <div className="ui-timeline__detail">{e.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}

/* ----------------------------------------------------------- VirtualList */
export interface VirtualListHandle {
  scrollToEnd: () => void
  scrollToIndex: (index: number) => void
}

interface VirtualListProps<T> {
  items: T[]
  getHeight: (item: T) => number
  getKey: (item: T) => string | number
  renderRow: (item: T, index: number) => ReactNode
  label: string
  className?: string
  /** Keep the newest row in view while the user is already at the bottom. */
  follow?: boolean
  onFollowChange?: (following: boolean) => void
  overscan?: number
  /** Viewport height assumed before the element can be measured (and in test environments). */
  fallbackViewport?: number
}

function VirtualListInner<T>(
  { items, getHeight, getKey, renderRow, label, className, follow, onFollowChange, overscan = 8, fallbackViewport = 480 }: VirtualListProps<T>,
  handle: React.ForwardedRef<VirtualListHandle>,
) {
  const ref = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(fallbackViewport)
  const stick = useRef(true)
  const raf = useRef<number | undefined>(undefined)

  const heights = useMemo(() => items.map(getHeight), [items, getHeight])
  const layout = useMemo(() => buildLayout(heights), [heights])

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => el.clientHeight > 0 && setViewport(el.clientHeight)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useLayoutEffect(() => {
    const el = ref.current
    if (el && follow && stick.current) {
      el.scrollTop = layout.total
      setScrollTop(el.scrollTop)
    }
  }, [layout.total, follow])

  const onScroll = useCallback(() => {
    if (raf.current !== undefined) return
    raf.current = window.requestAnimationFrame(() => {
      raf.current = undefined
      const el = ref.current
      if (!el) return
      setScrollTop(el.scrollTop)
      const near = isNearBottom(el.scrollTop, el.clientHeight || viewport, layout.total)
      if (near !== stick.current) {
        stick.current = near
        onFollowChange?.(near)
      }
    })
  }, [layout.total, onFollowChange, viewport])

  useEffect(
    () => () => {
      if (raf.current !== undefined) window.cancelAnimationFrame(raf.current)
    },
    [],
  )

  useImperativeHandle(
    handle,
    () => ({
      scrollToEnd: () => {
        const el = ref.current
        if (!el) return
        el.scrollTop = layout.total
        stick.current = true
        setScrollTop(el.scrollTop)
        onFollowChange?.(true)
      },
      scrollToIndex: (i: number) => {
        const el = ref.current
        if (!el) return
        el.scrollTop = layout.offsets[Math.max(0, Math.min(i, items.length - 1))] ?? 0
        setScrollTop(el.scrollTop)
      },
    }),
    [layout, items.length, onFollowChange],
  )

  const range = visibleRange(layout, scrollTop, viewport, overscan)
  const rows: ReactNode[] = []
  for (let i = range.start; i < range.end; i++) {
    const item = items[i]
    rows.push(
      <div key={getKey(item)} role="listitem" className="ui-vlist__row" style={{ top: layout.offsets[i], height: heights[i] }} aria-posinset={i + 1} aria-setsize={items.length}>
        {renderRow(item, i)}
      </div>,
    )
  }

  return (
    <div ref={ref} className={cx('ui-vlist', className)} onScroll={onScroll} role="list" aria-label={label} tabIndex={0}>
      <div className="ui-vlist__inner" style={{ height: layout.total }}>
        {rows}
      </div>
    </div>
  )
}

export const VirtualList = forwardRef(VirtualListInner) as <T>(props: VirtualListProps<T> & { ref?: React.Ref<VirtualListHandle> }) => ReturnType<typeof VirtualListInner>

/* ---------------------------------------------------------------- Charts */
export interface Segment {
  label: string
  value: number
  tone: Tone
}

/** Horizontal stacked bar (SVG). The legend carries the numbers, so color is never the only signal. */
export function StackedBar({ segments, label }: { segments: Segment[]; label: string }) {
  const total = segments.reduce((t, s) => t + s.value, 0)
  if (!total) return null
  let x = 0
  return (
    <div className="ui-stack">
      <svg className="ui-stack__svg" viewBox="0 0 100 6" preserveAspectRatio="none" role="img" aria-label={`${label}: ${segments.map(s => `${s.label} ${s.value}`).join(', ')}`}>
        {segments
          .filter(s => s.value > 0)
          .map(s => {
            const w = (s.value / total) * 100
            const rect = <rect key={s.label} x={x} y={0} width={Math.max(0, w - 0.4)} height={6} rx={1} className={`ui-fill--${s.tone}`} />
            x += w
            return rect
          })}
      </svg>
      <ul className="ui-stack__legend">
        {segments
          .filter(s => s.value > 0)
          .map(s => (
            <li key={s.label}>
              <span className={cx('ui-dot', `ui-dot--${s.tone}`)} aria-hidden="true" /> {s.label} <strong>{s.value}</strong>
            </li>
          ))}
      </ul>
    </div>
  )
}

/** Tiny SVG line with an area fill; `values` oldest first. */
export function Sparkline({ values, label, format }: { values: number[]; label: string; format: (v: number) => string }) {
  if (values.length < 2) return null
  const w = 120
  const h = 32
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 3 - ((v - min) / span) * (h - 6)] as const)
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  return (
    <svg className="ui-spark" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${label}: latest ${format(values[values.length - 1])}, range ${format(min)} to ${format(max)}`} preserveAspectRatio="none">
      <path d={`${line} L${w} ${h} L0 ${h} Z`} className="ui-spark__area" />
      <path d={line} className="ui-spark__line" fill="none" />
    </svg>
  )
}
