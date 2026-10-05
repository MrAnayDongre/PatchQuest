import { describe, expect, it } from 'vitest'
import { coerceAuto, coerceLike, splitDuration, toSeconds } from '../../src/lib/workflow/duration'
import { approvalMessage, describeWfEvent, isRunTerminal, latestSteps, nodeVisuals, pendingApprovals, uncertainSteps, visualOf, type WfStep } from '../../src/lib/workflow/run'
import { parseHash, buildHash } from '../../src/lib/router'
import { buildCommands } from '../../src/app/commands'
import { docsExample } from './wfHelpers'

const step = (node_id: string, status: string, visit = 1, extra: Partial<WfStep> = {}): WfStep => ({ node_id, visit, status, ...extra })

describe('durations', () => {
  it('shows the largest whole unit and converts back', () => {
    expect(splitDuration(86400)).toEqual({ amount: 1, unit: 'days' })
    expect(splitDuration(7200)).toEqual({ amount: 2, unit: 'hours' })
    expect(splitDuration(90)).toEqual({ amount: 90, unit: 'seconds' })
    expect(splitDuration(undefined)).toEqual({ amount: '', unit: 'minutes' })
    expect(toSeconds(2, 'hours')).toBe(7200)
    expect(toSeconds('', 'hours')).toBeUndefined()
    expect(toSeconds(0, 'days')).toBeUndefined()
  })
  it('keeps value types when editing text', () => {
    expect(coerceLike('5', 3)).toBe(5)
    expect(coerceLike('abc', 3)).toBe('abc')
    expect(coerceLike('5', 'x')).toBe('5')
    expect(coerceAuto('80')).toBe(80)
    expect(coerceAuto('false')).toBe(false)
    expect(coerceAuto('fast')).toBe('fast')
  })
})

describe('run helpers', () => {
  it('the latest visit of a node decides its colour', () => {
    const steps = [step('a', 'succeeded', 1), step('a', 'running', 2), step('b', 'waiting', 1, { wait_kind: 'approval' })]
    expect(latestSteps(steps).get('a')?.status).toBe('running')
    const v = nodeVisuals({ nodes: [{ id: 'a', type: 'agent' }, { id: 'b', type: 'approval' }, { id: 'c', type: 'end' }], edges: [] }, steps)
    expect(v).toEqual({ a: 'running', b: 'waiting', c: 'idle' })
    expect(visualOf(step('x', 'uncertain'))).toBe('uncertain')
    expect(visualOf(undefined)).toBe('idle')
  })
  it('finds what needs a person', () => {
    const steps = [step('r', 'waiting', 1, { wait_kind: 'approval' }), step('t', 'waiting', 1, { wait_kind: 'timer' }), step('p', 'uncertain')]
    expect(pendingApprovals(steps).map(s => s.node_id)).toEqual(['r'])
    expect(uncertainSteps(steps).map(s => s.node_id)).toEqual(['p'])
    expect(isRunTerminal('completed')).toBe(true)
    expect(isRunTerminal('waiting')).toBe(false)
  })
  it('describes events and finds the approval question', () => {
    const e = { id: 1, type: 'approval_requested', node_id: 'review', actor: 'engine', message: 'Post it?', payload: null, created_at: '' }
    expect(describeWfEvent(e)).toMatchObject({ title: 'Waiting for approval: review', detail: 'Post it?', tone: 'warning' })
    expect(approvalMessage([e], 'review')).toBe('Post it?')
    expect(approvalMessage([e], 'other')).toBeNull()
    expect(describeWfEvent({ ...e, type: 'step_uncertain', node_id: 'post' }).tone).toBe('danger')
  })
})

describe('workflow routes and commands', () => {
  it('routes the three workflow screens, with runs not mistaken for ids', () => {
    expect(parseHash('#/workflows').name).toBe('workflows')
    expect(parseHash('#/workflows/new?template=x')).toEqual({ name: 'workflow', params: { id: 'new' }, query: { template: 'x' } })
    expect(parseHash('#/workflows/wf_abc').params.id).toBe('wf_abc')
    expect(parseHash('#/workflows/runs/r1')).toEqual({ name: 'workflow-run', params: { id: 'r1' }, query: {} })
    expect(parseHash('#/workflows/runs').name).toBe('not-found')
    expect(buildHash('workflow-run', { id: 'r1' })).toBe('#/workflows/runs/r1')
    expect(buildHash('workflow', { id: 'wf1' }, { run: '1' })).toBe('#/workflows/wf1?run=1')
  })
  it('the palette can create, open and start workflows', () => {
    const noop = () => {}
    const cmds = buildCommands({ runs: [], openNewRun: noop, toggleTheme: noop, openHelp: noop, currentRunCommands: [], workflows: [{ id: 'wf1', workspace_id: 'ws', name: 'nightly-fix', version: 2, trigger_type: 'manual', created_at: '', description: '' }] })
    const titles = cmds.map(c => [c.group, c.title])
    expect(titles).toContainEqual(['Actions', 'New workflow'])
    expect(titles).toContainEqual(['Open workflow', 'nightly-fix'])
    expect(titles).toContainEqual(['Start run', 'Start a run of nightly-fix'])
    expect(docsExample().name).toBeTruthy()
  })
})
