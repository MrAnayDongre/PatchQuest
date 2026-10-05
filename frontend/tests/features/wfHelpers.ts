import type { WfDef } from '../../src/lib/workflow/types'

/** The example from docs/workflows.md. */
export function docsExample(): WfDef {
  return {
    schema: 1,
    name: 'issue-to-proposal',
    description: '',
    trigger: { type: 'github.issues.labeled', filter: { 'payload.label': 'agent-ready' } },
    variables: { repo: { default: '/work/app' } },
    nodes: [
      { id: 'fix', type: 'agent', config: { task: 'Resolve: {{trigger.payload.title}}', repo: '{{vars.repo}}', provider: 'sglang' } },
      { id: 'ok', type: 'condition', config: { if: { left: '{{nodes.fix.output.verdict}}', op: 'eq', right: 'passed' } } },
      { id: 'review', type: 'approval', config: { message: 'Post it?', timeout_s: 86400 } },
      { id: 'post', type: 'action', config: { action: 'github.comment', params: { issue_number: '{{trigger.payload.number}}', body: '...' } } },
      { id: 'done', type: 'end' },
    ],
    edges: [
      { from: 'fix', to: 'ok' },
      { from: 'ok', to: 'review', when: 'true' },
      { from: 'ok', to: 'done', when: 'false' },
      { from: 'review', to: 'post', when: 'approved' },
      { from: 'review', to: 'done', when: 'denied' },
      { from: 'post', to: 'done' },
    ],
  }
}

export const ACTIONS = [
  { name: 'notify.log', side_effect: 'PURE', idempotent: true, requires_approval: false, connector: null },
  { name: 'github.comment', side_effect: 'EXTERNAL_WRITE', idempotent: true, requires_approval: true, connector: 'github' },
]

export function lastArg<T>(fn: unknown): T {
  const calls = (fn as { mock: { calls: unknown[][] } }).mock.calls
  return calls[calls.length - 1][0] as T
}
