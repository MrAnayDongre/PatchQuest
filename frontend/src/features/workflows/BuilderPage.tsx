import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type KeyboardEvent as RKeyboardEvent } from 'react'
import { ApiError } from '../../api/errors'
import { getTemplate, getWorkflow, listActions, listVersions, saveWorkflow, startWorkflowRun, problemsFromError } from '../../api/workflows'
import { useApp } from '../../app/AppContext'
import { isTypingTarget } from '../../app/shortcuts'
import { Icon } from '../../design/icons'
import { Callout, Dialog, useToast } from '../../design/overlay'
import { Badge, Button, IconButton, Segmented, Skeleton, TabPanel, Tabs } from '../../design/primitives'
import { useAsync } from '../../hooks/useAsync'
import { useNarrow, useServerValidation } from '../../hooks/useValidation'
import { friendlyError } from '../../api/errors'
import { clientCheck, mergeProblems, problemsByNode, type ActionMeta } from '../../lib/workflow/clientCheck'
import { PERMISSION_COPY } from '../../lib/workflow/copy'
import { canRedo, canUndo, historyReducer, initHistory } from '../../lib/workflow/history'
import { autoLayout, ensureLayout, GRID, layoutFrom, placeNear, snapPos, withLayout } from '../../lib/workflow/layout'
import { addNode, duplicateNodes, emptyDefinition, normalize, removeEdge, removeNodes } from '../../lib/workflow/model'
import { clearDraft, loadDraft, saveDraft } from '../../lib/workflow/storage'
import type { NodeType, Pos, WfDef } from '../../lib/workflow/types'
import { screenToWorld, type Viewport } from '../../lib/workflow/viewport'
import { nextLabel, needsLabelChoice, labelOptions } from '../../lib/workflow/edgeRules'
import { addEdge } from '../../lib/workflow/model'
import { navigate, setNavGuard } from '../../lib/router'
import { Canvas, edgeKey } from './Canvas'
import { NodeConfig, WorkflowSettings } from './ConfigPanel'
import { ConnectDialog, ExportDialog, ImportDialog, NodeListView, Palette, ProblemsList, TestRunDialog, VersionsDialog } from './BuilderParts'
import { ErrorNotice } from '../common'

interface Doc {
  def: WfDef
  positions: Record<string, Pos>
}

const stable = (d: WfDef) => JSON.stringify(d)

