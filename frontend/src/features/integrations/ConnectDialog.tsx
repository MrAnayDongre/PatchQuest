import { useEffect, useMemo, useState } from 'react'
import { createIntegration, type Integration, type KindsResponse } from '../../api/platform'
import { friendlyError } from '../../api/errors'
import { Callout, Dialog } from '../../design/overlay'
import { Badge, Button, Field, Input, Select, Textarea } from '../../design/primitives'
import { humanize } from '../../lib/format'
import { buildCreateBody, sideEffectLabel, type ConnectDraft } from '../../lib/platform'

const emptyDraft = (): ConnectDraft => ({ name: '', config: {}, secrets: {} })

interface Props {
  open: boolean
  workspaceId: string
  kinds: KindsResponse | null
  onClose: () => void
  onCreated: (i: Integration) => void
}

export function ConnectDialog({ open, workspaceId, kinds, onClose, onCreated }: Props) {
  const [kindId, setKindId] = useState('')
  const [draft, setDraft] = useState<ConnectDraft>(emptyDraft)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [serverError, setServerError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const storedOk = kinds?.stored_secrets_available ?? false
  const kind = useMemo(() => kinds?.kinds.find(k => k.kind === kindId) ?? null, [kinds, kindId])

  useEffect(() => {
    if (open) {
      setKindId(kinds?.kinds[0]?.kind ?? '')
      setDraft(emptyDraft())
      setErrors({})
      setServerError(null)
    }
  }, [open, kinds])

  const pick = (id: string) => {
    setKindId(id)
    setDraft(d => ({ name: d.name, config: {}, secrets: {} }))
    setErrors({})
    setServerError(null)
  }

  const submit = async () => {
    if (!kind) return
    const built = buildCreateBody(workspaceId, kind, draft)
    if (!built.ok) {
      setErrors(built.errors)
      return
    }
    setErrors({})
    setServerError(null)
    setSaving(true)
    try {
      onCreated(await createIntegration(built.body))
    } catch (err) {
      setServerError(friendlyError(err, 'this integration').message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title="Connect an integration"
      description="Secrets are saved encrypted or read from an environment variable on the server. They are never shown again."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={saving} disabled={!kind} onClick={submit}>Connect</Button>
        </>
      }
    >
      {!kinds ? (
        <p className="ui-muted">Loading the available integrations…</p>
      ) : (
        <form className="form-stack" onSubmit={e => { e.preventDefault(); void submit() }} noValidate>
          {serverError && <Callout tone="danger" title="The server did not accept this">{serverError}</Callout>}
          <div className="form-row">
            <Field label="Integration">{p => <Select {...p} value={kindId} onChange={e => pick(e.target.value)}>{kinds.kinds.map(k => <option key={k.kind} value={k.kind}>{k.title}</option>)}</Select>}</Field>
            <Field label="Name" error={errors.name} hint="Shown in lists and logs.">{p => <Input {...p} value={draft.name} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} autoComplete="off" />}</Field>
          </div>

          {kind && (
            <>
              <section className="kind-info" aria-label={`About ${kind.title}`}>
                {kind.notes && <p>{kind.notes}</p>}
                <p className="ui-muted">{kind.inbound ? `Receives events: ${kind.triggers.join(', ')}.` : 'Outbound only: it does not receive events.'} Check: {kind.test}.</p>
                <ul className="kind-actions" aria-label="What PatchQuest can do with it">
                  {kind.actions.map(a => (
                    <li key={a.name}>
                      <strong>{humanize(a.name)}</strong>
                      <span className="ui-muted">{sideEffectLabel(a.side_effect)}</span>
                      <Badge tone={a.requires_approval ? 'warning' : 'neutral'}>{a.requires_approval ? 'Needs approval' : 'No approval'}</Badge>
                    </li>
                  ))}
                </ul>
              </section>

              {kind.config.map(f => {
                const val = draft.config[f.name] ?? ''
                const set = (v: string) => setDraft(d => ({ ...d, config: { ...d.config, [f.name]: v } }))
                const hint = [f.description, f.type === 'string_list' ? 'Separate entries with commas or new lines.' : f.type === 'string_map' ? 'One per line: name=https://…' : ''].filter(Boolean).join(' ')
                return (
                  <Field key={f.name} label={humanize(f.name)} optional={!f.required} hint={hint || undefined} error={errors[`config.${f.name}`]}>
                    {p => f.type === 'string' ? <Input {...p} value={val} onChange={e => set(e.target.value)} autoComplete="off" /> : <Textarea {...p} rows={3} value={val} onChange={e => set(e.target.value)} spellCheck={false} />}
                  </Field>
                )
              })}

              {kind.secrets.length > 0 && <h3 className="kind-heading">Secrets</h3>}
              {!storedOk && kind.secrets.length > 0 && (
                <Callout tone="info" title="Storing secrets is turned off on this server">Point each secret at an environment variable that the server can read. Setting up an encryption key on the server enables storing them here.</Callout>
              )}
              {kind.secrets.map(s => {
                const d = draft.secrets[s.name] ?? { mode: 'env' as const, text: '' }
                const set = (next: Partial<typeof d>) => setDraft(x => ({ ...x, secrets: { ...x.secrets, [s.name]: { ...d, ...next } } }))
                return (
                  <div key={s.name} className="secret-row">
                    <Field label="How to provide" className="secret-row__mode">
                      {p => (
                        <Select {...p} aria-label={`How to provide ${humanize(s.name).toLowerCase()}`} value={d.mode} onChange={e => set({ mode: e.target.value as 'env' | 'stored', text: '' })}>
                          <option value="env">Environment variable</option>
                          <option value="stored" disabled={!storedOk}>{storedOk ? 'Store encrypted' : 'Store encrypted (unavailable)'}</option>
                        </Select>
                      )}
                    </Field>
                    <Field label={`${humanize(s.name)}${d.mode === 'env' ? ': variable name' : ''}`} optional={!s.required} error={errors[`secret.${s.name}`]} hint={d.mode === 'stored' ? 'Saved encrypted. It cannot be read back.' : 'Only the name is saved, never the value.'}>
                      {p => d.mode === 'env'
                        ? <Input {...p} className="ui-mono" placeholder="MY_TOKEN" value={d.text} onChange={e => set({ text: e.target.value })} autoComplete="off" spellCheck={false} />
                        : <Input {...p} type="password" autoComplete="new-password" value={d.text} onChange={e => set({ text: e.target.value })} />}
                    </Field>
                  </div>
                )
              })}
            </>
          )}
          <button type="submit" hidden />
        </form>
      )}
    </Dialog>
  )
}
