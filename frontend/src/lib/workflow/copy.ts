import type { NodeType } from './types'
import type { ActionMeta } from './clientCheck'

export const NODE_COPY: Record<NodeType, { label: string; hint: string }> = {
  agent: { label: 'Agent', hint: 'Runs a full PatchQuest agent on a repository and waits for it.' },
  action: { label: 'Action', hint: 'Does one thing in another tool, such as posting a comment.' },
  condition: { label: 'Condition', hint: 'Takes one path or another depending on a value.' },
  approval: { label: 'Approval', hint: 'Stops until a person says go or no-go.' },
  wait_event: { label: 'Wait for event', hint: 'Waits for something outside to happen.' },
  timer: { label: 'Timer', hint: 'Waits for a set amount of time.' },
  end: { label: 'End', hint: 'Finishes the workflow.' },
}

export const SIDE_EFFECT_COPY: Record<string, string> = {
  PURE: 'Only records a note in the workflow history',
  READ_ONLY: 'Only reads',
  NETWORK_READ: 'Reads from the network',
  WORKSPACE_WRITE: 'Writes inside the isolated workspace',
  REPOSITORY_WRITE: 'Writes to a repository',
  EXTERNAL_WRITE: 'Changes something outside PatchQuest',
  HOST_MUTATION: 'Changes the machine PatchQuest runs on',
  DESTRUCTIVE: 'Can delete or overwrite data',
  UNKNOWN: 'Effect unknown',
}

const CONNECTOR_NAME: Record<string, string> = { github: 'GitHub', slack: 'Slack', webhook: 'a webhook' }

export function connectorName(c: string | null): string {
  return c ? CONNECTOR_NAME[c] ?? c : 'PatchQuest'
}

/** "This workflow posts to GitHub, so a person must approve first." */
export function gateSentence(a: ActionMeta): string {
  if (!a.requires_approval) return ''
  const where = connectorName(a.connector)
  const verb = a.connector === 'github' ? 'writes to' : a.connector === 'slack' ? 'posts to' : a.connector === 'webhook' ? 'sends data to' : 'changes something in'
  return `This step ${verb} ${where}, so a person must approve first.`
}

export const TRIGGER_TYPES: { id: string; label: string }[] = [
  { id: 'manual', label: 'Started by hand' },
  { id: 'github.issues.labeled', label: 'GitHub issue labelled' },
  { id: 'github.check_suite.completed', label: 'GitHub check suite finished' },
  { id: 'github.push', label: 'GitHub push' },
]

export function triggerLabel(type: string | undefined | null): string {
  if (!type) return 'No trigger'
  return TRIGGER_TYPES.find(t => t.id === type)?.label ?? type
}

export const OPERATOR_COPY: { id: string; label: string; unary?: boolean }[] = [
  { id: 'eq', label: 'is equal to' },
  { id: 'ne', label: 'is not equal to' },
  { id: 'gt', label: 'is greater than' },
  { id: 'ge', label: 'is at least' },
  { id: 'lt', label: 'is less than' },
  { id: 'le', label: 'is at most' },
  { id: 'in', label: 'is one of' },
  { id: 'contains', label: 'contains' },
  { id: 'exists', label: 'exists', unary: true },
]

export const PERMISSION_COPY = {
  view: {
    title: "Your role can't see workflows here",
    message: 'Workflows are limited by role in each workspace. Ask a workspace owner for at least read access.',
  },
  define: {
    title: "Your role can't change workflows",
    message: 'Creating or editing a workflow needs the Developer role or higher in this workspace. You can still look at workflows and drafts stay in this browser.',
  },
  decide: {
    title: "Your role can't answer approvals",
    message: 'Approving or denying a step needs permission to decide approvals. Service accounts can start workflows but never approve them.',
  },
  control: {
    title: "Your role can't change this run",
    message: 'Settling or cancelling a workflow run needs permission to control runs in this workspace.',
  },
} as const
