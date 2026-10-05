import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, act } from '@testing-library/react'
import { CommandPalette } from '../../src/design/CommandPalette'
import type { PaletteCommand } from '../../src/lib/palette'

afterEach(cleanup)

function setup() {
  const run = { newRun: vi.fn(), runs: vi.fn(), theme: vi.fn() }
  const commands: PaletteCommand[] = [
    { id: 'n', title: 'New run', group: 'Actions', run: run.newRun },
    { id: 't', title: 'Toggle theme', group: 'Actions', run: run.theme },
    { id: 'r', title: 'Go to Runs', group: 'Go to', run: run.runs },
  ]
  const onClose = vi.fn()
  render(<CommandPalette open onClose={onClose} commands={commands} />)
  return { run, onClose, input: screen.getByRole('combobox') }
}

describe('CommandPalette', () => {
  it('focuses the search box and lists everything grouped', () => {
    const { input } = setup()
    expect(document.activeElement).toBe(input)
    expect(screen.getAllByRole('option').map(o => o.textContent)).toEqual(['New run', 'Toggle theme', 'Go to Runs'])
    expect(screen.getByRole('group', { name: 'Go to' })).toBeTruthy()
  })

  it('filters as you type and says when nothing matches', () => {
    const { input } = setup()
    fireEvent.change(input, { target: { value: 'theme' } })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    fireEvent.change(input, { target: { value: 'zzzz' } })
    expect(screen.queryAllByRole('option')).toHaveLength(0)
    expect(screen.getByText(/Nothing matches/)).toBeTruthy()
  })

  it('moves with the arrow keys, wraps around, and tracks the active option', () => {
    const { input } = setup()
    const active = () => input.getAttribute('aria-activedescendant')
    const selected = () => screen.getAllByRole('option').findIndex(o => o.getAttribute('aria-selected') === 'true')
    expect(selected()).toBe(0)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(selected()).toBe(1)
    expect(active()).toBe(screen.getAllByRole('option')[1].id)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(selected()).toBe(2)
  })

  it('runs the highlighted command on Enter and closes', async () => {
    vi.useFakeTimers()
    const { input, run, onClose } = setup()
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onClose).toHaveBeenCalled()
    await act(async () => {
      vi.runAllTimers()
    })
    expect(run.theme).toHaveBeenCalledTimes(1)
    expect(run.newRun).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('closes on Escape', () => {
    const { onClose } = setup()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('runs a command when clicked', async () => {
    vi.useFakeTimers()
    const { run } = setup()
    fireEvent.click(screen.getByText('Go to Runs'))
    await act(async () => {
      vi.runAllTimers()
    })
    expect(run.runs).toHaveBeenCalled()
    vi.useRealTimers()
  })
})
