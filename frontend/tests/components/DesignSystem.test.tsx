import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { DiffViewer } from '../../src/design/DiffViewer'
import { Dialog } from '../../src/design/overlay'
import { Field, Input, ProgressBar, Switch, TabPanel, Tabs } from '../../src/design/primitives'
import { parseUnifiedDiff } from '../../src/lib/diff'

afterEach(cleanup)

describe('Tabs', () => {
  function Demo() {
    const [v, setV] = useState('a')
    return (
      <>
        <Tabs label="Demo" idPrefix="d" value={v} onChange={setV} tabs={[{ id: 'a', label: 'One' }, { id: 'b', label: 'Two' }, { id: 'c', label: 'Three' }]} />
        <TabPanel idPrefix="d" id="a" active={v === 'a'}>panel a</TabPanel>
        <TabPanel idPrefix="d" id="b" active={v === 'b'}>panel b</TabPanel>
      </>
    )
  }
  it('uses roving focus with arrow, Home and End keys', () => {
    render(<Demo />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map(t => t.getAttribute('tabindex'))).toEqual(['0', '-1', '-1'])
    fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Two' }).getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'Two' }))
    expect(screen.getByText('panel b')).toBeTruthy()
    fireEvent.keyDown(document.activeElement as Element, { key: 'End' })
    expect(screen.getByRole('tab', { name: 'Three' }).getAttribute('aria-selected')).toBe('true')
    fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'One' }).getAttribute('aria-selected')).toBe('true')
  })
})

describe('Dialog', () => {
  it('traps focus, closes on Escape and restores focus to the opener', () => {
    function Demo() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button onClick={() => setOpen(true)}>open</button>
          <Dialog open={open} onClose={() => setOpen(false)} title="Hello">
            <button>first</button>
            <button>last</button>
          </Dialog>
        </>
      )
    }
    render(<Demo />)
    const opener = screen.getByText('open')
    opener.focus()
    fireEvent.click(opener)
    const dialog = screen.getByRole('dialog', { name: 'Hello' })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(document.activeElement?.textContent).toBe('first')
    const last = screen.getByText('last')
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Close')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })
})

describe('form primitives', () => {
  it('wires label, hint and error to the control', () => {
    render(<Field label="Repo" hint="Full path" error="Required">{p => <Input {...p} />}</Field>)
    const input = screen.getByLabelText('Repo')
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toBe('Required')
  })
  it('switch toggles with aria-checked', () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onChange={onChange} label="Dry run" />)
    fireEvent.click(screen.getByRole('switch', { name: 'Dry run' }))
    expect(onChange).toHaveBeenCalledWith(true)
  })
  it('progress bar exposes its value', () => {
    render(<ProgressBar value={0.37} label="Model calls used" />)
    expect(screen.getByRole('progressbar', { name: 'Model calls used' }).getAttribute('aria-valuenow')).toBe('37')
  })
})

describe('DiffViewer', () => {
  const files = parseUnifiedDiff('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n keep\n-old\n+new\ndiff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-x\n+y\n')
  it('lists files, switches files and layouts', () => {
    render(<DiffViewer files={files} />)
    expect(screen.getByText(/2 files/)).toBeTruthy()
    expect(screen.getByText('old')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /b\.py/ }))
    expect(screen.getByText('x')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Split' }))
    expect(screen.getByLabelText(/side by side/)).toBeTruthy()
  })
  it('says so when there is nothing to show', () => {
    render(<DiffViewer files={[]} emptyMessage="Nothing here." />)
    expect(screen.getByText('Nothing here.')).toBeTruthy()
  })
})
