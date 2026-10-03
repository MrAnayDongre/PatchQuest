import { useEffect, useState } from 'react'
import {
  addMemory, clearPreference, forgetMemory, getRepoProfile, listMemories, listPreferences, listRepositories, registerRepository, setPreference, setProfileField,
  type MemoryItem, type Preference,
} from '../../api/platform'
import { friendlyError } from '../../api/errors'
import { useApp } from '../../app/AppContext'
import { DataTable } from '../../design/data'
import { Callout, Dialog, useToast } from '../../design/overlay'
import { Badge, Button, Card, CardHeader, EmptyState, Field, IconButton, Input, Select, Skeleton, TabPanel, Tabs, Textarea } from '../../design/primitives'
import { useAsync } from '../../hooks/useAsync'
import { baseName, formatPercent, humanize, relativeTime } from '../../lib/format'
import { memorySourceLabel, preferenceOrigin, profileSourceLabel, showValue, valueFromText, valueToText } from '../../lib/platform'
import { buildHash, navigate, useRoute } from '../../lib/router'
import { ErrorNotice, PageHeader } from '../common'
import { WorkspaceGate, WorkspaceSelect } from '../workspace'

const pathHash = (path: string) => buildHash('repositories', {}, path ? { path } : {})

export default function RepositoriesPage() {
  const { can } = useApp()
  const [registering, setRegistering] = useState(false)
  const canRegister = can('repository.manage')
  return (
    <div className="page">
      <PageHeader
        title="Repositories"
        subtitle="What PatchQuest has learned about a repository, what it remembers, and the preferences that shape its runs."
        actions={<><WorkspaceSelect /><Button icon="plus" disabled={!canRegister} title={canRegister ? undefined : 'You need the repository.manage permission to register a repository.'} onClick={() => setRegistering(true)}>Register</Button></>}
      />
      <WorkspaceGate>{ws => <Body workspaceId={ws} registering={registering} setRegistering={setRegistering} />}</WorkspaceGate>
    </div>
  )
}

function Body({ workspaceId, registering, setRegistering }: { workspaceId: string; registering: boolean; setRegistering: (v: boolean) => void }) {
  const route = useRoute()
  const path = route.query.path ?? ''
  const [draft, setDraft] = useState(path)
  useEffect(() => setDraft(path), [path])
  const repos = useAsync(s => listRepositories(workspaceId, s), [workspaceId])

  return (
    <>
      <Card>
        <CardHeader title="Repositories in this workspace" />
        {repos.error ? (
          <ErrorNotice error={repos.error} onRetry={repos.refresh} subject="the repositories" />
        ) : !repos.data ? (
          <Skeleton width="100%" height={60} />
        ) : repos.data.length === 0 ? (
          <p className="ui-muted">No repositories are registered. A single-user local server needs no registration: every folder you start a run in is yours. Enter a path below to see what PatchQuest knows about it.</p>
        ) : (
          <ul className="repo-list">
            {repos.data.map(r => (
              <li key={r.id}>
                <a href={pathHash(r.path)} className="repo-list__item" aria-current={r.path === path ? 'true' : undefined}>
                  <strong>{r.name || baseName(r.path)}</strong>
                  <span className="ui-mono ui-muted ui-wrap">{r.path}</span>
                </a>
              </li>
            ))}
          </ul>
        )}
        <form className="path-form" onSubmit={e => { e.preventDefault(); if (draft.trim()) navigate(pathHash(draft.trim())) }}>
          <Field label="Repository path" hint="The folder on the server, for example /home/me/projects/app.">{p => <Input {...p} className="ui-mono" value={draft} onChange={e => setDraft(e.target.value)} autoComplete="off" spellCheck={false} />}</Field>
          <Button type="submit" variant="primary" disabled={!draft.trim()}>View</Button>
        </form>
      </Card>

      {path ? <Detail key={`${workspaceId}:${path}`} workspaceId={workspaceId} path={path} /> : (
        <Card><EmptyState icon="file" title="Pick a repository">Choose one above or enter a path to see its profile, memory and preferences.</EmptyState></Card>
      )}

      <RegisterDialog open={registering} workspaceId={workspaceId} onClose={() => setRegistering(false)} onDone={() => { setRegistering(false); void repos.refresh() }} />
    </>
  )
}

