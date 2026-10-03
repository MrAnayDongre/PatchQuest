import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useRunStream } from '../../src/hooks/useRunStream'
import { reduceEvents } from '../../src/lib/runState'
import { ev, happyRun } from '../features/helpers'
import { FakeEventSource, mockFetch } from '../components/testUtils'

const run = (status: string) => ({
  id: 'r1', repo_path: '/r', task: 't', status, current_phase: null, provider: 'mock', model: null, runtime_mode: 'local',
  created_at: '2026-01-01T10:00:00Z', updated_at: '2026-01-01T10:00:00Z', completed_at: null,
})

const all = happyRun()

beforeEach(() => {
  FakeEventSource.reset()
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useRunStream', () => {
  it('loads the persisted ledger first, then streams from the last id without duplicates', async () => {
    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('running') },
      { match: /\/events/, body: all.slice(0, 10) },
    ])
    const { result } = renderHook(() => useRunStream('r1'))
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
    expect(FakeEventSource.last.url).toContain('after_id=10')
    expect(result.current.state.lastId).toBe(10)

    act(() => {
      FakeEventSource.last.open()
      FakeEventSource.last.emit({ type: 'ping', run_id: 'r1' })
      FakeEventSource.last.emit(all[9]) // duplicate of an event we already have
      FakeEventSource.last.emit(all[10])
      FakeEventSource.last.emit(all[11])
    })
    await waitFor(() => expect(result.current.state.lastId).toBe(12))
    expect(result.current.connection).toBe('live')
    expect(result.current.state.events.map(e => e.id)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    expect(result.current.state.pendingApprovals).toHaveLength(1)
  })

  it('reaches the same state whether it loads everything or streams it (refresh equivalence)', async () => {
    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('completed') },
      { match: /\/events/, body: all },
    ])
    const refreshed = renderHook(() => useRunStream('r1'))
    await waitFor(() => expect(refreshed.result.current.connection).toBe('ended'))

    FakeEventSource.reset()
    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('running') },
      { match: /\/events/, body: [] },
    ])
    const live = renderHook(() => useRunStream('r1'))
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
    act(() => {
      for (const e of all.slice(0, -1)) FakeEventSource.last.emit(e)
    })
    mockFetch([{ match: /\/api\/runs\/r1$/, body: run('completed') }, { match: /\/events/, body: [] }])
    act(() => FakeEventSource.last.emit(all[all.length - 1]))
    await waitFor(() => expect(live.result.current.connection).toBe('ended'))
    expect(live.result.current.state).toEqual(refreshed.result.current.state)
    expect(live.result.current.state).toEqual(reduceEvents(all))
  })

  it('shows "reconnecting", then resumes from the last id after a dropped connection', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('running') },
      { match: /\/events\?after_id=5$/, body: [] },
      { match: /\/events$/, body: all.slice(0, 5) },
    ])
    const { result } = renderHook(() => useRunStream('r1'))
    await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(1))
    act(() => {
      FakeEventSource.last.open()
      FakeEventSource.last.emit(all[5])
    })
    await vi.waitFor(() => expect(result.current.state.lastId).toBe(6))

    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('running') },
      { match: /\/events\?after_id=6$/, body: [all[6]] },
    ])
    act(() => FakeEventSource.last.fail())
    await vi.waitFor(() => expect(result.current.connection).toBe('reconnecting'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200)
    })
    await vi.waitFor(() => expect(FakeEventSource.instances.length).toBe(2))
    expect(FakeEventSource.last.url).toContain('after_id=7')
    expect(result.current.state.lastId).toBe(7)
    act(() => FakeEventSource.last.open())
    await vi.waitFor(() => expect(result.current.connection).toBe('live'))
  })

  it('stops following after a terminal event', async () => {
    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('completed') },
      { match: /\/events/, body: all.slice(0, 20) },
    ])
    const { result } = renderHook(() => useRunStream('r1'))
    await waitFor(() => expect(result.current.connection).toBe('ended'))
    expect(FakeEventSource.instances.length).toBe(0)
  })

  it('treats an interrupted run as ended and exposes the interruption', async () => {
    const events = [ev(1, 'run_state_changed', { payload: { from: 'created', to: 'running' } }), ev(2, 'run_interrupted', { message: 'process stopped' })]
    mockFetch([
      { match: /\/api\/runs\/r1$/, body: run('interrupted') },
      { match: /\/events/, body: events },
    ])
    const { result } = renderHook(() => useRunStream('r1'))
    await waitFor(() => expect(result.current.connection).toBe('ended'))
    expect(result.current.state.status).toBe('interrupted')
    expect(result.current.state.statusReason).toBe('process stopped')
  })

  it('reports a missing run as an error', async () => {
    mockFetch([{ match: /\/api\/runs\/r1/, status: 404, body: { detail: 'Run not found' } }])
    const { result } = renderHook(() => useRunStream('r1'))
    await waitFor(() => expect(result.current.connection).toBe('error'))
    expect(result.current.error?.status).toBe(404)
  })
})
