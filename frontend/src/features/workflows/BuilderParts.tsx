import { useEffect, useRef, useState } from 'react'
import { CodeBlock } from '../../design/data'
import { Badge, Button, Field, Input, Segmented, Textarea } from '../../design/primitives'
import { Callout, Dialog } from '../../design/overlay'
import { relativeTime } from '../../lib/format'
import type { ActionMeta } from '../../lib/workflow/clientCheck'
import { NODE_COPY } from '../../lib/workflow/copy'
import { labelText } from '../../lib/workflow/edgeRules'
import { outgoingOf } from '../../lib/workflow/model'
import { parseImport, toJson, toYaml } from '../../lib/workflow/serialize'
import { listVersions, type WorkflowVersion } from '../../api/workflows'
import { useAsync } from '../../hooks/useAsync'
import { ErrorNotice } from '../common'
import { NODE_TYPES, type NodeType, type Problem, type WfDef } from '../../lib/workflow/types'
import { nodeSummary } from './summary'
import { Connections } from './ConfigPanel'


export function Palette({ onAdd, disabled }: { onAdd: (t: NodeType) => void; disabled?: boolean }) {
  return (
    <nav className="wf-palette" aria-label="Add a step">
      <h2 className="wf-section-title">Add a step</h2>
      <ul>
        {NODE_TYPES.map(t => (
          <li key={t}>
            <button
              type="button"
              className={`wf-palette__item wf-palette__item--${t}`}
              disabled={disabled}
              draggable={!disabled}
              onDragStart={e => {
                e.dataTransfer.setData('application/x-patchquest-node', t)
                e.dataTransfer.effectAllowed = 'copy'
              }}
              onClick={() => onAdd(t)}
              title={NODE_COPY[t].hint}
            >
              <span className="wf-palette__label">{NODE_COPY[t].label}</span>
              <span className="wf-palette__hint">{NODE_COPY[t].hint}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}

export function ProblemsList({ problems, checking, ok, onSelect }: { problems: Problem[]; checking: boolean; ok: boolean; onSelect: (node: string) => void }) {
  return (
    <section aria-label="Problems" className="wf-problems">
      <p className="wf-problems__status" role="status">
        {checking ? 'Checking with the server…' : problems.length ? `${problems.length} ${problems.length === 1 ? 'problem' : 'problems'} to fix before saving` : ok ? 'No problems. The server accepts this workflow.' : 'No problems found so far.'}
      </p>
      <ul>
        {problems.map((p, i) => (
          <li key={`${p.code}-${p.node}-${i}`}>
            {p.node ? (
              <button type="button" className="wf-problems__item" onClick={() => onSelect(p.node as string)}>
                <Badge tone="danger">{p.node}</Badge> <span>{p.message}</span>
              </button>
            ) : (
              <div className="wf-problems__item wf-problems__item--static"><span>{p.message}</span></div>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The keyboard- and screen-reader-friendly alternative to the canvas: every step and where it leads. */
export function NodeListView({ def, selection, onSelect, onOpen, problems, actions }: { def: WfDef; selection: ReadonlySet<string>; onSelect: (id: string) => void; onOpen: (id: string) => void; problems: Map<string, Problem[]>; actions: Map<string, ActionMeta> }) {
  if (!def.nodes.length) return <p className="ui-muted wf-list__empty">No steps yet. Use “Add a step”.</p>
  return (
    <ol className="wf-list" aria-label="Steps in this workflow">
      {def.nodes.map((n, i) => {
        const out = outgoingOf(def, n.id)
        const probs = problems.get(n.id) ?? []
        return (
          <li key={n.id} className={`wf-list__item${selection.has(n.id) ? ' wf-list__item--selected' : ''}`}>
            <button type="button" className="wf-list__main" aria-pressed={selection.has(n.id)} onClick={() => onSelect(n.id)} onDoubleClick={() => onOpen(n.id)}>
              <span className="wf-list__num">{i + 1}</span>
              <span className="wf-list__text">
                <strong>{n.id}</strong> <span className="ui-muted">{NODE_COPY[n.type as NodeType]?.label ?? n.type}</span>
                <span className="wf-list__sum ui-truncate">{nodeSummary(n, actions)}</span>
              </span>
              {probs.length > 0 && <Badge tone="danger">{probs.length} {probs.length === 1 ? 'problem' : 'problems'}</Badge>}
            </button>
            <p className="wf-list__out">
              {out.length ? `Leads to ${out.map(e => `${e.to}${e.when ? ` (${labelText(e.when).toLowerCase()})` : ''}`).join(', ')}` : n.type === 'end' ? 'Finishes the workflow' : 'Leads nowhere yet'}
            </p>
          </li>
        )
      })}
    </ol>
  )
}

export function ConnectDialog({ def, nodeId, open, onClose, onDef }: { def: WfDef; nodeId: string | null; open: boolean; onClose: () => void; onDef: (d: WfDef) => void }) {
  const node = def.nodes.find(n => n.id === nodeId)
  return (
    <Dialog open={open && !!node} onClose={onClose} title={node ? `Connect ${node.id} to…` : 'Connect'} description="Choose the step that should come next." footer={<Button onClick={onClose}>Done</Button>}>
      {node && <Connections def={def} node={node} onDef={onDef} />}
    </Dialog>
  )
}

export function ExportDialog({ def, open, onClose }: { def: WfDef; open: boolean; onClose: () => void }) {
  const [format, setFormat] = useState<'json' | 'yaml'>('json')
  const text = format === 'json' ? toJson(def) : toYaml(def)
  const download = () => {
    try {
      const url = URL.createObjectURL(new Blob([text], { type: format === 'json' ? 'application/json' : 'text/yaml' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `${def.name || 'workflow'}.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // the text above can still be copied
    }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Export workflow" description="Exactly what the server stores. Step positions on the canvas are not part of it." size="lg" footer={<><Button variant="ghost" onClick={onClose}>Close</Button><Button variant="primary" icon="download" onClick={download}>Download</Button></>}>
      <div className="form-stack">
        <Segmented label="Format" value={format} onChange={setFormat} options={[{ id: 'json', label: 'JSON' }, { id: 'yaml', label: 'YAML' }]} />
        <CodeBlock code={text} label={`${def.name || 'workflow'}.${format}`} maxHeight={360} />
      </div>
    </Dialog>
  )
}

export function ImportDialog({ open, onClose, onImport }: { open: boolean; onClose: () => void; onImport: (def: WfDef) => void }) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const file = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (open) {
      setText('')
      setError(null)
    }
  }, [open])
  const go = () => {
    const r = parseImport(text)
    if (!r.ok) return setError(r.error)
    onImport(r.def)
    onClose()
  }
  return (
    <Dialog open={open} onClose={onClose} title="Import workflow" description="Paste JSON or YAML, or choose a file. This replaces the workflow you are editing. You can undo it." size="lg" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" icon="upload" onClick={go} disabled={!text.trim()}>Import</Button></>}>
      <div className="form-stack">
        <Field label="Workflow file" error={error}>
          {p => <Textarea {...p} rows={12} value={text} onChange={e => { setText(e.target.value); setError(null) }} spellCheck={false} className="ui-mono" />}
        </Field>
        <div>
          <input ref={file} type="file" accept=".json,.yaml,.yml,application/json,text/yaml" className="ui-sr-only" aria-label="Choose a workflow file" onChange={async e => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setError(null) } }} />
          <Button size="sm" onClick={() => file.current?.click()}>Choose a file…</Button>
        </div>
      </div>
    </Dialog>
  )
}

export function VersionsDialog({ currentId, currentVersion, open, onClose }: { currentId: string | null; currentVersion: number | null; open: boolean; onClose: () => void }) {
  const versions = useAsync(s => listVersions(currentId as string, s), [currentId], open && !!currentId)
  const list: WorkflowVersion[] = versions.data ?? []
  const newest = list[0]?.version
  return (
    <Dialog open={open} onClose={onClose} title="Versions" description="Saving never changes an existing version, and a run always stays on the version it started with." footer={<Button onClick={onClose}>Close</Button>}>
      {versions.error ? (
        <ErrorNotice error={versions.error} onRetry={versions.refresh} subject="the versions" />
      ) : versions.loading && !versions.data ? (
        <p className="ui-muted">Loading…</p>
      ) : (
        <ul className="wf-versions">
          {list.map(v => (
            <li key={v.id}>
              <strong>Version {v.version}</strong>
              {v.version === newest && <Badge tone="success">Newest</Badge>}
              {v.id === currentId && <Badge tone="info">Open now</Badge>}
              {v.status !== 'active' && <Badge tone="muted">{v.status}</Badge>}
              <span className="ui-muted">{v.created_by ? `${v.created_by}, ` : ''}{relativeTime(v.created_at)}</span>
              {v.id !== currentId && (
                <a href={`#/workflows/${encodeURIComponent(v.id)}`} onClick={onClose}>{v.version === newest ? 'Open' : 'Open (read-only)'}</a>
              )}
            </li>
          ))}
        </ul>
      )}
      {currentVersion !== null && newest !== undefined && currentVersion < newest && <Callout tone="warning" title={`This is version ${currentVersion}, not the newest`}>Use “Restore this version” on the page to base a new version on it.</Callout>}
    </Dialog>
  )
}

export function TestRunDialog({ def, open, onClose, onStart, busy, error }: { def: WfDef; open: boolean; onClose: () => void; onStart: (vars: Record<string, string>) => void; busy: boolean; error: string | null }) {
  const names = Object.keys(def.variables ?? {})
  const [values, setValues] = useState<Record<string, string>>({})
  useEffect(() => {
    if (open) setValues({})
  }, [open])
  const missing = names.filter(n => def.variables?.[n]?.required && def.variables[n].default === undefined && !(values[n] ?? '').trim())
  return (
    <Dialog open={open} onClose={onClose} title="Start a test run" description="This runs the saved version for real. Steps that act outside PatchQuest still stop for approval." footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" icon="play" loading={busy} disabled={missing.length > 0} onClick={() => onStart(Object.fromEntries(Object.entries(values).filter(([, v]) => v !== '')))}>Start run</Button></>}>
      <div className="form-stack">
        {names.length === 0 && <p className="ui-muted">This workflow has no variables to fill in.</p>}
        {names.map(n => {
          const spec = def.variables?.[n] ?? {}
          return (
            <Field key={n} label={n} optional={!spec.required} hint={spec.default !== undefined ? `Default: ${String(spec.default)}` : undefined}>
              {p => <Input {...p} value={values[n] ?? ''} onChange={e => setValues(v => ({ ...v, [n]: e.target.value }))} placeholder={spec.default !== undefined ? String(spec.default) : ''} />}
            </Field>
          )
        })}
        {error && <p className="ui-field__error" role="alert">{error}</p>}
      </div>
    </Dialog>
  )
}