export default function BuilderPage({ id, template, runIntent }: { id: string | null; template?: string; runIntent?: boolean }) {
  const isNew = id === null
  const storageKey = id ?? 'new'
  const toast = useToast()
  const { refreshWorkflows } = useApp()
  const narrow = useNarrow()
  const actionsQ = useAsync(s => listActions(s), [])
  const actions: ActionMeta[] = actionsQ.data ?? []
  const actionMap = useMemo(() => new Map(actions.map(a => [a.name, a])), [actions])

  // ---- loading
  const [record, setRecord] = useState<{ id: string; name: string; version: number; workspace_id: string } | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [ready, setReady] = useState(isNew && !template)
  const [hist, dispatch] = useReducer(historyReducer<Doc>, undefined, () => initHistory<Doc>({ def: emptyDefinition(), positions: {} }))
  const doc = hist.present
  const savedJson = useRef(stable(emptyDefinition()))
  const [restoredDraft, setRestoredDraft] = useState(false)
  const [isOld, setIsOld] = useState(false)
  const [requires, setRequires] = useState<string[]>([])

  // Positions live in the definition's `layout`; nodes without one get an automatic spot.
  const start = useCallback((def: WfDef) => {
    const norm = normalize(def)
    const positions = ensureLayout(norm, layoutFrom(norm))
    dispatch({ type: 'reset', value: { def: norm, positions } })
    savedJson.current = stable(withLayout(norm, positions))
  }, [])

  useEffect(() => {
    let live = true
    const draft = loadDraft(storageKey)
    ;(async () => {
      try {
        if (isNew && template) {
          const t = await getTemplate(template)
          if (!live) return
          start(t.definition)
          setRequires(t.requires)
          savedJson.current = stable(emptyDefinition()) // a template is unsaved by definition
        } else if (!isNew) {
          const rec = await getWorkflow(id as string)
          if (!live) return
          setRecord({ id: rec.id, name: rec.name, version: rec.version, workspace_id: rec.workspace_id })
          start(rec.definition)
          listVersions(rec.id).then(all => {
            if (live && all.some(v => v.version > rec.version)) setIsOld(true)
          }).catch(() => {})
        }
        if (draft && (isNew || draft.baseId === id) && stable(draft.def) !== savedJson.current) {
          const restored = normalize(draft.def)
          dispatch({ type: 'reset', value: { def: restored, positions: ensureLayout(restored, layoutFrom(restored)) } })
          setRestoredDraft(true)
        } else if (isNew && !template && draft) {
          // an identical draft: nothing to restore
        }
        setReady(true)
      } catch (e) {
        if (live) setLoadError(e)
      }
    })()
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, template])

  const def = doc.def
  // what is saved, validated and exported: the definition plus the positions of its nodes
  const full = useMemo(() => withLayout(def, doc.positions), [def, doc.positions])
  const dirty = ready && stable(full) !== savedJson.current
  const readOnly = (isOld && !restoredDraft && !dirty) || narrow

  // ---- editing helpers
  const commit = useCallback((next: Doc, key?: string) => dispatch({ type: 'commit', value: next, key }), [])
  const setDef = useCallback((next: WfDef, key?: string) => {
    const positions = ensureLayout(next, doc.positions)
    commit({ def: next, positions }, key)
  }, [commit, doc.positions])
  const [selection, setSelection] = useState<ReadonlySet<string>>(new Set())
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [viewport, setViewport] = useState<Viewport>({ x: 48, y: 48, zoom: 1 })
  const [fitSignal, setFitSignal] = useState(0)
  const [panel, setPanel] = useState<'step' | 'workflow' | 'problems'>('workflow')
  const [view, setView] = useState<'canvas' | 'list'>('canvas')
  const [dialog, setDialog] = useState<null | 'import' | 'export' | 'versions' | 'test' | 'connect' | 'leave'>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const configRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (ready) setFitSignal(n => n + 1)
  }, [ready])
  useEffect(() => {
    if (ready && runIntent && record) setDialog('test')
  }, [ready, runIntent, record])

  // positions and drafts persist quietly
  useEffect(() => {
    if (!ready) return
    const t = window.setTimeout(() => {
      if (dirty) saveDraft(storageKey, { def: full, savedAt: Date.now(), baseId: id })
      else clearDraft(storageKey)
    }, 400)
    return () => window.clearTimeout(t)
  }, [ready, full, dirty, storageKey, id])

  const select = useCallback((ids: string[], additive: boolean) => {
    setSelectedEdge(null)
    setSelection(prev => {
      if (!additive) return new Set(ids)
      const next = new Set(prev)
      for (const i of ids) next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
    if (ids.length === 1) setPanel(p => (p === 'workflow' ? 'step' : p))
  }, [])

  const only = selection.size === 1 ? [...selection][0] : null
  const openConfig = useCallback((nodeId: string) => {
    setSelection(new Set([nodeId]))
    setPanel('step')
    window.setTimeout(() => configRef.current?.querySelector<HTMLElement>('input, textarea, select')?.focus(), 30)
  }, [])

  const addStep = useCallback((type: NodeType, at?: Pos) => {
    if (readOnly) return
    const r = wrap.current?.querySelector('.wf-canvas')?.getBoundingClientRect()
    const center = at ?? screenToWorld(viewport, { x: (r?.width ?? 600) / 2, y: (r?.height ?? 400) / 2 })
    const added = addNode(def, type)
    const p = at ? snapPos({ x: at.x - 104, y: at.y - 38 }) : placeNear(doc.positions, center)
    commit({ def: added.def, positions: { ...doc.positions, [added.id]: p } })
    openConfig(added.id)
  }, [readOnly, viewport, def, doc.positions, commit, openConfig])

  const deleteSelection = useCallback(() => {
    if (readOnly) return
    if (selectedEdge) {
      const e = def.edges.find(x => edgeKey(x) === selectedEdge)
      if (e) setDef(removeEdge(def, e.from, e.to, e.when ?? null))
      setSelectedEdge(null)
      return
    }
    if (!selection.size) return
    const next = removeNodes(def, [...selection])
    commit({ def: next, positions: ensureLayout(next, doc.positions) })
    setSelection(new Set())
  }, [readOnly, selectedEdge, selection, def, doc.positions, commit, setDef])

  const duplicate = useCallback(() => {
    if (readOnly || !selection.size) return
    const r = duplicateNodes(def, [...selection])
    const positions = { ...doc.positions }
    const src = [...selection]
    r.ids.forEach((nid, i) => {
      const o = doc.positions[src[i]] ?? { x: 0, y: 0 }
      positions[nid] = { x: o.x + GRID * 2, y: o.y + GRID * 2 }
    })
    commit({ def: r.def, positions })
    setSelection(new Set(r.ids))
  }, [readOnly, selection, def, doc.positions, commit])

  const moveSelection = useCallback((dx: number, dy: number) => {
    if (readOnly || !selection.size) return
    const positions = { ...doc.positions }
    for (const sid of selection) positions[sid] = { x: positions[sid].x + dx, y: positions[sid].y + dy }
    commit({ def, positions }, `move:${[...selection].join(',')}`)
  }, [readOnly, selection, doc.positions, def, commit])

  const connect = useCallback((from: string, to: string) => {
    if (readOnly) return
    const label = nextLabel(def, from)
    const r = addEdge(def, from, to, needsLabelChoice(def, from) ? label : null)
    if (!r.ok) {
      toast({ title: "Can't connect those", message: r.reason, tone: 'warning' })
      return
    }
    setDef(r.def)
    const opts = labelOptions(def.nodes.find(n => n.id === from)!)
    if (opts.length > 1) toast({ title: 'Connected', message: 'Change the connection type in the step settings if needed.', tone: 'info' })
  }, [readOnly, def, setDef, toast])

  const undo = useCallback(() => dispatch({ type: 'undo' }), [])
  const redo = useCallback(() => dispatch({ type: 'redo' }), [])

  // keys that work anywhere on the page except inside text fields
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || document.querySelector('[role="dialog"]')) return
      const mod = e.ctrlKey || e.metaKey
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        e.shiftKey ? redo() : undo()
      } else if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        redo()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [undo, redo])

  const onCanvasKey = (e: RKeyboardEvent<HTMLDivElement>) => {
    if (isTypingTarget(e.target)) return
    const mod = e.ctrlKey || e.metaKey
    const step = (e.shiftKey ? 5 : 1) * GRID
    switch (true) {
      case mod && e.key.toLowerCase() === 'd': e.preventDefault(); duplicate(); break
      case mod && e.key.toLowerCase() === 'a': e.preventDefault(); select(def.nodes.map(n => n.id), false); break
      case e.key === 'Delete' || e.key === 'Backspace': e.preventDefault(); deleteSelection(); break
      case e.key === 'ArrowLeft': e.preventDefault(); moveSelection(-step, 0); break
      case e.key === 'ArrowRight': e.preventDefault(); moveSelection(step, 0); break
      case e.key === 'ArrowUp': e.preventDefault(); moveSelection(0, -step); break
      case e.key === 'ArrowDown': e.preventDefault(); moveSelection(0, step); break
      case e.key === 'Escape': setSelection(new Set()); setSelectedEdge(null); break
      case e.key === 'Enter' && only !== null: e.preventDefault(); openConfig(only); break
      case e.key.toLowerCase() === 'c' && !mod && only !== null && !readOnly: e.preventDefault(); setDialog('connect'); break
      case e.key === '0' && !mod: setFitSignal(n => n + 1); break
      default:
    }
  }

  // ---- validation
  const local = useMemo(() => clientCheck(def, actions.length ? actions : undefined), [def, actions])
  const { verdict, checking, error: validateError } = useServerValidation(full, ready)
  const current = verdict?.def === full ? verdict : null
  const problems = mergeProblems(current ? current.problems : null, local)
  const byNode = useMemo(() => problemsByNode(problems), [problems])
  const canSave = !!current?.ok && !readOnly && dirty !== undefined && !narrow
  const saveBlockedReason = readOnly ? 'This version is read-only.' : checking || !current ? 'Checking with the server…' : !current.ok ? 'Fix the problems first.' : null

  // ---- save
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<unknown>(null)
  const doSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const r = await saveWorkflow(full, record?.workspace_id)
      savedJson.current = stable(full)
      clearDraft(storageKey)
      setNavGuard(null)
      void refreshWorkflows()
      toast({ title: `Saved as version ${r.version}`, message: 'Earlier versions and runs are unchanged.', tone: 'success' })
      navigate(`#/workflows/${encodeURIComponent(r.id)}`)
    } catch (e) {
      setSaveError(e)
    } finally {
      setSaving(false)
    }
  }
  const saveProblems = problemsFromError(saveError)

  // ---- leaving with unsaved changes
  const [pendingLeave, setPendingLeave] = useState<string | null>(null)
  useEffect(() => {
    if (!dirty) {
      setNavGuard(null)
      return
    }
    setNavGuard(target => {
      setPendingLeave(target)
      setDialog('leave')
      return false
    })
    const before = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', before)
    return () => {
      window.removeEventListener('beforeunload', before)
      setNavGuard(null)
    }
  }, [dirty])

  // ---- test run
  const [starting, setStarting] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const startRun = async (vars: Record<string, string>) => {
    if (!record) return
    setStarting(true)
    setRunError(null)
    try {
      const r = await startWorkflowRun(record.id, vars)
      setDialog(null)
      navigate(`#/workflows/runs/${encodeURIComponent(r.run_id)}`)
    } catch (e) {
      const f = friendlyError(e, 'this workflow')
      setRunError(`${f.title}. ${f.message}`)
    } finally {
      setStarting(false)
    }
  }

  const importDef = (next: WfDef) => commit({ def: next, positions: ensureLayout(next, layoutFrom(next)) })

  if (loadError) {
    const status = loadError instanceof ApiError ? loadError.status : 0
    return (
      <div className="page">
        {status === 403 ? <Callout tone="warning" title={PERMISSION_COPY.view.title}>{PERMISSION_COPY.view.message}</Callout> : <ErrorNotice error={loadError} subject="this workflow" />}
        <p><a href="#/workflows">Back to workflows</a></p>
      </div>
    )
  }
  if (!ready) return <div className="page"><Skeleton width="100%" height={320} /></div>

  const selectedProblems = only ? byNode.get(only) ?? [] : []
  const dirtyLabel = dirty ? 'Unsaved changes' : record ? `Version ${record.version}` : 'Not saved yet'

  return (
    <div className="wf-builder" ref={wrap}>
      <header className="wf-toolbar">
        <div className="wf-toolbar__title">
          <a href="#/workflows" className="wf-back">Workflows</a>
          <Icon name="chevron-right" size={14} />
          <h1 className="wf-toolbar__name ui-truncate">{def.name || 'Untitled workflow'}</h1>
          <Badge tone={dirty ? 'warning' : 'neutral'}>{dirtyLabel}</Badge>
        </div>
        <div className="wf-toolbar__actions">
          <IconButton icon="undo" label="Undo (Ctrl+Z)" disabled={!canUndo(hist) || readOnly} onClick={undo} />
          <IconButton icon="redo" label="Redo (Ctrl+Shift+Z)" disabled={!canRedo(hist) || readOnly} onClick={redo} />
          <Segmented<'canvas' | 'list'> label="View" value={view} onChange={setView} options={[{ id: 'canvas', label: 'Canvas' }, { id: 'list', label: 'List' }]} />
          <Button icon="upload" onClick={() => setDialog('import')} disabled={readOnly}>Import</Button>
          <Button icon="download" onClick={() => setDialog('export')}>Export</Button>
          {record && <Button onClick={() => setDialog('versions')}>Versions</Button>}
          <Button icon="play" disabled={!record || dirty} title={!record ? 'Save the workflow first' : dirty ? 'A test run uses the saved version. Save your changes first.' : undefined} onClick={() => setDialog('test')}>Test run</Button>
          <Button variant="primary" loading={saving} disabled={!canSave || !dirty} title={saveBlockedReason ?? (dirty ? undefined : 'No changes to save')} onClick={doSave}>{record ? 'Save new version' : 'Save'}</Button>
        </div>
      </header>

      {narrow && <Callout tone="info" title="Editing needs a larger screen">You can read this workflow here. To change it, open PatchQuest on a tablet or computer.</Callout>}
      {isOld && !restoredDraft && !dirty && !narrow && (
        <Callout tone="warning" title="You are looking at an older version" action={<Button size="sm" onClick={() => { setIsOld(false); setRestoredDraft(true) }}>Restore this version</Button>}>
          It is read-only. Restoring lets you edit it, and saving creates a new version on top of the newest one.
        </Callout>
      )}
      {restoredDraft && dirty && (
        <Callout tone="info" title="Restored your unsaved draft" action={<Button size="sm" onClick={() => { clearDraft(storageKey); window.location.reload() }}>Discard draft</Button>}>
          It was kept in this browser from your last visit.
        </Callout>
      )}
      {requires.length > 0 && (
        <Callout tone="muted" title={`This template needs: ${requires.join(', ')}`}>
          You can design and save it now. Steps that use {requires.join(' and ')} fail with a clear message when a run reaches them unless that connector is connected.
        </Callout>
      )}
      {saveError != null && (
        saveProblems ? (
          <Callout tone="danger" title="The server rejected this workflow">{saveProblems.length} {saveProblems.length === 1 ? 'problem is' : 'problems are'} listed under Problems.</Callout>
        ) : saveError instanceof ApiError && saveError.status === 403 ? (
          <Callout tone="warning" title={PERMISSION_COPY.define.title}>{PERMISSION_COPY.define.message}</Callout>
        ) : saveError instanceof ApiError && saveError.code === 'workspace_required' ? (
          <Callout tone="warning" title="Choose a workspace">This server has several workspaces and the save needs to know which one. Open the workflow from its workspace or contact an admin.</Callout>
        ) : (
          <ErrorNotice error={saveError} subject="this workflow" />
        )
      )}
      {validateError && !checking && <Callout tone="warning" title="Couldn't check with the server">Showing quick local checks only. You can keep editing; saving needs the server's answer.</Callout>}

      <div className="wf-layout">
        <aside className="wf-layout__palette">
          <Palette onAdd={t => addStep(t)} disabled={readOnly} />
        </aside>
        <section className="wf-layout__canvas" aria-label="Workflow editor">
          {view === 'canvas' ? (
            <Canvas
              def={def}
              positions={doc.positions}
              selection={selection}
              selectedEdge={selectedEdge}
              readOnly={readOnly}
              problems={byNode}
              actions={actionMap}
              viewport={viewport}
              onViewport={setViewport}
              fitSignal={fitSignal}
              onSelect={select}
              onSelectEdge={k => { setSelectedEdge(k); if (k) setSelection(new Set()) }}
              onMoveEnd={p => commit({ def, positions: p })}
              onConnect={connect}
              onOpenConfig={openConfig}
              onDropNew={(t, at) => addStep(t, at)}
              onKeyDown={onCanvasKey}
            />
          ) : (
            <NodeListView def={def} selection={selection} problems={byNode} actions={actionMap} onSelect={nid => select([nid], false)} onOpen={openConfig} />
          )}
          <p className="wf-hint ui-muted">
            Tab moves between steps. Enter edits one. Arrow keys move it. <kbd className="ui-kbd">C</kbd> connects it to another step.
          </p>
        </section>
        <aside className="wf-layout__panel" ref={configRef}>
          <Tabs
            label="Editor panel"
            idPrefix="wfp"
            value={panel}
            onChange={v => setPanel(v as typeof panel)}
            tabs={[
              { id: 'step', label: 'Step' },
              { id: 'workflow', label: 'Workflow' },
              { id: 'problems', label: 'Problems', count: problems.length },
            ]}
          />
          <TabPanel idPrefix="wfp" id="step" active={panel === 'step'}>
            {only ? (
              <NodeConfig def={def} nodeId={only} actions={actions} problems={selectedProblems} readOnly={readOnly} onDef={setDef} onRenamed={(o, n) => setSelection(new Set([n]))} />
            ) : (
              <p className="ui-muted">{selection.size > 1 ? `${selection.size} steps selected. Move, duplicate or delete them together.` : 'Select a step to change its settings.'}</p>
            )}
          </TabPanel>
          <TabPanel idPrefix="wfp" id="workflow" active={panel === 'workflow'}>
            <WorkflowSettings def={def} onDef={setDef} readOnly={readOnly} />
          </TabPanel>
          <TabPanel idPrefix="wfp" id="problems" active={panel === 'problems'}>
            <ProblemsList problems={problems} checking={checking} ok={!!current?.ok} onSelect={nid => { setSelection(new Set([nid])); setPanel('step') }} />
            {saveProblems && (
              <ProblemsList problems={saveProblems} checking={false} ok={false} onSelect={nid => { setSelection(new Set([nid])); setPanel('step') }} />
            )}
          </TabPanel>
        </aside>
      </div>

      <ConnectDialog def={def} nodeId={only} open={dialog === 'connect'} onClose={() => setDialog(null)} onDef={setDef} />
      <ImportDialog open={dialog === 'import'} onClose={() => setDialog(null)} onImport={importDef} />
      <ExportDialog def={full} open={dialog === 'export'} onClose={() => setDialog(null)} />
      <VersionsDialog currentId={record?.id ?? null} currentVersion={record?.version ?? null} open={dialog === 'versions'} onClose={() => setDialog(null)} />
      <TestRunDialog def={def} open={dialog === 'test'} onClose={() => setDialog(null)} onStart={startRun} busy={starting} error={runError} />
      <Dialog
        open={dialog === 'leave'}
        onClose={() => { setDialog(null); setPendingLeave(null) }}
        title="Leave without saving?"
        description="Your changes are kept as a draft in this browser, so you can come back to them. They are not saved to the server."
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => { setDialog(null); setPendingLeave(null) }}>Keep editing</Button>
            <Button variant="danger" onClick={() => { const t = pendingLeave; setNavGuard(null); setDialog(null); if (t) navigate(t) }}>Leave</Button>
          </>
        }
      >
        <span />
      </Dialog>
    </div>
  )
}
