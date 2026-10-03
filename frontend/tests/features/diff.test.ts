import { describe, expect, it } from 'vitest'
import { diffTotals, filterFiles, parseUnifiedDiff, toSplitRows } from '../../src/lib/diff'

const SAMPLE = `diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@ def main():
 import os
-print("old")
+print("new")
+print("extra")
 x = 1
 y = 2
@@ -20,2 +21,2 @@
 tail
-last
\\ No newline at end of file
+last!
\\ No newline at end of file
diff --git a/docs/new.md b/docs/new.md
new file mode 100644
--- /dev/null
+++ b/docs/new.md
@@ -0,0 +1,2 @@
+# Title
+body
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
diff --git a/a.txt b/b.txt
similarity index 100%
rename from a.txt
rename to b.txt
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
`

describe('parseUnifiedDiff', () => {
  const files = parseUnifiedDiff(SAMPLE)

  it('splits a git diff into files with statuses', () => {
    expect(files.map(f => [f.path, f.status])).toEqual([
      ['src/app.py', 'modified'],
      ['docs/new.md', 'added'],
      ['old.txt', 'deleted'],
      ['b.txt', 'renamed'],
      ['img.png', 'modified'],
    ])
    expect(files[3].oldPath).toBe('a.txt')
    expect(files[4].binary).toBe(true)
  })

  it('counts additions and deletions', () => {
    expect(files[0]).toMatchObject({ additions: 3, deletions: 2 })
    expect(files[1]).toMatchObject({ additions: 2, deletions: 0 })
    expect(files[2]).toMatchObject({ additions: 0, deletions: 1 })
    expect(diffTotals(files)).toEqual({ files: 5, additions: 5, deletions: 3 })
  })

  it('numbers lines on both sides', () => {
    const lines = files[0].hunks[0].lines
    expect(lines[0]).toMatchObject({ type: 'ctx', oldNo: 1, newNo: 1 })
    expect(lines[1]).toMatchObject({ type: 'del', oldNo: 2, newNo: null, text: 'print("old")' })
    expect(lines[2]).toMatchObject({ type: 'add', oldNo: null, newNo: 2 })
    expect(lines[4]).toMatchObject({ type: 'ctx', oldNo: 3, newNo: 4 })
    expect(files[0].hunks[1].lines[0]).toMatchObject({ oldNo: 20, newNo: 21 })
  })

  it('keeps "no newline" markers as notes, not changes', () => {
    const notes = files[0].hunks[1].lines.filter(l => l.type === 'note')
    expect(notes).toHaveLength(2)
  })

  it('treats a removed line that starts with dashes as a deletion inside a hunk', () => {
    const f = parseUnifiedDiff('--- a/x.sql\n+++ b/x.sql\n@@ -1,2 +1,1 @@\n--- comment\n keep\n')
    expect(f).toHaveLength(1)
    expect(f[0].hunks[0].lines[0]).toMatchObject({ type: 'del', text: '-- comment' })
    expect(f[0].deletions).toBe(1)
  })

  it('parses plain unified diffs without a git header', () => {
    const f = parseUnifiedDiff('--- a/foo.txt\n+++ b/foo.txt\n@@ -1 +1 @@\n-a\n+b\n')
    expect(f[0]).toMatchObject({ path: 'foo.txt', additions: 1, deletions: 1 })
  })

  it('returns nothing for empty input', () => {
    expect(parseUnifiedDiff('')).toEqual([])
    expect(parseUnifiedDiff(null)).toEqual([])
  })

  it('pairs deletions with additions for the split view', () => {
    const rows = toSplitRows(files[0])
    expect(rows[0].header).toContain('@@ -1,4')
    const change = rows[2]
    expect(change.left).toMatchObject({ type: 'del', text: 'print("old")' })
    expect(change.right).toMatchObject({ type: 'add', text: 'print("new")' })
    const extra = rows[3]
    expect(extra.left).toBeNull()
    expect(extra.right).toMatchObject({ text: 'print("extra")' })
  })

  it('filters to a set of paths', () => {
    expect(filterFiles(files, new Set(['docs/new.md'])).map(f => f.path)).toEqual(['docs/new.md'])
    expect(filterFiles(files, new Set(['a.txt'])).map(f => f.path)).toEqual(['b.txt'])
  })
})
