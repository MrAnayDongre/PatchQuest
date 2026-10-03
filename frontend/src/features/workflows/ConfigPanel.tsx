import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Field, Input, Select, Switch } from '../../design/primitives'
import { Callout } from '../../design/overlay'
import type { ActionMeta } from '../../lib/workflow/clientCheck'
import { condKind, emptyComparison, filterToRows, rowsToFilter, type Cond, type FilterRow } from '../../lib/workflow/condition'
import { connectorName, gateSentence, NODE_COPY, SIDE_EFFECT_COPY, TRIGGER_TYPES } from '../../lib/workflow/copy'
import { coerceAuto, coerceLike, splitDuration, toSeconds, type Unit } from '../../lib/workflow/duration'
import { labelOptions, labelText, nextLabel } from '../../lib/workflow/edgeRules'
import { addEdge, outgoingOf, removeEdge, renameNode, setConfig, setConfigKey, setEdgeLabel, setMaxVisits, setMeta, setTrigger, setVariables } from '../../lib/workflow/model'
import { availableRefs } from '../../lib/workflow/refs'
import { asRecord, isNodeType, type Problem, type WfDef, type WfNode, type WfVariable } from '../../lib/workflow/types'
import { ConditionEditor, KeyValueEditor, RefField } from './fields'
import { sideEffectTone } from '../../lib/eventCopy'

type OnDef = (next: WfDef, coalesceKey?: string) => void