function RegisterDialog({ open, workspaceId, onClose, onDone }: { open: boolean; workspaceId: string; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) { setPath(''); setName(''); setError(null) } }, [open])
  const submit = async () => {
    if (!path.trim()) return
    setBusy(true)
    setError(null)
    try {
      const repo = await registerRepository(workspaceId, path.trim(), name.trim())
      toast({ title: 'Repository registered', message: repo.path, tone: 'success' })
      onDone()
    } catch (err) {
      setError(friendlyError(err, 'registering a repository').message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={open} onClose={onClose} size="sm" title="Register a repository" description="Registering claims the folder for this workspace, so other workspaces cannot use it."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!path.trim()} onClick={submit}>Register</Button></>}>
      <form className="form-stack" onSubmit={e => { e.preventDefault(); void submit() }}>
        {error && <Callout tone="danger" title="Not registered">{error}</Callout>}
        <Field label="Path">{p => <Input {...p} className="ui-mono" value={path} onChange={e => setPath(e.target.value)} autoComplete="off" spellCheck={false} />}</Field>
        <Field label="Name" optional>{p => <Input {...p} value={name} onChange={e => setName(e.target.value)} autoComplete="off" />}</Field>
        <button type="submit" hidden />
      </form>
    </Dialog>
  )
}

function Detail({ workspaceId, path }: { workspaceId: string; path: string }) {
  const [tab, setTab] = useState('profile')
  return (
    <section aria-label={`About ${baseName(path)}`} className="repo-detail">
      <h2 className="repo-detail__title">{baseName(path)} <span className="ui-mono ui-muted ui-wrap">{path}</span></h2>
      <Tabs label="Repository details" idPrefix="repo" value={tab} onChange={setTab} tabs={[{ id: 'profile', label: 'Profile' }, { id: 'memory', label: 'Memory' }, { id: 'preferences', label: 'Preferences' }]} />
      <TabPanel idPrefix="repo" id="profile" active={tab === 'profile'}>{tab === 'profile' && <ProfileTab workspaceId={workspaceId} path={path} />}</TabPanel>
      <TabPanel idPrefix="repo" id="memory" active={tab === 'memory'}>{tab === 'memory' && <MemoryTab workspaceId={workspaceId} path={path} />}</TabPanel>
      <TabPanel idPrefix="repo" id="preferences" active={tab === 'preferences'}>{tab === 'preferences' && <PreferencesTab workspaceId={workspaceId} path={path} />}</TabPanel>
    </section>
  )
}

/** A dialog with one editable value; shared by profile corrections and preferences. */
function ValueDialog({ open, title, description, label, hint, initial, list, extra, confirm, onClose, onSave }: {
  open: boolean
  title: string
  description?: string
  label: string
  hint?: string
  initial: string
  list: boolean
  extra?: React.ReactNode
  confirm: string
  onClose: () => void
  onSave: (value: unknown) => Promise<void>
}) {
  const [text, setText] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (open) { setText(initial); setError(null) } }, [open, initial])
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await onSave(valueFromText(text, list))
    } catch (err) {
      setError(friendlyError(err, 'that change').message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open={open} onClose={onClose} size="sm" title={title} description={description}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!text.trim()} onClick={save}>{confirm}</Button></>}>
      <form className="form-stack" onSubmit={e => { e.preventDefault(); void save() }}>
        {error && <Callout tone="danger" title="Not saved">{error}</Callout>}
        {extra}
        <Field label={label} hint={hint ?? (list ? 'One entry per line.' : undefined)}>
          {p => list ? <Textarea {...p} className="ui-mono" rows={4} value={text} onChange={e => setText(e.target.value)} spellCheck={false} /> : <Input {...p} value={text} onChange={e => setText(e.target.value)} autoComplete="off" />}
        </Field>
      </form>
    </Dialog>
  )
}

