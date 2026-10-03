import { useEffect, useMemo, useState } from 'react'
import { useApp } from '../../app/AppContext'
import { isTypingTarget } from '../../app/shortcuts'
import { Button, Card, EmptyState, Field, Input, Select, Skeleton } from '../../design/primitives'
import { navigate, runHash } from '../../lib/router'
import { DEFAULT_FILTERS, filterRuns, moveSelection, type RunFilters } from '../../lib/runStats'
import { ErrorNotice, PageHeader, RunRow } from '../common'

const OUTCOMES = [
  ['all', 'Any outcome'],
  ['applied', 'Applied'],
  ['rejected', 'Rejected'],
  ['conflict', 'Conflict'],
  ['no_changes', 'No changes'],
  ['no_patch', 'No patch'],
  ['read_only', 'Read-only'],
] as const

export default function RunsPage() {
  const { runs, runsLoaded, runsError, refreshRuns, openNewRun } = useApp()
  const [filters, setFilters] = useState<RunFilters>(DEFAULT_FILTERS)
  const [selected, setSelected] = useState(-1)
  const list = useMemo(() => filterRuns(runs, filters), [runs, filters])
  const set = <K extends keyof RunFilters>(k: K, v: RunFilters[K]) => {
    setFilters(f => ({ ...f, [k]: v }))
    setSelected(-1)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return
      if (document.querySelector('[role="dialog"]')) return
      if (e.key === 'Enter' && selected >= 0 && list[selected] && !(e.target instanceof HTMLAnchorElement) && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault()
        navigate(runHash(list[selected].id))
        return
      }
      const next = moveSelection(selected, e.key, list.length)
      if (next !== selected && (e.key === 'j' || e.key === 'k')) {
        e.preventDefault()
        setSelected(next)
        document.getElementById(`run-row-${next}`)?.scrollIntoView?.({ block: 'nearest' })
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [list, selected])

  return (
    <div className="page">
      <PageHeader title="Runs" subtitle={runsLoaded ? `${list.length} of ${runs.length} shown` : undefined} actions={<Button variant="primary" icon="plus" onClick={openNewRun}>New run</Button>} />
      <div className="filters" role="search">
        <Field label="Search" className="filters__search">
          {p => <Input {...p} type="search" placeholder="Search tasks, repositories, models" value={filters.query} onChange={e => set('query', e.target.value)} />}
        </Field>
        <Field label="Status">
          {p => (
            <Select {...p} value={filters.status} onChange={e => set('status', e.target.value as RunFilters['status'])}>
              <option value="all">All statuses</option>
              <option value="needs-you">Needs you</option>
              <option value="active">Working</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </Select>
          )}
        </Field>
        <Field label="Outcome">
          {p => (
            <Select {...p} value={filters.outcome} onChange={e => set('outcome', e.target.value)}>
              {OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Sort by">
          {p => (
            <Select {...p} value={filters.sort} onChange={e => set('sort', e.target.value as RunFilters['sort'])}>
              <option value="updated">Recent activity</option>
              <option value="created">Newest first</option>
              <option value="status">Needs attention first</option>
            </Select>
          )}
        </Field>
      </div>
      {!!runsError && <ErrorNotice error={runsError} onRetry={() => void refreshRuns()} subject="your runs" />}
      {!runsLoaded ? (
        <Skeleton width="100%" height={200} />
      ) : list.length === 0 ? (
        <Card>
          <EmptyState
            icon="runs"
            title={runs.length ? 'No runs match these filters' : 'No runs yet'}
            action={runs.length ? <Button onClick={() => { setFilters(DEFAULT_FILTERS) }}>Clear filters</Button> : <Button variant="primary" onClick={openNewRun}>Start your first run</Button>}
          >
            {runs.length ? 'Try a different search or status.' : 'Start a run and it will appear here.'}
          </EmptyState>
        </Card>
      ) : (
        <Card padded={false}>
          <div role="list" aria-label="Runs">
            {list.map((r, i) => (
              <div role="listitem" key={r.id}>
                <RunRow run={r} id={`run-row-${i}`} selected={i === selected} />
              </div>
            ))}
          </div>
          <p className="ui-muted runs-hint">Tip: press <kbd className="ui-kbd">j</kbd> and <kbd className="ui-kbd">k</kbd> to move, <kbd className="ui-kbd">Enter</kbd> to open.</p>
        </Card>
      )}
    </div>
  )
}
