/** Unified-diff parser (git style) and split-view row builder. */

export type DiffLineType = 'add' | 'del' | 'ctx' | 'note'

export interface DiffLine {
  type: DiffLineType
  oldNo: number | null
  newNo: number | null
  text: string
}

export interface DiffHunk {
  header: string
  oldStart: number
  newStart: number
  lines: DiffLine[]
}

export type FileStatus = 'added' | 'deleted' | 'modified' | 'renamed'

export interface DiffFile {
  path: string
  oldPath: string | null
  status: FileStatus
  binary: boolean
  additions: number
  deletions: number
  hunks: DiffHunk[]
}

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/

function stripPrefix(path: string): string {
  const p = path.replace(/^"|"$/g, '')
  return p.replace(/^[ab]\//, '')
}

export function parseUnifiedDiff(text: string | null | undefined): DiffFile[] {
  if (!text || !text.trim()) return []
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const files: DiffFile[] = []
  let file: DiffFile | null = null
  let hunk: DiffHunk | null = null
  let oldNo = 0
  let newNo = 0
  let remOld = 0
  let remNew = 0

  const start = (path: string): DiffFile => {
    const f: DiffFile = { path, oldPath: null, status: 'modified', binary: false, additions: 0, deletions: 0, hunks: [] }
    files.push(f)
    return f
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (hunk && (remOld > 0 || remNew > 0)) {
      // Inside a hunk body the first character is authoritative, even if it looks like a header.
      const c = line[0]
      if (c === '+') {
        hunk.lines.push({ type: 'add', oldNo: null, newNo: newNo++, text: line.slice(1) })
        file!.additions++
        remNew--
        continue
      }
      if (c === '-') {
        hunk.lines.push({ type: 'del', oldNo: oldNo++, newNo: null, text: line.slice(1) })
        file!.deletions++
        remOld--
        continue
      }
      if (c === ' ' || line === '') {
        hunk.lines.push({ type: 'ctx', oldNo: oldNo++, newNo: newNo++, text: line.slice(1) })
        remOld--
        remNew--
        continue
      }
      if (c === '\\') {
        hunk.lines.push({ type: 'note', oldNo: null, newNo: null, text: line.slice(2) || line })
        continue
      }
    } else if (hunk && line.startsWith('\\')) {
      hunk.lines.push({ type: 'note', oldNo: null, newNo: null, text: line.slice(2) || line })
      continue
    }

    if (line.startsWith('diff --git ')) {
      const m = /^diff --git (?:"?a\/(.+?)"?) (?:"?b\/(.+?)"?)$/.exec(line)
      file = start(m ? m[2] : stripPrefix(line.slice(11).split(' ').pop() ?? ''))
      if (m && m[1] !== m[2]) file.oldPath = m[1]
      hunk = null
      continue
    }
    if (line.startsWith('--- ')) {
      const p = line.slice(4).split('\t')[0]
      if (!file || file.hunks.length) {
        file = start(p === '/dev/null' ? '' : stripPrefix(p))
      }
      if (p === '/dev/null') file.status = 'added'
      else if (!file.path) file.path = stripPrefix(p)
      continue
    }
    if (line.startsWith('+++ ') && file) {
      const p = line.slice(4).split('\t')[0]
      if (p === '/dev/null') file.status = 'deleted'
      else {
        file.path = stripPrefix(p)
      }
      continue
    }
    if (!file) continue
    if (line.startsWith('new file mode')) file.status = 'added'
    else if (line.startsWith('deleted file mode')) file.status = 'deleted'
    else if (line.startsWith('rename from ')) {
      file.status = 'renamed'
      file.oldPath = line.slice(12)
    } else if (line.startsWith('rename to ')) file.path = line.slice(10)
    else if (line.startsWith('Binary files') || line.startsWith('GIT binary patch')) file.binary = true
    else {
      const m = HUNK_RE.exec(line)
      if (m) {
        oldNo = parseInt(m[1], 10)
        newNo = parseInt(m[3], 10)
        remOld = m[2] === undefined ? 1 : parseInt(m[2], 10)
        remNew = m[4] === undefined ? 1 : parseInt(m[4], 10)
        hunk = { header: line, oldStart: oldNo, newStart: newNo, lines: [] }
        file.hunks.push(hunk)
        if (file.status === 'deleted' && oldPathless(file)) file.path = file.oldPath ?? file.path
      }
    }
  }
  // A deleted file's "+++" is /dev/null, so its name comes from the "---" line, which we already stored.
  return files.filter(f => f.path || f.hunks.length || f.binary)
}

function oldPathless(f: DiffFile): boolean {
  return !f.path
}

export interface SplitRow {
  left: DiffLine | null
  right: DiffLine | null
  /** hunk header rows span both sides */
  header?: string
}

/** Pair deletions with the additions that follow them for side-by-side display. */
export function toSplitRows(file: DiffFile): SplitRow[] {
  const rows: SplitRow[] = []
  for (const hunk of file.hunks) {
    rows.push({ left: null, right: null, header: hunk.header })
    let dels: DiffLine[] = []
    let adds: DiffLine[] = []
    const flush = () => {
      const n = Math.max(dels.length, adds.length)
      for (let i = 0; i < n; i++) rows.push({ left: dels[i] ?? null, right: adds[i] ?? null })
      dels = []
      adds = []
    }
    for (const line of hunk.lines) {
      if (line.type === 'del') dels.push(line)
      else if (line.type === 'add') adds.push(line)
      else {
        flush()
        rows.push(line.type === 'note' ? { left: line, right: line } : { left: line, right: line })
      }
    }
    flush()
  }
  return rows
}

export function diffTotals(files: DiffFile[]): { files: number; additions: number; deletions: number } {
  return files.reduce(
    (t, f) => ({ files: t.files + 1, additions: t.additions + f.additions, deletions: t.deletions + f.deletions }),
    { files: 0, additions: 0, deletions: 0 },
  )
}

/** Keep only the files whose path is in `paths` (used for the "applied" view). */
export function filterFiles(files: DiffFile[], paths: ReadonlySet<string>): DiffFile[] {
  return files.filter(f => paths.has(f.path) || (f.oldPath !== null && paths.has(f.oldPath)))
}
