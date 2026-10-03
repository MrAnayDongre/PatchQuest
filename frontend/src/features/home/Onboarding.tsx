import { useState } from 'react'
import { createRun, getEngines, listProviders } from '../../api/client'
import { useApp } from '../../app/AppContext'
import { Badge, Button, Card, CardHeader, Field, Input, Select } from '../../design/primitives'
import { useToast } from '../../design/overlay'
import { useAsync } from '../../hooks/useAsync'
import { SAMPLE_TASK } from '../../lib/newRun'
import { navigate, runHash } from '../../lib/router'
import { ErrorNotice } from '../common'

export function Onboarding() {
  const { health, refreshRuns } = useApp()
  const engines = useAsync(s => getEngines(s), [])
  const providers = useAsync(s => listProviders(s), [])
  const [provider, setProvider] = useState('mock')
  const [repo, setRepo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [touched, setTouched] = useState(false)
  const toast = useToast()
  const healthyEngines = (engines.data ?? []).filter(e => e.healthy)
  const repoOk = /^([/~]|[A-Za-z]:[\\/])/.test(repo.trim())

  const go = async () => {
    setTouched(true)
    if (!repoOk) return
    setBusy(true)
    setError(null)
    try {
      const run = await createRun({ repo_path: repo.trim(), task: SAMPLE_TASK, provider, dry_run: true })
      void refreshRuns()
      toast({ title: 'Sample run started', message: 'It reads your repository and changes nothing.', tone: 'success' })
      navigate(runHash(run.id))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card aria-labelledby="onboard-title" className="onboarding">
      <CardHeader id="onboard-title" title="Try your first run" subtitle="Three quick steps. The sample task only reads your code." />
      <ol className="onboarding__steps">
        <li>
          <strong>Check the server and engines</strong>
          <p className="ui-muted">
            {health === 'ok' ? 'The PatchQuest server is running.' : 'Waiting for the server…'}{' '}
            {healthyEngines.length ? `Local engines ready: ${healthyEngines.map(e => e.engine).join(', ')}.` : 'No local engine detected. The mock provider works without one.'}
          </p>
          <Badge tone={health === 'ok' ? 'success' : 'warning'}>{health === 'ok' ? 'Server running' : 'Checking'}</Badge>
        </li>
        <li>
          <strong>Choose a provider</strong>
          <Field label="Provider" hint="Mock makes no model calls, so it is the quickest way to look around.">
            {p => (
              <Select {...p} value={provider} onChange={e => setProvider(e.target.value)}>
                {(providers.data ?? [{ name: 'mock', display_name: 'Mock (no model calls)' }]).map(c => (
                  <option key={c.name} value={c.name}>{c.display_name}</option>
                ))}
              </Select>
            )}
          </Field>
        </li>
        <li>
          <strong>Pick a repository</strong>
          <Field label="Repository folder" error={touched && !repoOk ? 'Enter a full path, starting with / or ~.' : null}>
            {p => <Input {...p} value={repo} onChange={e => setRepo(e.target.value)} placeholder="/home/you/projects/my-app" spellCheck={false} />}
          </Field>
        </li>
      </ol>
      {!!error && <ErrorNotice error={error} subject="the sample run" />}
      <Button variant="primary" icon="play" loading={busy} onClick={go}>Run the sample task</Button>
    </Card>
  )
}
