import { useEffect, useState } from 'react'
import { getSettings, updateSettings } from '../api/client'
import { currentToken, setToken } from '../api/auth'
import { useTheme } from '../theme/ThemeProvider'
import type { ThemeMode } from '../theme/theme'
import { Button, Card, CardHeader, Field, Input, Segmented, Skeleton, Switch } from '../design/primitives'
import { useToast } from '../design/overlay'
import { useAsync } from '../hooks/useAsync'
import { friendlyError } from '../api/errors'
import { ErrorNotice, PageHeader } from './common'

export default function SettingsPage() {
  const { mode, setMode } = useTheme()
  const toast = useToast()
  const [token, setTokenText] = useState(currentToken() ?? '')
  const settings = useAsync(() => getSettings(), [])
  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (settings.data) setDraft(settings.data)
  }, [settings.data])

  const save = async () => {
    setSaving(true)
    try {
      await updateSettings(draft)
      toast({ title: 'Settings saved', tone: 'success' })
    } catch (e) {
      const f = friendlyError(e, 'settings')
      toast({ title: f.title, message: f.message, tone: 'danger' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page page--narrow">
      <PageHeader title="Settings" />
      <Card>
        <CardHeader title="Appearance" />
        <Segmented<ThemeMode>
          label="Theme"
          value={mode}
          onChange={setMode}
          options={[
            { id: 'system', label: 'System' },
            { id: 'light', label: 'Light' },
            { id: 'dark', label: 'Dark' },
          ]}
        />
      </Card>
      <Card>
        <CardHeader title="Access token" subtitle="Only needed when the server requires one. It is stored in this browser." />
        <div className="form-row form-row--end">
          <Field label="Token">{p => <Input {...p} type="password" value={token} onChange={e => setTokenText(e.target.value)} autoComplete="off" placeholder="Paste a token" />}</Field>
          <div className="form-actions">
            <Button variant="primary" onClick={() => { setToken(token.trim() || null); toast({ title: token.trim() ? 'Token saved' : 'Token removed', tone: 'success' }) }}>Save</Button>
            <Button variant="ghost" onClick={() => { setToken(null); setTokenText('') }}>Remove</Button>
          </div>
        </div>
      </Card>
      <Card>
        <CardHeader title="Safety" subtitle="Defaults for new runs on this server. Command approvals are always enforced." />
        {settings.loading && !settings.data ? (
          <Skeleton width="100%" height={80} />
        ) : settings.error ? (
          <ErrorNotice error={settings.error} onRetry={settings.refresh} subject="the settings" />
        ) : (
          <div className="form-stack">
            <Switch checked={draft.allow_network === true} onChange={v => setDraft(d => ({ ...d, allow_network: v }))} label="Allow network access" description="Lets commands in a run reach the internet." />
            <Switch checked={draft.allow_outside_repo === true} onChange={v => setDraft(d => ({ ...d, allow_outside_repo: v }))} label="Allow files outside the repository" description="Lets a run read paths beyond the repository folder." />
            <div><Button variant="primary" loading={saving} onClick={save}>Save changes</Button></div>
          </div>
        )}
      </Card>
    </div>
  )
}
