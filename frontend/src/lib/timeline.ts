import type { EventCategory } from './eventCopy'
import type { TimelineItem } from './runState'

export interface TimelineRow {
  /** First item of the group; used for the row key. */
  item: TimelineItem
  /** Number of consecutive identical events folded into this row. */
  count: number
  /** Timestamp of the most recent item in the group. */
  lastAt: string
  /** Every item in the group (the inspector shows the first). */
  items: TimelineItem[]
}

export interface TimelineFilter {
  /** Empty set means "all categories". */
  categories: ReadonlySet<EventCategory>
  query: string
  collapse: boolean
}

export function filterTimeline(items: TimelineItem[], filter: Pick<TimelineFilter, 'categories' | 'query'>): TimelineItem[] {
  const q = filter.query.trim().toLowerCase()
  if (!filter.categories.size && !q) return items
  return items.filter(it => {
    if (filter.categories.size && !filter.categories.has(it.category)) return false
    if (!q) return true
    return `${it.title} ${it.detail ?? ''} ${it.code ?? ''} ${it.type}`.toLowerCase().includes(q)
  })
}

/** Fold runs of consecutive items sharing a collapse key into single rows. */
export function toRows(items: TimelineItem[], collapse: boolean): TimelineRow[] {
  const rows: TimelineRow[] = []
  for (const item of items) {
    const prev = rows[rows.length - 1]
    if (collapse && prev && prev.item.collapseKey === item.collapseKey) {
      prev.count += 1
      prev.lastAt = item.at
      prev.items.push(item)
    } else {
      rows.push({ item, count: 1, lastAt: item.at, items: [item] })
    }
  }
  return rows
}

export function countByCategory(items: TimelineItem[]): Record<EventCategory, number> {
  const out: Record<EventCategory, number> = { agent: 0, model: 0, command: 0, patch: 0, validation: 0, approval: 0, system: 0 }
  for (const it of items) out[it.category] += 1
  return out
}

export const ROW_HEIGHT = 40
export const ROW_HEIGHT_WITH_DETAIL = 62

export function rowHeight(row: TimelineRow): number {
  return row.item.detail || row.item.code ? ROW_HEIGHT_WITH_DETAIL : ROW_HEIGHT
}
