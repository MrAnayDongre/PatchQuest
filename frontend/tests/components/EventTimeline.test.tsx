import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { EventTimeline } from '../../src/features/run/EventTimeline'
import { reduceEvents } from '../../src/lib/runState'
import { ev, happyRun } from '../features/helpers'

afterEach(cleanup)

describe('EventTimeline', () => {
  it('renders only a window of a very large ledger', () => {
    const events = Array.from({ length: 12_000 }, (_, i) => ev(i + 1, i % 2 ? 'command_executed' : 'workspace_created', { payload: { command: `cmd ${i}`, returncode: 0 } }))
    const { timeline } = reduceEvents(events)
    render(<EventTimeline items={timeline} />)
    const rows = screen.getAllByRole('listitem')
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.length).toBeLessThan(60)
    // set size is announced even though rows are not in the DOM
    expect(rows[0].getAttribute('aria-setsize')).toBe(String(12_000))
  })

  it('filters by category chip', () => {
    const { timeline } = reduceEvents(happyRun())
    render(<EventTimeline items={timeline} />)
    const before = screen.getAllByRole('listitem').length
    fireEvent.click(screen.getByRole('button', { name: /^Approval/ }))
    const rows = screen.getAllByRole('listitem')
    expect(rows.length).toBe(2)
    expect(before).toBeGreaterThan(2)
    expect(within(rows[0]).getByText('Waiting for your approval')).toBeTruthy()
  })

  it('groups repeated events and can ungroup them', () => {
    const { timeline } = reduceEvents([1, 2, 3].map(i => ev(i, 'model_call', { payload: { role: 'coder', duration_ms: i * 100 } })))
    render(<EventTimeline items={timeline} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText(/×3/)).toBeTruthy()
    fireEvent.click(screen.getByRole('switch', { name: /Group repeats/ }))
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })

  it('searches and explains an empty result', () => {
    const { timeline } = reduceEvents(happyRun())
    render(<EventTimeline items={timeline} />)
    fireEvent.change(screen.getByLabelText('Search events'), { target: { value: 'nothing-like-this' } })
    expect(screen.getByText('No events match those filters.')).toBeTruthy()
  })

  it('opens an inspector with the raw event on click', () => {
    const { timeline } = reduceEvents(happyRun())
    render(<EventTimeline items={timeline} />)
    fireEvent.click(screen.getByRole('button', { name: /^Approval/ }))
    fireEvent.click(screen.getByRole('button', { name: /Waiting for your approval/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/"approval_id": "ap1"/)).toBeTruthy()
  })

  it('shows a helpful empty state before any event arrives', () => {
    render(<EventTimeline items={[]} />)
    expect(screen.getByText(/No events yet/)).toBeTruthy()
  })
})
