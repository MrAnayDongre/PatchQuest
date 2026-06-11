import { useState, useEffect, useCallback } from 'react'
import TopNav from './components/TopNav'
import HomePage from './pages/HomePage'
import RunDashboardPage from './pages/RunDashboardPage'
import MemoryPage from './pages/MemoryPage'
import SettingsPage from './pages/SettingsPage'
import ReportPage from './pages/ReportPage'
import SafetyQueuePage from './pages/SafetyQueuePage'
import SchedulerPage from './pages/SchedulerPage'
import SearchPage from './pages/SearchPage'
import CalendarPage from './pages/CalendarPage'
import { healthCheck } from './api/client'

type Page = 'home' | 'dashboard' | 'memory' | 'settings' | 'report' | 'safety' | 'scheduler' | 'search' | 'calendar'

export default function App() {
  const [page, setPage] = useState<Page>('home')
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null)

  useEffect(() => {
    healthCheck()
      .then(() => setBackendOnline(true))
      .catch(() => setBackendOnline(false))
  }, [page])

  const openRunConsole = useCallback((runId: string) => {
    setActiveRunId(runId)
    setPage('dashboard')
  }, [])

  const openRunReport = useCallback((runId: string) => {
    setActiveRunId(runId)
    setPage('report')
  }, [])

  const handleRunCreated = (runId: string) => {
    setActiveRunId(runId)
    setPage('dashboard')
  }

  const runNav = { onOpenConsole: openRunConsole, onViewReport: openRunReport }

  const renderPage = () => {
    switch (page) {
      case 'home':
        return (
          <HomePage
            onRunCreated={handleRunCreated}
            onViewConsole={() => setPage('dashboard')}
            onViewReports={() => setPage('report')}
            hasActiveRun={!!activeRunId}
          />
        )
      case 'dashboard':
        return (
          <RunDashboardPage
            runId={activeRunId}
            onViewReport={() => activeRunId && openRunReport(activeRunId)}
            onOpenConsole={openRunConsole}
            onViewReportForRun={openRunReport}
          />
        )
      case 'memory':
        return <MemoryPage {...runNav} />
      case 'settings':
        return <SettingsPage />
      case 'report':
        return <ReportPage runId={activeRunId} onOpenConsole={openRunConsole} />
      case 'safety':
        return <SafetyQueuePage runId={activeRunId} />
      case 'scheduler':
        return <SchedulerPage {...runNav} />
      case 'search':
        return <SearchPage />
      case 'calendar':
        return <CalendarPage {...runNav} />
      default:
        return (
          <HomePage
            onRunCreated={handleRunCreated}
            onViewConsole={() => setPage('dashboard')}
            onViewReports={() => setPage('report')}
            hasActiveRun={!!activeRunId}
          />
        )
    }
  }

  return (
    <div className="app-layout">
      <div className="horizon-glow" aria-hidden />
      <div className="halftone-overlay" aria-hidden />
      <div className="arrow-texture" aria-hidden />
      <TopNav currentPage={page} onNavigate={setPage} backendOnline={backendOnline} />
      <main className="main-content">
        {renderPage()}
      </main>
    </div>
  )
}