function ProfileTab({ workspaceId, path }: { workspaceId: string; path: string }) {
  const { can } = useApp()
  const toast = useToast()
  const q = useAsync(s => getRepoProfile(workspaceId, path, s), [workspaceId, path])
  const [editing, setEditing] = useState<string | null>(null)
  const canEdit = can('run.create')
  const rows = q.data ? Object.entries(q.data.profile) : []
  const current = editing ? q.data?.profile[editing] : undefined
  const init = valueToText(current?.value, editing ?? '')
  if (q.error) return <ErrorNotice error={q.error} onRetry={q.refresh} subject="this repository's profile" />
  if (!q.data) return <Skeleton width="100%" height={160} />
  return (
    <Card>
      <CardHeader title="Profile" subtitle="Facts PatchQuest uses when it plans work here. A value you set is never replaced by detection." />
      <DataTable
        caption="Repository profile"
        rows={rows}
        rowKey={([k]) => k}
        empty={<EmptyState icon="file" title="Nothing detected yet">The profile is built the first time a run scans this repository.</EmptyState>}
        columns={[
          { key: 'f', header: 'Field', render: ([k, f]) => <div><strong>{humanize(k)}</strong><div className="only-sm ui-muted">{profileSourceLabel(f)}</div></div> },
          { key: 'v', header: 'Value', render: ([, f]) => <span className="ui-mono ui-wrap">{showValue(f.value)}</span> },
          { key: 's', header: 'From', secondary: true, render: ([, f]) => <span title={f.reason}>{profileSourceLabel(f)}</span> },
          { key: 'c', header: 'Confidence', align: 'right', secondary: true, render: ([, f]) => formatPercent(f.confidence) },
          { key: 'ver', header: 'Verified', secondary: true, render: ([, f]) => (f.last_verified ? relativeTime(f.last_verified) : '—') },
          { key: 'a', header: '', align: 'right', render: ([k]) => <IconButton icon="edit" size="sm" disabled={!canEdit} label={canEdit ? `Correct ${humanize(k).toLowerCase()}` : 'You need the run.create permission to correct the profile.'} onClick={() => setEditing(k)} /> },
        ]}
      />
      <ValueDialog
        open={!!editing}
        title={editing ? `Correct ${humanize(editing).toLowerCase()}` : ''}
        description="Your value replaces the detected one and is marked as set by you."
        label="Value"
        initial={init.text}
        list={init.list}
        confirm="Save correction"
        onClose={() => setEditing(null)}
        onSave={async v => {
          await setProfileField(workspaceId, path, editing!, v)
          toast({ title: 'Profile corrected', tone: 'success' })
          setEditing(null)
          void q.refresh()
        }}
      />
    </Card>
  )
}

