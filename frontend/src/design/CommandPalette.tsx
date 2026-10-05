import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { filterCommands, groupCommands, type PaletteCommand } from '../lib/palette'
import { Icon } from './icons'
import { Modal } from './overlay'
import { Kbd } from './primitives'

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  commands: PaletteCommand[]
}

/** Combobox + listbox palette. Up/Down move, Enter runs, Escape closes; typing filters. */
export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
    }
  }, [open])

  const results = useMemo(() => filterCommands(commands, query), [commands, query])
  const groups = useMemo(() => groupCommands(results), [results])
  // Flat order must match what is rendered (grouped), not the score order.
  const flat = useMemo(() => groups.flatMap(g => g.items), [groups])
  const clamped = Math.min(active, Math.max(0, flat.length - 1))

  const run = (cmd: PaletteCommand | undefined) => {
    if (!cmd) return
    onClose()
    // Let the dialog unmount (and restore focus) before the action runs, so actions can open other dialogs.
    window.setTimeout(cmd.run, 0)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive(flat.length ? (clamped + 1) % flat.length : 0)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive(flat.length ? (clamped - 1 + flat.length) % flat.length : 0)
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActive(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActive(Math.max(0, flat.length - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      run(flat[clamped])
    }
  }

  useEffect(() => {
    if (!open) return
    document.getElementById(`${listId}-${clamped}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [clamped, open, listId])

  let index = -1
  return (
    <Modal open={open} onClose={onClose} title="Command palette" hideTitle size="md" className="ui-palette" initialFocus={() => inputRef.current}>
      <div className="ui-palette__search">
        <Icon name="search" size={18} />
        <input
          ref={inputRef}
          className="ui-palette__input"
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={flat.length ? `${listId}-${clamped}` : undefined}
          aria-autocomplete="list"
          aria-label="Search commands and runs"
          placeholder="Type a command or search runs…"
          value={query}
          onChange={e => {
            setQuery(e.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      <div id={listId} role="listbox" aria-label="Results" className="ui-palette__list">
        {flat.length === 0 && <p className="ui-palette__empty">Nothing matches “{query}”.</p>}
        {groups.map(g => (
          <div key={g.group} role="group" aria-label={g.group}>
            <div className="ui-palette__group" aria-hidden="true">
              {g.group}
            </div>
            {g.items.map(c => {
              index++
              const i = index
              return (
                <div
                  key={c.id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === clamped}
                  className="ui-palette__item"
                  onMouseMove={() => i !== clamped && setActive(i)}
                  onClick={() => run(c)}
                >
                  <span className="ui-palette__title ui-truncate">{c.title}</span>
                  {c.hint && <span className="ui-palette__hint ui-truncate">{c.hint}</span>}
                  {c.shortcut && (
                    <span className="ui-palette__keys">
                      {c.shortcut.map(k => (
                        <Kbd key={k}>{k}</Kbd>
                      ))}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <div className="ui-palette__footer" aria-hidden="true">
        <span>
          <Kbd>↑</Kbd> <Kbd>↓</Kbd> navigate
        </span>
        <span>
          <Kbd>Enter</Kbd> run
        </span>
        <span>
          <Kbd>Esc</Kbd> close
        </span>
      </div>
    </Modal>
  )
}
