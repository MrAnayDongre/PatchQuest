import type { Run } from '../api/types'
import { friendlyStatusLine } from '../lib/eventCopy'
import { baseName, relativeTime, truncate } from '../lib/format'
import type { PaletteCommand } from '../lib/palette'
import { buildHash, navigate, runHash } from '../lib/router'

export interface CommandDeps {
  runs: Run[]
  openNewRun: () => void
  toggleTheme: () => void
  openHelp: () => void
  /** Commands that act on the run currently on screen (cancel, resume, approve...). */
  currentRunCommands: PaletteCommand[]
  now?: number
  maxRuns?: number
}

/** Everything the palette can do right now. Pure: the same inputs always give the same list. */
export function buildCommands(d: CommandDeps): PaletteCommand[] {
  const go = (title: string, hash: string, keywords: string[] = [], shortcut?: string[]): PaletteCommand => ({
    id: `go:${hash}`,
    title,
    group: 'Go to',
    keywords,
    shortcut,
    run: () => navigate(hash),
  })
  const list: PaletteCommand[] = [
    { id: 'new-run', title: 'New run', group: 'Actions', keywords: ['start', 'create', 'task'], shortcut: ['n'], run: d.openNewRun },
    ...d.currentRunCommands,
    { id: 'toggle-theme', title: 'Toggle light or dark theme', group: 'Actions', keywords: ['appearance', 'dark', 'light', 'mode'], run: d.toggleTheme },
    { id: 'help', title: 'Keyboard shortcuts', group: 'Actions', keywords: ['help', 'keys'], shortcut: ['?'], run: d.openHelp },
    go('Go to Home', buildHash('home'), ['dashboard', 'mission control'], ['g', 'h']),
    go('Go to Runs', buildHash('runs'), ['list', 'history'], ['g', 'r']),
    go('Go to Engines', buildHash('engines'), ['providers', 'models', 'local'], ['g', 'e']),
    go('Go to Settings', buildHash('settings'), ['token', 'preferences'], ['g', 's']),
    go('Go to Extras', buildHash('extras'), ['games', 'calendar', 'search', 'memory', 'scheduler'], ['g', 'x']),
  ]
  const now = d.now ?? Date.now()
  const recent = [...d.runs].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, d.maxRuns ?? 30)
  for (const r of recent) {
    list.push({
      id: `run:${r.id}`,
      title: truncate(r.task.replace(/\s+/g, ' '), 80),
      group: 'Open run',
      hint: `${baseName(r.repo_path)} · ${friendlyStatusLine(r)} · ${relativeTime(r.updated_at, now)}`,
      keywords: [r.id, r.repo_path, r.status, r.model ?? ''],
      run: () => navigate(runHash(r.id)),
    })
  }
  return list
}
