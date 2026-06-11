import { useState, useEffect, useCallback, useMemo } from 'react'
import PixelPanel from '../components/PixelPanel'
import PhaseTimeline from '../components/PhaseTimeline'
import ProgressBar from '../components/ProgressBar'
import EventConsole from '../components/EventConsole'
import PermissionPrompt from '../components/PermissionPrompt'
import GameOverlay from '../games/GameOverlay'
import { GAMES } from '../games/GameRegistry'
import CompletionStatusPanel from '../dashboard/CompletionStatusPanel'
import RecentRunsPanel from '../components/RecentRunsPanel'
import { getRun, getRunEvents, approveAction } from '../api/client'
import { subscribeToRunEvents } from '../api/events'
import { deriveMissionState, mergeRunEvents } from '../lib/phaseState'
import type { Run, RunEvent, GameMode } from '../api/types'

interface Props {
  runId: string | null
  onViewReport: () => void
  onOpenConsole: (runId: string) => void
  onViewReportForRun: (runId: string) => void
}

export default function RunDashboardPage({ runId, onViewReport, onOpenConsole, onViewReportForRun }: Props) {
  const [run, setRun] = useState<Run | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [pendingApproval, setPendingApproval] = useState<{ id: string; command: string; reason: string } | null>(null)
  const [activeGame, setActiveGame] = useState<GameMode>(null)
  const [showStatus, setShowStatus] = useState(false)

  const refreshRun = useCallback(async () => {
    if (!runId) return
    try {
      const [runData, eventData] = await Promise.all([getRun(runId), getRunEvents(runId)])
      setRun(runData)
      setEvents(eventData)
    } catch {
      // keep last known state
    }
  }, [runId])

  useEffect(() => {
    if (!runId) return
    refreshRun()
  }, [runId, refreshRun])

  useEffect(() => {
    if (!runId) return
    const unsub = subscribeToRunEvents(runId, (event) => {
      setEvents(prev => mergeRunEvents(prev, event))
      if (event.type === 'permission_required' && event.payload) {
        setPendingApproval({
          id: event.payload.approval_id as string,
          command: (event.payload.command as string) || '',
          reason: (event.payload.reason as string) || '',
        })
      }
      if (event.type === 'run_completed' || event.type === 'run_failed') {
        refreshRun()
      }
    })
    return unsub
  }, [runId, refreshRun])

  const { phases, progress, status } = useMemo(
    () => deriveMissionState(events, run),
    [events, run],
  )

  const handleApprove = useCallback(async (id: string) => {
    if (!runId) return
    await approveAction(runId, id, true)
    setPendingApproval(null)
  }, [runId])

  const handleReject = useCallback(async (id: string) => {
    if (!runId) return
    await approveAction(runId, id, false)
    setPendingApproval(null)
  }, [runId])

  if (!runId) {
    return (
      <div className="launch-panel" style={{ maxWidth: '960px' }}>
        <section className="hero" style={{ paddingTop: 0 }}>
          <h1 className="hero__title" style={{ fontSize: 'clamp(1.5rem, 3vw, 2.2rem)' }}>
            Mission <em>Console</em>
          </h1>
          <p className="hero__subtitle">
            No mission is selected. Pick a recent run below or start a new one from the Mission page.
          </p>
        </section>
        <RecentRunsPanel
          onOpenConsole={onOpenConsole}
          onViewReport={onViewReportForRun}
          title="Recent Missions"
        />
      </div>
    )
  }

  return (
    <>
      <section className="console-hero">
        <div className="console-hero__icon-wrap">
          <div className="console-hero__icon">PQ</div>
          <div className="console-hero__connector" aria-hidden />
        </div>
        <h1 className="console-hero__title">Mission <em>Console</em></h1>
        <p className="console-hero__task">{run?.task || 'Loading mission…'}</p>
        <p className="console-hero__meta">{run?.repo_path}</p>
        <div className="chip-row" style={{ marginTop: '0.85rem' }}>
          {run?.provider && <span className="chip chip--cyan">{run.provider}</span>}
          {run?.model && <span className="chip">{run.model}</span>}
          {run?.runtime_mode && <span className="chip">{run.runtime_mode}</span>}
        </div>
      </section>

      <div className="product-section">
        <div className="page-grid page-grid--dashboard">
          <div className="console-sidebar">
            <PixelPanel title="Phase Flow">
              <PhaseTimeline phases={phases} />
            </PixelPanel>
            <PixelPanel title="Actions" className="console-sidebar__actions">
              <button
                type="button"
                className="pixel-btn pixel-btn--primary pixel-btn--block"
                onClick={onViewReport}
                disabled={status !== 'complete' && status !== 'failed'}
              >
                View Report
              </button>
            </PixelPanel>
            <PixelPanel title="Arcade Modules">
              <div className="game-selector">
                {GAMES.map(g => (
                  <button
                    key={g.id}
                    type="button"
                    className={`game-card ${activeGame === g.id ? 'game-card--active' : ''}`}
                    onClick={() => setActiveGame(g.id)}
                  >
                    <div className="game-card__title">{g.name}</div>
                    <div className="game-card__desc">{g.description}</div>
                    <div className="game-card__meta">
                      <span className="game-card__tag">{g.difficulty}</span>
                      <span className="game-card__tag">{g.duration}</span>
                    </div>
                  </button>
                ))}
              </div>
            </PixelPanel>
          </div>

          <div className="console-main">
            <PixelPanel title="Event Stream" glow>
              <EventConsole events={events} />
            </PixelPanel>
            {status === 'complete' && (
              <div className="console-main__cta">
                <button type="button" className="pixel-btn pixel-btn--primary" onClick={onViewReport}>
                  Open Final Report
                </button>
              </div>
            )}
          </div>

          <div className="console-aside">
            <PixelPanel title="Mission Intel">
              <dl className="meta-list">
                <div><dt>Run ID</dt><dd className="mono">{runId.slice(0, 8)}…</dd></div>
                <div><dt>Status</dt><dd>{run?.status || '—'}</dd></div>
                <div><dt>Phase</dt><dd>{run?.current_phase || '—'}</dd></div>
                <div><dt>Memory</dt><dd>{run?.memory_mode || 'repo'}</dd></div>
              </dl>
            </PixelPanel>
          </div>
        </div>
      </div>

      {activeGame && <GameOverlay game={activeGame} onClose={() => setActiveGame(null)} />}

      {pendingApproval && (
        <PermissionPrompt
          command={pendingApproval.command}
          reason={pendingApproval.reason}
          approvalId={pendingApproval.id}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}

      {showStatus && <CompletionStatusPanel phases={phases} onClose={() => setShowStatus(false)} />}

      <ProgressBar progress={progress} status={status} onClick={() => setShowStatus(!showStatus)} />
    </>
  )
}