function MemoryTab({ workspaceId, path }: { workspaceId: string; path: string }) {
  const { can } = useApp()
  const toast = useToast()
  const q = useAsync(s => listMemories(workspaceId, path, s), [workspaceId, path])
  const [forgetting, setForgetting] = useState<MemoryItem | null>(null)
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const canWrite = can('run.create')
  // The detected profile is on the Profile tab; its raw records would only be noise here.
  const items = (q.data ?? []).filter(m => !m.key.startsWith('profile.'))

  const remember = async () => {
    setBusy(true)
    setFormError(null)
    try {
      await addMemory(workspaceId, path, key.trim(), valueFromText(value, false))
      toast({ title: 'Remembered', message: 'It will be offered to future runs on this repository.', tone: 'success' })
      setKey('')
      setValue('')
      void q.refresh()
    } catch (err) {
      setFormError(friendlyError(err, 'saving that').message)
    } finally {
      setBusy(false)
    }
  }
  const forget = async () => {
    if (!forgetting) return
    try {
      await forgetMemory(workspaceId, forgetting.id)
      toast({ title: 'Forgotten', message: forgetting.key, tone: 'success' })
      void q.refresh()
    } catch (err) {
      const f = friendlyError(err, 'that memory')
      toast({ title: f.title, message: f.message, tone: 'danger' })
    }
    setForgetting(null)
  }

  return (
    <>
      <Card>
        <CardHeader title="Remembered facts" subtitle="Short notes offered to the agent on later runs here. Facts you add are trusted; ones inferred from past runs are marked." />
        {q.error ? (
          <ErrorNotice error={q.error} onRetry={q.refresh} subject="the memory" />
        ) : !q.data ? (
          <Skeleton width="100%" height={120} />
        ) : (
          <DataTable
            caption="Remembered facts"
            rows={items}
            rowKey={m => m.id}
            empty={<EmptyState icon="file" title="Nothing remembered yet">Add a fact below, or finish a run and PatchQuest will leave a short note.</EmptyState>}
            columns={[
              { key: 'k', header: 'Fact', render: m => <div><strong className="ui-mono ui-wrap">{m.key}</strong><div className="ui-wrap">{showValue(m.value)}</div></div> },
              { key: 'b', header: 'Status', render: m => (
                <span className="badge-row">
                  <Badge tone={m.source === 'user_explicit' ? 'info' : 'neutral'}>{memorySourceLabel(m.source)}</Badge>
                  <Badge tone={m.trusted ? 'success' : 'warning'}>{m.trusted ? 'Trusted' : 'Untrusted'}</Badge>
                  <Badge tone={m.status === 'active' ? 'neutral' : 'warning'}>{humanize(m.status)}</Badge>
                </span>
              ) },
              { key: 'u', header: 'Updated', secondary: true, render: m => relativeTime(m.updated_at) },
              { key: 'a', header: '', align: 'right', render: m => <Button size="sm" variant="ghost" icon="trash" disabled={!canWrite} aria-label={`Forget ${m.key}`} title={canWrite ? undefined : 'You need the run.create permission to forget facts.'} onClick={() => setForgetting(m)}>Forget</Button> },
            ]}
          />
        )}
      </Card>
      <Card>
        <CardHeader title="Remember something" subtitle="For example key “test.how” and value “run make check before committing”." />
        <form className="form-stack" onSubmit={e => { e.preventDefault(); if (key.trim() && value.trim()) void remember() }}>
          {formError && <Callout tone="danger" title="Not saved">{formError}</Callout>}
          <div className="form-row">
            <Field label="Name" hint="A short label, no spaces needed.">{p => <Input {...p} value={key} onChange={e => setKey(e.target.value)} autoComplete="off" disabled={!canWrite} />}</Field>
            <Field label="What to remember">{p => <Input {...p} value={value} onChange={e => setValue(e.target.value)} autoComplete="off" disabled={!canWrite} />}</Field>
          </div>
          <div><Button type="submit" variant="primary" loading={busy} disabled={!canWrite || !key.trim() || !value.trim()} title={canWrite ? undefined : 'You need the run.create permission to add memory.'}>Remember</Button></div>
        </form>
      </Card>
      <Dialog open={!!forgetting} onClose={() => setForgetting(null)} size="sm" title="Forget this?" description={forgetting ? `“${forgetting.key}” will no longer be offered to runs on this repository.` : undefined}
        footer={<><Button variant="ghost" onClick={() => setForgetting(null)}>Keep it</Button><Button variant="danger" onClick={forget}>Forget</Button></>}>
        <span />
      </Dialog>
    </>
  )
}

const SCOPES = [
  { id: 'repository', label: 'This repository' },
  { id: 'workspace', label: 'Everything in this workspace' },
  { id: 'user', label: 'Only me' },
]

