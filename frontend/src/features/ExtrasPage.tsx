import { useState } from 'react'
import '../styles/themes.css'
import '../styles/globals.css'
import '../styles/lumina.css'
import '../styles/arcade-theme.css'
import '../styles/layout.css'
import '../styles/games.css'
import CalendarPage from '../pages/CalendarPage'
import MemoryPage from '../pages/MemoryPage'
import SchedulerPage from '../pages/SchedulerPage'
import SearchPage from '../pages/SearchPage'
import GameOverlay from '../games/GameOverlay'
import { GAMES } from '../games/GameRegistry'
import type { GameMode } from '../api/types'
import { Tabs, TabPanel } from '../design/primitives'
import { navigate, runHash } from '../lib/router'
import { PageHeader } from './common'

const SECTIONS = [
  { id: 'games', label: 'Games' },
  { id: 'scheduler', label: 'Scheduler' },
  { id: 'calendar', label: 'Calendar' },
  { id: 'search', label: 'Search' },
  { id: 'memory', label: 'Memory' },
]

/** Off-mission pages kept working as they were. Their styles are scoped under .legacy-root. */
export default function ExtrasPage({ section }: { section?: string }) {
  const current = SECTIONS.some(s => s.id === section) ? (section as string) : 'games'
  const [game, setGame] = useState<GameMode>(null)
  const openRun = (id: string) => navigate(runHash(id))
  const nav = { onOpenConsole: openRun, onViewReport: openRun }
  return (
    <div className="page">
      <PageHeader title="Extras" subtitle="Side features that sit outside the main mission flow." />
      <Tabs label="Extras" idPrefix="extras" value={current} onChange={id => navigate(`#/extras/${id}`)} tabs={SECTIONS} />
      <TabPanel idPrefix="extras" id={current} active>
        <div className="legacy-root">
          {current === 'games' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.75rem' }}>
              {GAMES.map(g => (
                <button key={g.id} type="button" className="game-card" onClick={() => setGame(g.id)}>
                  <span className="game-card__title">{g.name}</span>
                  <span className="game-card__desc">{g.description}</span>
                  <span className="game-card__desc">{g.difficulty} · {g.duration}</span>
                </button>
              ))}
            </div>
          )}
          {current === 'scheduler' && <SchedulerPage {...nav} />}
          {current === 'calendar' && <CalendarPage {...nav} />}
          {current === 'search' && <SearchPage />}
          {current === 'memory' && <MemoryPage {...nav} />}
          {game && <GameOverlay game={game} onClose={() => setGame(null)} />}
        </div>
      </TabPanel>
    </div>
  )
}
