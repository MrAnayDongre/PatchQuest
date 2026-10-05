import { describe, expect, it } from 'vitest'
import { filterCommands, groupCommands, scoreMatch, type PaletteCommand } from '../../src/lib/palette'

const cmd = (id: string, title: string, group = 'Actions', extra: Partial<PaletteCommand> = {}): PaletteCommand => ({ id, title, group, run: () => {}, ...extra })

const commands = [
  cmd('new', 'New run', 'Actions', { keywords: ['start', 'create'] }),
  cmd('runs', 'Go to runs', 'Go to'),
  cmd('engines', 'Go to engines', 'Go to'),
  cmd('theme', 'Toggle theme', 'Actions', { keywords: ['dark', 'light'] }),
  cmd('r1', 'Fix flaky login test', 'Runs', { hint: '/home/me/webapp' }),
]

describe('command palette matching', () => {
  it('returns everything for an empty query, in the original order', () => {
    expect(filterCommands(commands, '').map(c => c.id)).toEqual(['new', 'runs', 'engines', 'theme', 'r1'])
  })

  it('ranks prefix matches above substring and fuzzy matches', () => {
    expect(filterCommands(commands, 'go').map(c => c.id).slice(0, 2)).toEqual(['runs', 'engines'])
    expect(scoreMatch('new', 'New run')).toBeGreaterThan(scoreMatch('run', 'New run'))
  })

  it('matches keywords and hints that are not in the title', () => {
    expect(filterCommands(commands, 'dark').map(c => c.id)).toEqual(['theme'])
    expect(filterCommands(commands, 'webapp').map(c => c.id)).toEqual(['r1'])
  })

  it('supports fuzzy subsequences and drops non-matches', () => {
    expect(filterCommands(commands, 'tgl').map(c => c.id)).toContain('theme')
    expect(filterCommands(commands, 'qqq')).toEqual([])
  })

  it('groups commands in first-seen order', () => {
    expect(groupCommands(commands).map(g => [g.group, g.items.length])).toEqual([
      ['Actions', 2],
      ['Go to', 2],
      ['Runs', 1],
    ])
  })
})
