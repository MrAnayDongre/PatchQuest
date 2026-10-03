import { useEffect, useMemo, useState } from 'react'
import { ApiError, createRun, getEngines, getProviderStatus, listProviders } from '../api/client'
import { Button, Field, Input, Select, Switch, Textarea, Badge } from '../design/primitives'
import { Callout, Dialog, useToast } from '../design/overlay'
import { useApp } from '../app/AppContext'
import { buildCreateRequest, validateNewRun, type NewRunForm } from '../lib/newRun'
import { formatCount } from '../lib/format'
import { navigate, runHash } from '../lib/router'
import { useAsync } from '../hooks/useAsync'
import { ErrorNotice } from './common'

const LAST_KEY = 'patchquest-last-run-form'

function loadLast(): Partial<NewRunForm> {
  try {
    return JSON.parse(localStorage.getItem(LAST_KEY) ?? '{}') as Partial<NewRunForm>
  } catch {
    return {}
  }
}

const blank = (): NewRunForm => ({ repoPath: '', provider: 'mock', model: '', runtime: 'local', dryRun: false, baseUrl: '', workspaceId: '', ...loadLast(), task: '' })

export function NewRunDialog() {
  const { newRunOpen, closeNewRun, refreshRuns } = useApp()
  const toast = useToast()
  const [form, setForm] = useState<NewRunForm>(blank)
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [workspaces, setWorkspaces] = useState<string[]>([])

  const catalogue = useAsync(s => listProviders(s), [], newRunOpen)
  const status = useAsync(s => getProviderStatus(s), [], newRunOpen)
  const engines = useAsync(s => getEngines(s), [], newRunOpen)

  useEffect(() => {
    if (newRunOpen) {
      setForm(blank())
      setTouched(false)
      setError(null)
    }
  }, [newRunOpen])

  const provider = catalogue.data?.find(p => p.name === form.provider)
  const providerStatus = status.data?.find(s => s.name === form.provider)
  const engine = engines.data?.find(e => e.engine === form.provider)
  const errors = useMemo(() => validateNewRun(form), [form])
  const set = <K extends keyof NewRunForm>(k: K, v: NewRunForm[K]) => setForm(f => ({ ...f, [k]: v }))

  const changeProvider = (name: string) => {
    const p = catalogue.data?.find(x => x.name === name)
    setForm(f => ({ ...f, provider: name, model: p?.default_model ?? '', baseUrl: '' }))
  }

  const submit = async () => {
    setTouched(true)
    if (Object.keys(errors).length) return
    setBusy(true)
    setError(null)
    try {
      const run = await createRun(buildCreateRequest(form))
      try {
        localStorage.setItem(LAST_KEY, JSON.stringify({ repoPath: form.repoPath, provider: form.provider, model: form.model, runtime: form.runtime }))
      } catch {
        // remembering the last choice is optional
      }
      closeNewRun()
      void refreshRuns()
      toast({ title: 'Run started', message: form.dryRun ? 'Dry run: nothing will be written.' : undefined, tone: 'success' })
      navigate(runHash(run.id))
    } catch (err) {
      if (err instanceof ApiError && err.code === 'workspace_required') {
        const list = (err.data?.workspaces as string[] | undefined) ?? []
        setWorkspaces(list)
        setForm(f => ({ ...f, workspaceId: list[0] ?? '' }))
      }
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const fieldErr = (k: keyof typeof errors) => (touched ? errors[k] ?? null : null)

  return (
    <Dialog
      open={newRunOpen}
      onClose={closeNewRun}
      title="New run"
      description="Describe a task. PatchQuest works in an isolated copy and only touches your repository at the end, after checks pass."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={closeNewRun}>Cancel</Button>
          <Button variant="primary" icon="play" loading={busy} onClick={submit}>Start run</Button>
        </>
      }
    >
      <form className="form-stack" onSubmit={e => { e.preventDefault(); void submit() }}>
        <Field label="Repository folder" error={fieldErr('repoPath')} hint="The full path on the machine running PatchQuest.">
          {p => <Input {...p} value={form.repoPath} onChange={e => set('repoPath', e.target.value)} placeholder="/home/you/projects/my-app" autoComplete="off" spellCheck={false} />}
        </Field>
        <Field label="Task" error={fieldErr('task')} hint="For example: Fix the failing login test, or: Add input validation to the signup form.">
          {p => <Textarea {...p} value={form.task} onChange={e => set('task', e.target.value)} rows={4} />}
        </Field>
        <div className="form-row">
          <Field label="Provider">
            {p => (
              <Select {...p} value={form.provider} onChange={e => changeProvider(e.target.value)}>
                {(catalogue.data ?? [{ name: 'mock', display_name: 'Mock (no model calls)', default_model: '', models: [], api_key_env: null, base_url: null }]).map(c => {
                  const st = status.data?.find(s => s.name === c.name)
                  return (
                    <option key={c.name} value={c.name}>
                      {c.display_name}{st && !st.available ? ' (key not set)' : ''}
                    </option>
                  )
                })}
              </Select>
            )}
          </Field>
          {form.provider !== 'mock' && (
            <Field label="Model">
              {p => (
                <>
                  <Input {...p} list="model-options" value={form.model} onChange={e => set('model', e.target.value)} placeholder={provider?.default_model} autoComplete="off" />
                  <datalist id="model-options">
                    {(engine?.models.length ? engine.models : provider?.models ?? []).map(m => <option key={m} value={m} />)}
                  </datalist>
                </>
              )}
            </Field>
          )}
        </div>
        {form.provider === 'mock' && <p className="ui-muted">The mock provider makes no model calls. It is a safe way to see how a run looks.</p>}
        {providerStatus && !providerStatus.available && form.provider !== 'mock' && (
          <Callout tone="warning" title="This provider isn't ready">
            {provider?.api_key_env ? `Set the ${provider.api_key_env} environment variable on the server, then restart it.` : providerStatus.error ?? 'It is not available right now.'}
          </Callout>
        )}
        {engine && (
          <div className="engine-line" role="status">
            <Badge tone={engine.healthy ? 'success' : engine.available ? 'warning' : 'danger'}>{engine.healthy ? 'Engine healthy' : engine.available ? 'Engine reachable, degraded' : 'Engine not reachable'}</Badge>
            {engine.context_limit ? <span className="ui-muted">Context {formatCount(engine.context_limit)} tokens</span> : null}
            {!engine.model_loaded && engine.available && <span className="ui-muted">No model loaded</span>}
            {engine.last_error && <span className="ui-muted ui-truncate">{engine.last_error}</span>}
          </div>
        )}
        <fieldset className="radio-group radio-group--row">
          <legend className="ui-field__label">Where commands run</legend>
          {(['local', 'docker'] as const).map(r => (
            <label key={r} className="radio">
              <input type="radio" name="runtime" checked={form.runtime === r} onChange={() => set('runtime', r)} />
              <span>
                <strong>{r === 'local' ? 'This machine' : 'Docker container'}</strong>
                <span className="radio__text">{r === 'local' ? 'Fastest. Runs in an isolated copy of the repository.' : 'Extra isolation. Needs Docker to be installed.'}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <Switch checked={form.dryRun} onChange={v => set('dryRun', v)} label="Dry run" description="Plan and propose changes without writing anything to your repository." />
        <details className="advanced">
          <summary>Advanced</summary>
          <div className="form-stack">
            <Field label="Base URL" optional error={fieldErr('baseUrl')} hint="Only for self-hosted or OpenAI-compatible endpoints.">
              {p => <Input {...p} value={form.baseUrl} onChange={e => set('baseUrl', e.target.value)} placeholder={provider?.base_url ?? 'https://…'} spellCheck={false} />}
            </Field>
            <p className="ui-muted">To raise limits such as model calls, fork the run from a checkpoint and set the new limit there.</p>
          </div>
        </details>
        {workspaces.length > 0 && (
          <Field label="Workspace" hint="This server has several workspaces. Choose where the run belongs.">
            {p => (
              <Select {...p} value={form.workspaceId} onChange={e => set('workspaceId', e.target.value)}>
                {workspaces.map(w => <option key={w}>{w}</option>)}
              </Select>
            )}
          </Field>
        )}
        {!!error && <ErrorNotice error={error} subject="this run" />}
        <button type="submit" hidden />
      </form>
    </Dialog>
  )
}
