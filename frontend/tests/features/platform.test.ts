import { describe, expect, it } from 'vitest'
import { friendlyError, normalizeError } from '../../src/api/errors'
import type { IntegrationKind } from '../../src/api/platform'
import { buildCreateBody, describeSecret, explain, isSimulated, parseList, parseMap, profileSourceLabel, valueFromText, webhookUrl } from '../../src/lib/platform'
import { parseHash, buildHash } from '../../src/lib/router'
import { nextShortcut } from '../../src/app/shortcuts'

const webhook: IntegrationKind = {
  kind: 'webhook', title: 'Generic webhook', inbound: true, triggers: ['*'], actions: [], notes: '', test: '',
  config: [{ name: 'event_types', type: 'string_list', required: true, description: '' }, { name: 'endpoints', type: 'string_map', required: false, description: '' }, { name: 'label', type: 'string', required: false, description: '' }],
  secrets: [{ name: 'signing_secret', required: true }, { name: 'post_secret', required: false }],
}

describe('connect form to request body', () => {
  it('maps lists, maps and both secret modes', () => {
    const r = buildCreateBody('ws1', webhook, {
      name: ' Hooks ',
      config: { event_types: 'a, b\nc,,a', endpoints: 'ci=https://x.test/1\n\nfoo = https://y.test' },
      secrets: { signing_secret: { mode: 'env', text: ' SIGN ' }, post_secret: { mode: 'stored', text: 's3cret' } },
    })
    expect(r).toEqual({ ok: true, body: { workspace_id: 'ws1', kind: 'webhook', name: 'Hooks', config: { event_types: ['a', 'b', 'c'], endpoints: { ci: 'https://x.test/1', foo: 'https://y.test' } }, secrets: { signing_secret: { env: 'SIGN' }, post_secret: { value: 's3cret' } } } })
  })
  it('omits blank optional fields and reports missing required ones', () => {
    const r = buildCreateBody('ws1', webhook, { name: '', config: {}, secrets: { post_secret: { mode: 'env', text: '' } } })
    expect(r.ok).toBe(false)
    if (!r.ok) expect(Object.keys(r.errors).sort()).toEqual(['config.event_types', 'name', 'secret.signing_secret'])
  })
  it('rejects a map line without a value', () => {
    const r = buildCreateBody('w', webhook, { name: 'n', config: { event_types: 'a', endpoints: 'broken' }, secrets: { signing_secret: { mode: 'env', text: 'X' } } })
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.errors['config.endpoints']).toMatch(/broken/)
  })
  it('parses lists and maps', () => {
    expect(parseList('a,\n b ,a')).toEqual(['a', 'b'])
    expect(parseMap('x=1\n=2\ny')).toEqual({ map: { x: '1' }, bad: ['=2', 'y'] })
    expect(valueFromText('true', false)).toBe(true)
    expect(valueFromText('a\n\nb', true)).toEqual(['a', 'b'])
  })
})

describe('labels and copy', () => {
  it('shows saved secrets exactly as the API reports them', () => {
    expect(describeSecret({ env: 'TOK' })).toBe('{env: TOK}')
    expect(describeSecret({ stored: true })).toBe('{stored: true}')
  })
  it('flags simulators and builds the webhook URL', () => {
    expect(isSimulated('GitHub (simulator)')).toBe(true)
    expect(isSimulated('GitHub')).toBe(false)
    expect(webhookUrl('/hooks/i1', 'http://h:1/')).toBe('http://h:1/hooks/i1')
  })
  it('labels where a profile value came from', () => {
    expect(profileSourceLabel({ source: 'user_explicit', reason: '', evidence: {} })).toBe('You set this')
    expect(profileSourceLabel({ source: 'repository_detected', reason: 'detected from repository layout', evidence: { files: ['pyproject.toml'] } })).toBe('Detected from pyproject.toml')
    expect(profileSourceLabel({ source: 'repository_detected', reason: 'detected from repository layout', evidence: {} })).toBe('Detected from repository layout')
  })
  it('extracts the 422 message the server sends', () => {
    const e = normalizeError(422, { detail: { code: 'refused', message: 'github needs a repo' } })
    expect(friendlyError(e).message).toBe('github needs a repo')
  })
})

describe('explanations in plain language', () => {
  it('says why a command was chosen', () => {
    const w = explain({ id: 1, type: 'decision_explained', phase: 'testing', message: '', created_at: '', payload: { decision: 'test_commands', chosen: ['pytest -q'], because: [{ kind: 'preference', scope: 'repository', key: 'test.commands' }], rejected: [{ command: 'make', why: 'not allowed' }] } })
    expect(w.title).toBe('Used pytest -q as the test command because your repository preference for test.commands says so')
    expect(w.details).toEqual(['Not used: make (not allowed)'])
  })
  it('summarises memory selection', () => {
    const w = explain({ id: 2, type: 'memory_selected', phase: null, message: '', created_at: '', payload: { considered: 5, selected: 2, tokens: 40, stale_rejected: 1, items: [{ key: 'a', source: 'user_explicit', trusted: true, reason: 'matches x' }] } })
    expect(w.title).toBe('Remembered 2 of 5 facts (about 40 tokens)')
    expect(w.details[0]).toMatch(/left out because they looked out of date/)
    expect(w.details[1]).toBe('a (you set this): matches x')
  })
  it('reads remembered-fact keys as words', () => {
    const w = explain({ id: 3, type: 'memory_selected', phase: null, message: '', created_at: '', payload: { considered: 2, selected: 2, tokens: 9, items: [
      { key: 'profile.test_commands', source: 'repository_detected', trusted: true }, { key: 'run:32bd9b18-07b', source: 'run_outcome', trusted: false }] } })
    expect(w.details[0]).toMatch(/^test commands \(/)
    expect(w.details[1]).toMatch(/^note from an earlier run \(/)
  })
})

describe('navigation', () => {
  it('routes and shortcuts', () => {
    expect(parseHash('#/integrations').name).toBe('integrations')
    expect(parseHash(buildHash('repositories', {}, { path: '/a b' })).query.path).toBe('/a b')
    const first = nextShortcut({ goAt: null }, { key: 'g', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, typing: false }, 0)
    expect(nextShortcut(first.state, { key: 'i', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, typing: false }, 5).action).toBe('go-integrations')
  })
})
