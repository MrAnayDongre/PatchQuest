import { describe, expect, it } from 'vitest'
import { ancestorsOf, availableRefs, insertAtCursor, referencesIn } from '../../src/lib/workflow/refs'
import { docsExample } from './wfHelpers'

describe('reference inserter', () => {
  const def = docsExample()
  it('only offers earlier steps, declared variables and trigger fields', () => {
    const forPost = availableRefs(def, 'post').map(r => r.path)
    expect(forPost).toContain('nodes.fix.output.verdict')
    expect(forPost).toContain('vars.repo')
    expect(forPost).toContain('trigger.payload.label')
    expect(forPost).not.toContain('nodes.post.output')
    expect(forPost.some(p => p.startsWith('nodes.done'))).toBe(false)
    const forFix = availableRefs(def, 'fix').map(r => r.path)
    expect(forFix.some(p => p.startsWith('nodes.'))).toBe(false)
  })
  it('computes ancestors through branches and ignores the node itself on loops', () => {
    expect([...ancestorsOf(def, 'post')].sort()).toEqual(['fix', 'ok', 'review'])
    const loop = { nodes: [{ id: 'a', type: 'agent' }, { id: 'b', type: 'agent' }], edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }] }
    expect([...ancestorsOf(loop, 'a')]).toEqual(['b'])
  })
  it('inserts a token at the cursor, replacing a selection', () => {
    expect(insertAtCursor('Fix: ', 5, 5, 'trigger.payload.title')).toEqual({ value: 'Fix: {{trigger.payload.title}}', caret: 30 })
    expect(insertAtCursor('abcXYZdef', 3, 6, 'vars.x')).toEqual({ value: 'abc{{vars.x}}def', caret: 13 })
    expect(insertAtCursor('ab', 99, 99, 'vars.x').value).toBe('ab{{vars.x}}')
  })
  it('finds references anywhere in a config', () => {
    expect(referencesIn({ a: '{{vars.x}} and {{ nodes.fix.output.run_id }}', b: ['{{trigger.type}}'], c: 1 }).sort()).toEqual(['nodes.fix.output.run_id', 'trigger.type', 'vars.x'])
  })
})
