import { describe, expect, it } from 'vitest'
import { baseName, elapsed, formatBytes, formatCount, formatDuration, humanize, median, plural, relativeTime, truncate } from '../../src/lib/format'

const NOW = Date.parse('2026-03-10T12:00:00Z')

describe('formatting', () => {
  it('relative time reads naturally', () => {
    expect(relativeTime('2026-03-10T11:59:58Z', NOW)).toBe('just now')
    expect(relativeTime('2026-03-10T11:59:15Z', NOW)).toBe('45 s ago')
    expect(relativeTime('2026-03-10T11:55:00Z', NOW)).toBe('5 min ago')
    expect(relativeTime('2026-03-10T09:00:00Z', NOW)).toBe('3 h ago')
    expect(relativeTime('2026-03-09T09:00:00Z', NOW)).toBe('yesterday')
    expect(relativeTime('2026-03-06T12:00:00Z', NOW)).toBe('4 days ago')
    expect(relativeTime(null, NOW)).toBe('')
    expect(relativeTime('garbage', NOW)).toBe('')
  })

  it('durations are compact', () => {
    expect(formatDuration(850)).toBe('850 ms')
    expect(formatDuration(4200)).toBe('4.2 s')
    expect(formatDuration(42_000)).toBe('42 s')
    expect(formatDuration(185_000)).toBe('3 min 05 s')
    expect(formatDuration(59_900 + 60_000 * 2)).toBe('3 min')
    expect(formatDuration(2 * 3600_000 + 10 * 60_000)).toBe('2 h 10 min')
    expect(formatDuration(-1)).toBe('—')
  })

  it('elapsed uses now for unfinished runs', () => {
    expect(elapsed('2026-03-10T11:00:00Z', null, NOW)).toBe(3600_000)
    expect(elapsed('2026-03-10T11:00:00Z', '2026-03-10T11:10:00Z', NOW)).toBe(600_000)
    expect(elapsed(null, null, NOW)).toBeNull()
  })

  it('median handles odd, even and empty lists', () => {
    expect(median([3, 1, 2])).toBe(2)
    expect(median([4, 1, 3, 2])).toBe(2.5)
    expect(median([])).toBeNull()
  })

  it('misc helpers', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatCount(1500)).toBe('1.5k')
    expect(truncate('abcdefghij', 6)).toBe('abcde…')
    expect(baseName('/home/me/project/')).toBe('project')
    expect(humanize('model_calls')).toBe('Model calls')
    expect(plural(1, 'file')).toBe('1 file')
    expect(plural(3, 'file')).toBe('3 files')
  })
})