function PreferencesTab({ workspaceId, path }: { workspaceId: string; path: string }) {
  const { can } = useApp()
  const toast = useToast()
  const q = useAsync(s => listPreferences(workspaceId, path, s), [workspaceId, path])
  const [editing, setEditing] = useState<string | null>(null)
  const [scope, setScope] = useState('repository')
  const entries = q.data ? Object.entries(q.data) : []
  const pref: Preference | undefined = editing ? q.data?.[editing] : undefined
  const init = valueToText(pref?.value, editing ?? '')
  const canWrite = can('run.create')
  const refFor = (s: string) => (s === 'repository' ? path : undefined)

  const clear = async (key: string, p: Preference) => {
    if (typeof p.decided_by === 'string') return
    try {
      await clearPreference(workspaceId, p.decided_by.scope, refFor(p.decided_by.scope), key)
      toast({ title: 'Setting cleared', message: 'The next setting down, or the default, applies again.', tone: 'success' })
      void q.refresh()
    } catch (err) {
      const f = friendlyError(err, 'that setting')
      toast({ title: f.title, message: f.message, tone: 'danger' })
    }
  }

  if (q.error) return <ErrorNotice error={q.error} onRetry={q.refresh} subject="the preferences" />
  if (!q.data) return <Skeleton width="100%" height={160} />
  return (
    <Card>
      <CardHeader title="Preferences" subtitle="How PatchQuest should behave here." />
      <Callout tone="info" title="Which setting wins">
        The most specific one: a workflow, then you, then this repository, then the workspace, then the organisation. Policy and the command gate always win over any preference.
      </Callout>
      <DataTable
        caption="Preferences"
        rows={entries}
        rowKey={([k]) => k}
        empty={<EmptyState icon="settings" title="No preferences are available" />}
        columns={[
          { key: 'k', header: 'Preference', render: ([k, p]) => <div><strong className="ui-mono ui-wrap">{k}</strong><div className="ui-muted ui-wrap">{p.description}</div></div> },
          { key: 'v', header: 'Value', render: ([, p]) => <span className="ui-mono ui-wrap">{p.value === null ? 'not set' : showValue(p.value)}</span> },
          { key: 'by', header: 'Decided by', render: ([, p]) => {
            const o = preferenceOrigin(p.decided_by)
            return <span className="badge-row"><Badge tone={o.scope ? 'info' : 'neutral'}>{o.label}</Badge>{p.overridden.length > 0 && <Badge title={`Overrides ${p.overridden.length} less specific setting(s)`}>Overrides {p.overridden.length}</Badge>}</span>
          } },
          { key: 'a', header: '', align: 'right', render: ([k, p]) => (
            <span className="badge-row badge-row--end">
              <Button size="sm" icon="edit" disabled={!canWrite} aria-label={`Set ${k}`} title={canWrite ? undefined : 'You need the run.create permission to change preferences.'} onClick={() => { setScope('repository'); setEditing(k) }}>Set</Button>
              {typeof p.decided_by !== 'string' && <Button size="sm" variant="ghost" disabled={!canWrite} aria-label={`Clear ${k}`} onClick={() => void clear(k, p)}>Clear</Button>}
            </span>
          ) },
        ]}
      />
      <ValueDialog
        open={!!editing}
        title={editing ? `Set ${editing}` : ''}
        description={pref?.description}
        label="Value"
        hint={editing === 'test.commands' ? 'One command per line. They must still be ones the command policy runs unattended.' : 'Use true or false for switches; auto or ask for approval modes.'}
        initial={init.text}
        list={init.list}
        confirm="Save"
        extra={<Field label="Applies to">{p => <Select {...p} value={scope} onChange={e => setScope(e.target.value)}>{SCOPES.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</Select>}</Field>}
        onClose={() => setEditing(null)}
        onSave={async v => {
          await setPreference(workspaceId, scope, refFor(scope), editing!, v)
          toast({ title: 'Preference saved', tone: 'success' })
          setEditing(null)
          void q.refresh()
        }}
      />
    </Card>
  )
}
