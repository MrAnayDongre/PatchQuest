import { useId, useRef, type ReactNode } from 'react'
import { Button, Field, IconButton, Input, Select, Textarea } from '../../design/primitives'
import { OPERATOR_COPY } from '../../lib/workflow/copy'
import { addMember, changeKind, children, condKind, emptyComparison, removeMember, setAt, type Cond, type CondKind, type CondPath } from '../../lib/workflow/condition'
import { showValue } from '../../lib/workflow/duration'
import { insertAtCursor, type RefOption } from '../../lib/workflow/refs'

/** A text input with an "Insert value" menu that only offers references the server will accept here. */
export function RefField({ label, value, onChange, refs, multiline, hint, optional, placeholder, error }: {
  label: string
  value: string
  onChange: (v: string) => void
  refs: RefOption[]
  multiline?: boolean
  hint?: ReactNode
  optional?: boolean
  placeholder?: string
  error?: string | null
}) {
  const el = useRef<HTMLInputElement | HTMLTextAreaElement>(null)
  const id = useId()
  const insert = (path: string) => {
    if (!path) return
    const node = el.current
    const start = node?.selectionStart ?? value.length
    const end = node?.selectionEnd ?? value.length
    const next = insertAtCursor(value, start, end, path)
    onChange(next.value)
    requestAnimationFrame(() => {
      node?.focus()
      node?.setSelectionRange?.(next.caret, next.caret)
    })
  }
  return (
    <Field label={label} hint={hint} optional={optional} error={error}>
      {p => (
        <>
          {multiline ? (
            <Textarea {...p} ref={el as React.Ref<HTMLTextAreaElement>} rows={3} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} />
          ) : (
            <Input {...p} ref={el as React.Ref<HTMLInputElement>} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} />
          )}
          <label htmlFor={`${id}-ref`} className="ui-sr-only">Insert a value into {label}</label>
          <Select id={`${id}-ref`} className="wf-ref" value="" onChange={e => insert(e.target.value)} disabled={!refs.length}>
            <option value="">Insert a value…</option>
            {(['Trigger', 'Variables', 'Earlier steps'] as const).map(g => {
              const items = refs.filter(r => r.group === g)
              return items.length ? (
                <optgroup key={g} label={g}>
                  {items.map(r => (
                    <option key={r.path} value={r.path}>{r.label}</option>
                  ))}
                </optgroup>
              ) : null
            })}
          </Select>
        </>
      )}
    </Field>
  )
}

export interface KvProps {
  label: string
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  refs: RefOption[]
  coerce: (text: string, original: unknown) => unknown
  keyPlaceholder?: string
  keyError?: (key: string) => string | null
  addLabel?: string
  hint?: ReactNode
}

/** Rows of name and value. Order is kept; renaming a key keeps its position. */
export function KeyValueEditor({ label, value, onChange, refs, coerce, keyPlaceholder = 'name', keyError, addLabel = 'Add row', hint }: KvProps) {
  const entries = Object.entries(value)
  const rebuild = (list: [string, unknown][]) => onChange(Object.fromEntries(list))
  return (
    <fieldset className="wf-kv">
      <legend className="ui-field__label">{label}</legend>
      {hint && <p className="ui-field__hint">{hint}</p>}
      {entries.map(([k, v], i) => (
        <div className="wf-kv__row" key={i}>
          <Field label={`${label} name ${i + 1}`} className="wf-kv__k" error={keyError?.(k) ?? null}>
            {p => <Input {...p} value={k} placeholder={keyPlaceholder} onChange={e => rebuild(entries.map((x, j) => (j === i ? [e.target.value, x[1]] : x)))} spellCheck={false} />}
          </Field>
          <RefField label={`${label} value ${i + 1}`} value={showValue(v)} refs={refs} onChange={t => rebuild(entries.map((x, j) => (j === i ? [x[0], coerce(t, x[1])] : x)))} />
          <IconButton icon="trash" label={`Remove ${k || 'row'}`} size="sm" onClick={() => rebuild(entries.filter((_, j) => j !== i))} />
        </div>
      ))}
      <Button size="sm" icon="plus" onClick={() => { rebuild([...entries, [uniqueKey(entries.map(e => e[0]), keyPlaceholder === 'agent.setting' ? 'agent.setting' : 'name'), '']]) }}>{addLabel}</Button>
    </fieldset>
  )
}

function uniqueKey(existing: string[], base: string): string {
  if (!existing.includes(base)) return base
  for (let i = 2; ; i++) if (!existing.includes(`${base}_${i}`)) return `${base}_${i}`
}

const KIND_LABEL: Record<CondKind, string> = { compare: 'One comparison', all: 'All of these', any: 'Any of these', not: 'None of these (not)' }

/** Structured comparison builder: a comparison, or all/any/not groups of comparisons. No expressions. */
export function ConditionEditor({ value, onChange, refs, path = [] }: { value: Cond; onChange: (v: Cond) => void; refs: RefOption[]; path?: CondPath }) {
  const root = value
  const render = (c: Cond, at: CondPath, depth: number): ReactNode => {
    const kind = condKind(c)
    const update = (next: Cond) => onChange(setAt(root, at, next))
    const label = at.length ? `condition ${at.map(i => i + 1).join('.')}` : 'condition'
    return (
      <div className={`wf-cond wf-cond--${kind}`} key={at.join('.')} role="group" aria-label={label}>
        <Field label="Type" className="wf-cond__kind">
          {p => (
            <Select {...p} value={kind} onChange={e => update(changeKind(c, e.target.value as CondKind))}>
              {(Object.keys(KIND_LABEL) as CondKind[]).filter(k => depth < 2 || k === 'compare').map(k => (
                <option key={k} value={k}>{KIND_LABEL[k]}</option>
              ))}
            </Select>
          )}
        </Field>
        {kind === 'compare' ? (
          <>
            <RefField label="Value" value={String(c.left ?? '')} refs={refs} onChange={t => update({ ...c, left: t })} />
            <Field label="Comparison">
              {p => (
                <Select {...p} value={String(c.op ?? 'eq')} onChange={e => {
                  const { right, ...rest } = c
                  update(e.target.value === 'exists' ? { ...rest, op: 'exists' } : { ...rest, op: e.target.value, right: right ?? '' })
                }}>
                  {OPERATOR_COPY.map(o => (
                    <option key={o.id} value={o.id}>{o.label}</option>
                  ))}
                </Select>
              )}
            </Field>
            {c.op !== 'exists' && <RefField label="Compare with" value={showValue(c.right ?? '')} refs={refs} onChange={t => update({ ...c, right: t })} />}
          </>
        ) : (
          <div className="wf-cond__members">
            {children(c).map((m, i) => (
              <div className="wf-cond__member" key={i}>
                {render(m, [...at, i], depth + 1)}
                {kind !== 'not' && <IconButton icon="trash" label={`Remove ${label} part ${i + 1}`} size="sm" onClick={() => onChange(removeMember(root, [...at, i]))} />}
              </div>
            ))}
            {kind !== 'not' && <Button size="sm" icon="plus" onClick={() => onChange(addMember(root, at))}>Add a comparison</Button>}
          </div>
        )}
      </div>
    )
  }
  return <>{render(value, path, 0)}</>
}

export { emptyComparison }
