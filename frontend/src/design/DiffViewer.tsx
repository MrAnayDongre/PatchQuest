import { useEffect, useMemo, useState } from 'react'
import { diffTotals, toSplitRows, type DiffFile, type DiffLine } from '../lib/diff'
import { Badge, Segmented, cx } from './primitives'

interface DiffViewerProps {
  files: DiffFile[]
  /** Called when the selected file changes (keeps selection across tabs if the caller wants it). */
  initialPath?: string
  emptyMessage?: string
}

const MAX_LINES = 1200

function marker(t: DiffLine['type']): string {
  return t === 'add' ? '+' : t === 'del' ? '−' : t === 'note' ? '' : ' '
}

const STATUS_TONE = { added: 'success', deleted: 'danger', modified: 'neutral', renamed: 'info' } as const
const STATUS_TEXT = { added: 'New', deleted: 'Deleted', modified: 'Modified', renamed: 'Renamed' } as const

export function DiffViewer({ files, initialPath, emptyMessage = 'No changes to show.' }: DiffViewerProps) {
  const [selected, setSelected] = useState(initialPath ?? files[0]?.path ?? '')
  const [mode, setMode] = useState<'unified' | 'split'>('unified')
  const [showAll, setShowAll] = useState(false)
  const totals = useMemo(() => diffTotals(files), [files])

  useEffect(() => {
    if (!files.some(f => f.path === selected)) setSelected(files[0]?.path ?? '')
  }, [files, selected])
  useEffect(() => setShowAll(false), [selected])

  const file = files.find(f => f.path === selected) ?? files[0]
  const lineCount = file ? file.hunks.reduce((n, h) => n + h.lines.length, 0) : 0
  const truncated = !showAll && lineCount > MAX_LINES

  if (!files.length || !file) return <p className="ui-muted ui-diff__empty">{emptyMessage}</p>

  return (
    <div className="ui-diff">
      <nav className="ui-diff__files" aria-label="Changed files">
        <div className="ui-diff__summary">
          {totals.files} {totals.files === 1 ? 'file' : 'files'} · <span className="ui-text--success">+{totals.additions}</span> <span className="ui-text--danger">−{totals.deletions}</span>
        </div>
        <ul>
          {files.map(f => (
            <li key={f.path}>
              <button type="button" className="ui-diff__file" aria-current={f.path === file.path} onClick={() => setSelected(f.path)} title={f.path}>
                <span className="ui-diff__filename ui-truncate">{f.path}</span>
                <span className="ui-diff__counts">
                  <span className="ui-text--success">+{f.additions}</span> <span className="ui-text--danger">−{f.deletions}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div className="ui-diff__main">
        <div className="ui-diff__toolbar">
          <div className="ui-diff__path">
            <span className="ui-truncate ui-mono">{file.oldPath && file.status === 'renamed' ? `${file.oldPath} → ${file.path}` : file.path}</span>
            <Badge tone={STATUS_TONE[file.status]}>{STATUS_TEXT[file.status]}</Badge>
          </div>
          <Segmented
            label="Diff layout"
            value={mode}
            onChange={setMode}
            options={[
              { id: 'unified', label: 'Unified' },
              { id: 'split', label: 'Split' },
            ]}
          />
        </div>

        {file.binary ? (
          <p className="ui-muted ui-diff__empty">Binary file; contents are not shown.</p>
        ) : file.hunks.length === 0 ? (
          <p className="ui-muted ui-diff__empty">{file.status === 'renamed' ? 'Renamed with no content changes.' : 'No textual changes.'}</p>
        ) : mode === 'unified' ? (
          <div className="ui-diff__scroll" tabIndex={0} aria-label={`Changes in ${file.path}`}>
            <table className="ui-diff__table">
              <tbody>
                {renderUnified(file, truncated ? MAX_LINES : Infinity)}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="ui-diff__scroll" tabIndex={0} aria-label={`Changes in ${file.path}, side by side`}>
            <table className="ui-diff__table ui-diff__table--split">
              <tbody>{renderSplit(file, truncated ? MAX_LINES : Infinity)}</tbody>
            </table>
          </div>
        )}
        {truncated && (
          <button type="button" className="ui-diff__more" onClick={() => setShowAll(true)}>
            Show all {lineCount.toLocaleString()} lines
          </button>
        )}
      </div>
    </div>
  )
}

function renderUnified(file: DiffFile, budget: number) {
  const rows: JSX.Element[] = []
  let used = 0
  for (let h = 0; h < file.hunks.length && used < budget; h++) {
    const hunk = file.hunks[h]
    rows.push(
      <tr key={`h${h}`} className="ui-diff__hunk">
        <td colSpan={3}>{hunk.header}</td>
      </tr>,
    )
    for (let i = 0; i < hunk.lines.length && used < budget; i++, used++) {
      const l = hunk.lines[i]
      rows.push(
        <tr key={`${h}-${i}`} className={`ui-diff__line ui-diff__line--${l.type}`}>
          <td className="ui-diff__no" aria-hidden="true">{l.oldNo ?? ''}</td>
          <td className="ui-diff__no" aria-hidden="true">{l.newNo ?? ''}</td>
          <td className="ui-diff__code">
            <span className="ui-sr-only">{l.type === 'add' ? 'added: ' : l.type === 'del' ? 'removed: ' : ''}</span>
            <span aria-hidden="true" className="ui-diff__mark">{marker(l.type)}</span>
            {l.type === 'note' ? <em>{l.text}</em> : l.text}
          </td>
        </tr>,
      )
    }
  }
  return rows
}

function renderSplit(file: DiffFile, budget: number) {
  const out: JSX.Element[] = []
  const rows = toSplitRows(file)
  for (let i = 0; i < rows.length && i < budget; i++) {
    const r = rows[i]
    if (r.header !== undefined) {
      out.push(
        <tr key={i} className="ui-diff__hunk">
          <td colSpan={4}>{r.header}</td>
        </tr>,
      )
      continue
    }
    out.push(
      <tr key={i} className="ui-diff__line">
        <td className={cx('ui-diff__no', r.left && `ui-diff__no--${r.left.type}`)} aria-hidden="true">{r.left?.oldNo ?? ''}</td>
        <td className={cx('ui-diff__code', r.left ? `ui-diff__line--${r.left.type}` : 'ui-diff__line--empty')}>{r.left?.text}</td>
        <td className={cx('ui-diff__no', r.right && `ui-diff__no--${r.right.type}`)} aria-hidden="true">{r.right?.newNo ?? ''}</td>
        <td className={cx('ui-diff__code', r.right ? `ui-diff__line--${r.right.type}` : 'ui-diff__line--empty')}>{r.right?.text}</td>
      </tr>,
    )
  }
  return out
}
