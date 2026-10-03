export interface PaletteCommand {
  id: string
  title: string
  /** Section heading: "Actions", "Go to", "Runs", ... */
  group: string
  hint?: string
  /** Extra words that should match but are not shown. */
  keywords?: string[]
  /** Keyboard shortcut chips, e.g. ['g', 'r']. */
  shortcut?: string[]
  run: () => void
}

/** Subsequence match score: higher is better, 0 means no match. Prefix and word-start matches rank first. */
export function scoreMatch(query: string, text: string): number {
  const q = query.trim().toLowerCase()
  if (!q) return 1
  const t = text.toLowerCase()
  if (t === q) return 1000
  if (t.startsWith(q)) return 800 - t.length
  const idx = t.indexOf(q)
  if (idx >= 0) return (t[idx - 1] === ' ' ? 600 : 400) - idx
  // all query words present anywhere
  const words = q.split(/\s+/).filter(Boolean)
  if (words.length > 1 && words.every(w => t.includes(w))) return 300
  // fuzzy subsequence
  let ti = 0
  let streak = 0
  let score = 0
  for (const ch of q) {
    const found = t.indexOf(ch, ti)
    if (found === -1) return 0
    streak = found === ti ? streak + 1 : 0
    score += 5 + streak * 3 - Math.min(found - ti, 5)
    ti = found + 1
  }
  return Math.max(1, score)
}

export function filterCommands(commands: PaletteCommand[], query: string): PaletteCommand[] {
  if (!query.trim()) return commands
  return commands
    .map((c, i) => ({
      c,
      i,
      s: Math.max(scoreMatch(query, c.title), scoreMatch(query, `${c.title} ${(c.keywords ?? []).join(' ')} ${c.hint ?? ''}`) * 0.7),
    }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map(x => x.c)
}

/** Group commands preserving first-seen group order. */
export function groupCommands(commands: PaletteCommand[]): { group: string; items: PaletteCommand[] }[] {
  const groups: { group: string; items: PaletteCommand[] }[] = []
  for (const c of commands) {
    let g = groups.find(x => x.group === c.group)
    if (!g) groups.push((g = { group: c.group, items: [] }))
    g.items.push(c)
  }
  return groups
}