function DurationField({ label, seconds, onChange, optional, hint }: { label: string; seconds: number | undefined; onChange: (s: number | undefined) => void; optional?: boolean; hint?: string }) {
  const init = splitDuration(seconds)
  const [unit, setUnit] = useState<Unit>(init.unit)
  const [amount, setAmount] = useState<number | ''>(init.amount)
  useEffect(() => {
    const s = splitDuration(seconds)
    if (toSeconds(amount, unit) !== seconds) {
      setAmount(s.amount)
      setUnit(s.unit)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seconds])
  return (
    <div className="wf-duration">
      <Field label={label} optional={optional} hint={hint}>
        {p => <Input {...p} type="number" min={1} value={amount} onChange={e => { const a = e.target.value === '' ? '' : Number(e.target.value); setAmount(a); onChange(toSeconds(a, unit)) }} />}
      </Field>
      <Field label={`${label} unit`} className="wf-duration__unit">
        {p => (
          <Select {...p} value={unit} onChange={e => { const u = e.target.value as Unit; setUnit(u); onChange(toSeconds(amount, u)) }}>
            <option value="seconds">seconds</option>
            <option value="minutes">minutes</option>
            <option value="hours">hours</option>
            <option value="days">days</option>
          </Select>
        )}
      </Field>
    </div>
  )
}

interface Props {
  def: WfDef
  nodeId: string
  actions: ActionMeta[]
  problems: Problem[]
  readOnly?: boolean
  onDef: OnDef
  onRenamed: (oldId: string, newId: string) => void
}

/** Settings for one step. Only the keys that step type accepts are offered, so a typo can't reach the server. */
export function NodeConfig({ def, nodeId, actions, problems, readOnly, onDef, onRenamed }: Props) {
  const node = def.nodes.find(n => n.id === nodeId)
  const [name, setName] = useState(nodeId)
  const [nameError, setNameError] = useState<string | null>(null)
  useEffect(() => {
    setName(nodeId)
    setNameError(null)
  }, [nodeId])
  const refs = useMemo(() => availableRefs(def, nodeId), [def, nodeId])
  if (!node) return null
  const cfg = asRecord(node.config)
  const set = (key: string, value: unknown, drop = true) => onDef(setConfigKey(def, nodeId, key, value, drop), `cfg:${nodeId}:${key}`)
  const action = actions.find(a => a.name === cfg.action)
  const copy = isNodeType(node.type) ? NODE_COPY[node.type] : { label: node.type, hint: '' }
  const commitName = () => {
    const r = renameNode(def, nodeId, name.trim())
    if (!r.ok) return setNameError(r.reason)
    setNameError(null)
    if (name.trim() !== nodeId) {
      onDef(r.def)
      onRenamed(nodeId, name.trim())
    }
  }

  return (
    <fieldset className="wf-config" disabled={readOnly}>
      <legend className="ui-sr-only">Settings for {nodeId}</legend>
      <div className="wf-config__head">
        <Badge>{copy.label}</Badge>
        <span className="ui-muted">{copy.hint}</span>
      </div>
      {problems.length > 0 && (
        <Callout tone="danger" title={problems.length === 1 ? 'One problem with this step' : `${problems.length} problems with this step`}>
          <ul className="wf-problems-inline">{problems.map((p, i) => <li key={i}>{p.message}</li>)}</ul>
        </Callout>
      )}
      <Field label="Step name" error={nameError} hint="Used in connections and in {{nodes.name…}} references.">
        {p => <Input {...p} value={name} onChange={e => setName(e.target.value)} onBlur={commitName} onKeyDown={e => e.key === 'Enter' && commitName()} spellCheck={false} />}
      </Field>

      {node.type === 'agent' && (
        <>
          <RefField label="Task" multiline value={String(cfg.task ?? '')} refs={refs} onChange={v => set('task', v, false)} hint="What the agent should do. Insert values from the trigger or earlier steps." />
          <RefField label="Repository folder" optional value={String(cfg.repo ?? '')} refs={refs} onChange={v => set('repo', v)} />
          <div className="form-row">
            <RefField label="Provider" optional value={String(cfg.provider ?? '')} refs={refs} onChange={v => set('provider', v)} />
            <RefField label="Model" optional value={String(cfg.model ?? '')} refs={refs} onChange={v => set('model', v)} />
          </div>
          <RefField label="Base URL" optional value={String(cfg.base_url ?? '')} refs={refs} onChange={v => set('base_url', v)} />
          <KeyValueEditor
            label="Run settings"
            hint="Optional limits for this agent run, such as agent.max_model_calls."
            value={asRecord(cfg.overrides)}
            refs={[]}
            coerce={(t) => coerceAuto(t)}
            keyPlaceholder="agent.setting"
            keyError={k => (k && !k.startsWith('agent.') ? 'Only agent.* settings can be changed.' : null)}
            addLabel="Add a setting"
            onChange={v => onDef(setConfigKey(def, nodeId, 'overrides', v, false), `cfg:${nodeId}:overrides`)}
          />
          <Switch checked={cfg.on_failure === 'continue'} onChange={v => set('on_failure', v ? 'continue' : '')} label="Continue if this step fails" description="Adds an “On failure” connection you can follow instead of stopping the workflow." />
        </>
      )}

      {node.type === 'action' && (
        <>
          <Field label="Action">
            {p => (
              <Select {...p} value={String(cfg.action ?? '')} onChange={e => set('action', e.target.value, false)}>
                <option value="">Choose an action…</option>
                {!!cfg.action && !action && <option value={String(cfg.action)}>{String(cfg.action)} (not in the catalogue)</option>}
                {actions.map(a => (
                  <option key={a.name} value={a.name}>{a.name}{a.requires_approval ? ' (needs approval)' : ''}</option>
                ))}
              </Select>
            )}
          </Field>
          {action && (
            <div className="wf-action-info">
              <Badge tone={sideEffectTone(action.side_effect)}>{SIDE_EFFECT_COPY[action.side_effect] ?? action.side_effect}</Badge>
              {!action.idempotent && <Badge tone="warning">Can't be safely repeated</Badge>}
              {action.connector && <Badge>{`Needs ${connectorName(action.connector)}`}</Badge>}
              {action.requires_approval && <Callout tone="warning" title="Needs a human gate">{gateSentence(action)} Put an Approval step on every path that leads here.</Callout>}
            </div>
          )}
          <KeyValueEditor label="Details" hint="What to send, such as the comment text." value={asRecord(cfg.params)} refs={refs} coerce={coerceLike} addLabel="Add a detail" onChange={v => onDef(setConfigKey(def, nodeId, 'params', v, false), `cfg:${nodeId}:params`)} />
          <Switch checked={cfg.on_failure === 'continue'} onChange={v => set('on_failure', v ? 'continue' : '')} label="Continue if this step fails" description="Adds an “On failure” connection you can follow instead of stopping the workflow." />
        </>
      )}

      {node.type === 'condition' && (
        <>
          <p className="ui-muted">Takes the “If true” connection when this holds, otherwise “If false”. Both are needed.</p>
          <ConditionEditor value={asRecord(cfg.if) as Cond} refs={refs} onChange={v => onDef(setConfig(def, nodeId, { ...cfg, if: v }), `cfg:${nodeId}:if`)} />
          {condKind(asRecord(cfg.if)) === 'compare' && !cfg.if && <Button size="sm" onClick={() => set('if', emptyComparison(), false)}>Start a condition</Button>}
        </>
      )}

      {node.type === 'approval' && (
        <>
          <RefField label="Question for the approver" multiline value={String(cfg.message ?? '')} refs={refs} onChange={v => set('message', v)} hint="Shown with the Approve and Deny buttons." />
          <DurationField label="Give up after" optional seconds={typeof cfg.timeout_s === 'number' ? cfg.timeout_s : undefined} onChange={s => set('timeout_s', s)} hint="No answer by then counts as denied, and the “No answer in time” connection is followed." />
        </>
      )}

      {node.type === 'wait_event' && (
        <>
          <RefField label="Event to wait for" value={String(cfg.event ?? '')} refs={refs} onChange={v => set('event', v, false)} placeholder="github.pull_request.merged" />
          <KeyValueEditor label="Only when" hint="Match on parts of the event, for example payload.number." value={asRecord(cfg.filter)} refs={refs} coerce={coerceLike} addLabel="Add a match" onChange={v => onDef(setConfigKey(def, nodeId, 'filter', Object.keys(v).length ? v : undefined, true), `cfg:${nodeId}:filter`)} />
          <DurationField label="Give up after" optional seconds={typeof cfg.timeout_s === 'number' ? cfg.timeout_s : undefined} onChange={s => set('timeout_s', s)} />
        </>
      )}

      {node.type === 'timer' && <DurationField label="Wait for" seconds={typeof cfg.seconds === 'number' ? cfg.seconds : undefined} onChange={s => set('seconds', s, false)} hint="Between 1 second and 90 days." />}

      {node.type === 'end' && (
        <Field label="Finish as">
          {p => (
            <Select {...p} value={String(cfg.result ?? 'success')} onChange={e => set('result', e.target.value, false)}>
              <option value="success">Success</option>
              <option value="failure">Failure</option>
            </Select>
          )}
        </Field>
      )}

      {node.type !== 'end' && (
        <Field label="Most times this step may run" hint="Leave at 1 unless this step is part of a loop. Loops must have a limit." >
          {p => <Input {...p} type="number" min={1} max={20} value={node.max_visits ?? 1} onChange={e => onDef(setMaxVisits(def, nodeId, e.target.value === '' ? undefined : Number(e.target.value)), `visits:${nodeId}`)} />}
        </Field>
      )}

      <Connections def={def} node={node} onDef={onDef} readOnly={readOnly} />
    </fieldset>
  )
}

/** What this step leads to. Also the keyboard route to connect steps: pick a target and a label, press Connect. */
export function Connections({ def, node, onDef, readOnly }: { def: WfDef; node: WfNode; onDef: OnDef; readOnly?: boolean }) {
  const outgoing = outgoingOf(def, node.id)
  const options = labelOptions(node)
  const targets = def.nodes
  const [target, setTarget] = useState('')
  const [when, setWhen] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setError(null)
    setWhen(nextLabel(def, node.id) ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id, def.edges.length])
  if (node.type === 'end') return <p className="ui-muted">An end step has no connections out.</p>
  const connect = () => {
    if (!target) return setError('Choose which step comes next.')
    const r = addEdge(def, node.id, target, when || null)
    if (!r.ok) return setError(r.reason)
    setError(null)
    setTarget('')
    onDef(r.def)
  }
  return (
    <section className="wf-connections" aria-label="Connections from this step">
      <h3 className="wf-section-title">Leads to</h3>
      {outgoing.length === 0 && <p className="ui-muted">Nothing yet. Drag from the dot on the right of the step, or connect it below.</p>}
      <ul className="wf-conn-list">
        {outgoing.map(e => (
          <li key={`${e.to}:${e.when ?? ''}`} className="wf-conn">
            <span className="wf-conn__to ui-truncate">{e.to}</span>
            {options.length > 1 ? (
              <Select aria-label={`Connection to ${e.to}`} value={e.when ?? ''} disabled={readOnly} onChange={ev => {
                const r = setEdgeLabel(def, node.id, e.to, e.when ?? null, ev.target.value || null)
                if (r.ok) onDef(r.def)
                else setError(r.reason)
              }}>
                {options.map(o => <option key={o.value ?? 'none'} value={o.value ?? ''}>{o.label}</option>)}
              </Select>
            ) : (
              <span className="ui-muted">{labelText(e.when) || 'Next'}</span>
            )}
            <Button size="sm" variant="ghost" disabled={readOnly} aria-label={`Remove connection to ${e.to}`} onClick={() => onDef(removeEdge(def, node.id, e.to, e.when ?? null))}>Remove</Button>
          </li>
        ))}
      </ul>
      {!readOnly && (
        <div className="wf-conn-add">
          <Field label="Connect to">
            {p => (
              <Select {...p} value={target} onChange={e => setTarget(e.target.value)}>
                <option value="">Choose a step…</option>
                {targets.map(n => <option key={n.id} value={n.id}>{n.id}</option>)}
              </Select>
            )}
          </Field>
          {options.length > 1 && (
            <Field label="When">
              {p => (
                <Select {...p} value={when} onChange={e => setWhen(e.target.value)}>
                  {options.map(o => <option key={o.value ?? 'none'} value={o.value ?? ''}>{o.label}</option>)}
                </Select>
              )}
            </Field>
          )}
          <Button icon="link" onClick={connect}>Connect</Button>
        </div>
      )}
      {error && <p className="ui-field__error" role="alert">{error}</p>}
    </section>
  )
}

/** Workflow-level settings: name, description, trigger with structured filter rows, variables. */
export function WorkflowSettings({ def, onDef, readOnly }: { def: WfDef; onDef: OnDef; readOnly?: boolean }) {
  // Rows live locally so a blank row can exist while it is being filled in; only complete rows reach the definition.
  const [rows, setLocalRows] = useState<FilterRow[]>(() => filterToRows(def.trigger?.filter))
  const filterJson = JSON.stringify(def.trigger?.filter ?? {})
  useEffect(() => {
    if (JSON.stringify(rowsToFilter(rows)) !== filterJson) setLocalRows(filterToRows(def.trigger?.filter))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterJson])
  const setRows = (next: FilterRow[]) => {
    setLocalRows(next)
    const filter = rowsToFilter(next)
    if (JSON.stringify(filter) !== filterJson) onDef(setTrigger(def, { filter }), 'trigger-filter')
  }
  const vars = Object.entries(def.variables ?? {}) as [string, WfVariable][]
  const manual = (def.trigger?.type ?? 'manual') === 'manual'
  return (
    <fieldset className="wf-config" disabled={readOnly}>
      <legend className="ui-sr-only">Workflow settings</legend>
      <Field label="Workflow name" hint="Saving under the same name creates a new version.">
        {p => <Input {...p} value={def.name ?? ''} onChange={e => onDef(setMeta(def, { name: e.target.value }), 'name')} />}
      </Field>
      <Field label="Description" optional>
        {p => <Input {...p} value={def.description ?? ''} onChange={e => onDef(setMeta(def, { description: e.target.value }), 'description')} />}
      </Field>

      <h3 className="wf-section-title">When it starts</h3>
      <Field label="Trigger">
        {p => (
          <Select {...p} value={def.trigger?.type ?? 'manual'} onChange={e => onDef(setTrigger(def, { type: e.target.value }))}>
            {def.trigger?.type && !TRIGGER_TYPES.some(t => t.id === def.trigger?.type) && <option value={def.trigger.type}>{def.trigger.type}</option>}
            {TRIGGER_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </Select>
        )}
      </Field>
      {!manual && (
        <fieldset className="wf-kv">
          <legend className="ui-field__label">Only when the event matches</legend>
          {rows.map((r, i) => (
            <div className="wf-kv__row wf-kv__row--filter" key={i}>
              <Field label={`Event field ${i + 1}`}>{p => <Input {...p} value={r.path} placeholder="payload.label" onChange={e => setRows(rows.map((x, j) => (j === i ? { ...x, path: e.target.value } : x)))} spellCheck={false} />}</Field>
              <Field label={`Match type ${i + 1}`}>
                {p => (
                  <Select {...p} value={r.mode} onChange={e => setRows(rows.map((x, j) => (j === i ? { ...x, mode: e.target.value as FilterRow['mode'] } : x)))}>
                    <option value="equals">equals</option>
                    <option value="in">is one of</option>
                  </Select>
                )}
              </Field>
              <Field label={`Value ${i + 1}`} hint={r.mode === 'in' ? 'Separate with commas' : undefined}>{p => <Input {...p} value={r.value} onChange={e => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />}</Field>
              <Button size="sm" variant="ghost" aria-label={`Remove event field ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</Button>
            </div>
          ))}
          <Button size="sm" icon="plus" onClick={() => setRows([...rows, { path: '', mode: 'equals', value: '' }])}>Add a match</Button>
        </fieldset>
      )}

      <h3 className="wf-section-title">Variables</h3>
      <p className="ui-muted">Values you can use as {'{{vars.name}}'}. {manual ? 'Required ones are asked for when you start a run.' : 'Event-started workflows need a default for every variable.'}</p>
      {vars.map(([name, spec], i) => (
        <div className="wf-kv__row wf-kv__row--var" key={i}>
          <Field label={`Variable name ${i + 1}`}>
            {p => <Input {...p} value={name} onChange={e => onDef(setVariables(def, Object.fromEntries(vars.map(([k, v], j) => [j === i ? e.target.value : k, v]))), 'vars')} spellCheck={false} />}
          </Field>
          <Field label={`Default for ${name || 'variable'}`} optional>
            {p => <Input {...p} value={spec.default === undefined ? '' : String(spec.default)} onChange={e => onDef(setVariables(def, { ...(def.variables ?? {}), [name]: e.target.value === '' ? stripDefault(spec) : { ...spec, default: e.target.value } }), 'vars')} />}
          </Field>
          <Switch checked={spec.required === true} onChange={v => onDef(setVariables(def, { ...(def.variables ?? {}), [name]: { ...spec, required: v || undefined } }))} label="Required" />
          <Button size="sm" variant="ghost" aria-label={`Remove variable ${name}`} onClick={() => onDef(setVariables(def, Object.fromEntries(vars.filter((_, j) => j !== i))))}>Remove</Button>
        </div>
      ))}
      <Button size="sm" icon="plus" onClick={() => onDef(setVariables(def, { ...(def.variables ?? {}), [freshVar(vars.map(v => v[0]))]: {} }))}>Add a variable</Button>
    </fieldset>
  )
}

function stripDefault(spec: WfVariable): WfVariable {
  const { default: _unused, ...rest } = spec
  void _unused
  return rest
}

function freshVar(names: string[]): string {
  for (let i = 1; ; i++) if (!names.includes(`variable${i}`)) return `variable${i}`
}
