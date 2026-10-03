import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { onUnauthorized, setToken } from '../api/auth'
import { getDemoInfo } from '../api/platform'
import { CommandPalette } from '../design/CommandPalette'
import { Icon, type IconName } from '../design/icons'
import { Dialog, Drawer, ToastProvider } from '../design/overlay'
import { Badge, Button, Field, IconButton, Input, Kbd, Skeleton, StatusDot } from '../design/primitives'
import { NewRunDialog } from '../features/NewRunDialog'
import EnginesPage from '../features/EnginesPage'
import HomePage from '../features/home/HomePage'
import RunPage from '../features/run/RunPage'
import RunsPage from '../features/runs/RunsPage'
import MetricsPage from '../features/MetricsPage'
import SettingsPage from '../features/SettingsPage'
import { buildHash, navigate, useRoute, type RouteName } from '../lib/router'
import { useTheme } from '../theme/ThemeProvider'
import { buildCommands } from './commands'
import { AppProvider, useApp } from './AppContext'
import { SHORTCUT_HELP, useGlobalShortcuts, type ShortcutAction } from './shortcuts'

const IntegrationsPage = lazy(() => import('../features/integrations/IntegrationsPage'))
const RepositoriesPage = lazy(() => import('../features/repositories/RepositoriesPage'))
const ExtrasPage = lazy(() => import('../features/ExtrasPage'))
const WorkflowsPage = lazy(() => import('../features/workflows/WorkflowsPage'))
const BuilderPage = lazy(() => import('../features/workflows/BuilderPage'))
const WorkflowRunPage = lazy(() => import('../features/workflows/WorkflowRunPage'))

const NAV: { name: RouteName; label: string; icon: IconName; hash: string }[] = [
  { name: 'home', label: 'Home', icon: 'home', hash: buildHash('home') },
  { name: 'runs', label: 'Runs', icon: 'runs', hash: buildHash('runs') },
  { name: 'engines', label: 'Engines', icon: 'engine', hash: buildHash('engines') },
  { name: 'workflows', label: 'Workflows', icon: 'link', hash: buildHash('workflows') },
  { name: 'metrics', label: 'Metrics', icon: 'clock', hash: buildHash('metrics') },
  { name: 'integrations', label: 'Integrations', icon: 'link', hash: buildHash('integrations') },
  { name: 'repositories', label: 'Repositories', icon: 'file', hash: buildHash('repositories') },
  { name: 'settings', label: 'Settings', icon: 'settings', hash: buildHash('settings') },
  { name: 'extras', label: 'Extras', icon: 'extras', hash: buildHash('extras') },
]

function Brand() {
  return (
    <a href="#/" className="brand" aria-label="PatchQuest home">
      <span className="brand__mark" aria-hidden="true" />
      <span className="brand__name">PatchQuest</span>
    </a>
  )
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const route = useRoute()
  const { runs } = useApp()
  const attention = runs.filter(r => r.status === 'waiting_approval').length
  const active = route.name === 'run' ? 'runs' : route.name === 'workflow' || route.name === 'workflow-run' ? 'workflows' : route.name
  return (
    <nav aria-label="Main" className="nav">
      {NAV.map(n => (
        <a key={n.name} href={n.hash} className="nav__link" aria-current={active === n.name ? 'page' : undefined} onClick={onNavigate}>
          <Icon name={n.icon} />
          <span>{n.label}</span>
          {n.name === 'runs' && attention > 0 && <Badge tone="warning" title="Runs waiting for your approval">{attention}</Badge>}
        </a>
      ))}
    </nav>
  )
}

function ThemeButton() {
  const { resolved, setMode } = useTheme()
  return <IconButton icon={resolved === 'dark' ? 'sun' : 'moon'} label={resolved === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} onClick={() => setMode(resolved === 'dark' ? 'light' : 'dark')} />
}

function TokenDialog() {
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')
  useEffect(() => onUnauthorized(() => setOpen(true)), [])
  const save = () => {
    setToken(value.trim() || null)
    window.location.reload()
  }
  return (
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
      title="Enter your access token"
      description="This PatchQuest server needs a token. Yours is missing or no longer valid."
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => setOpen(false)}>Later</Button>
          <Button variant="primary" onClick={save} disabled={!value.trim()}>Continue</Button>
        </>
      }
    >
      <form onSubmit={e => { e.preventDefault(); if (value.trim()) save() }}>
        <Field label="Access token" hint="It is kept in this browser only.">
          {p => <Input {...p} type="password" autoComplete="off" value={value} onChange={e => setValue(e.target.value)} />}
        </Field>
      </form>
    </Dialog>
  )
}

function HelpDialog() {
  const { helpOpen, setHelpOpen } = useApp()
  return (
    <Dialog open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard shortcuts" size="sm">
      <dl className="shortcuts">
        {SHORTCUT_HELP.map(s => (
          <div key={s.label} className="shortcuts__row">
            <dt>{s.label}</dt>
            <dd>{s.keys.map((k, i) => <span key={k}>{i > 0 && ' then '}<Kbd>{k}</Kbd></span>)}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  )
}

/** Shown only by `patchquest demo`: its model answers, GitHub and Slack are simulated, and the numbers on screen say so. */
function DemoBanner() {
  const [demo, setDemo] = useState(false)
  useEffect(() => {
    const c = new AbortController()
    getDemoInfo(c.signal).then(d => setDemo(!!d.demo)).catch(() => undefined)
    return () => c.abort()
  }, [])
  if (!demo) return null
  return (
    <p className="demo-banner" role="note">
      <strong>Demo environment.</strong> Model answers, GitHub and Slack are simulated, so timings and token counts come from scripted models. The pipeline, ledger, policy and metrics are the real ones.
    </p>
  )
}

function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="page"><Skeleton width="100%" height={160} /></div>}>{children}</Suspense>
}

function Shell() {
  const route = useRoute()
  const app = useApp()
  const { resolved, setMode } = useTheme()
  const [nav, setNav] = useState(false)

  const toggleTheme = () => setMode(resolved === 'dark' ? 'light' : 'dark')
  const commands = useMemo(
    () => buildCommands({ runs: app.runs, openNewRun: app.openNewRun, toggleTheme, openHelp: () => app.setHelpOpen(true), currentRunCommands: app.runCommands, workflows: app.workflows }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [app.runs, app.runCommands, app.workflows, resolved, app.openNewRun],
  )

  const anyDialog = app.newRunOpen || app.helpOpen
  useGlobalShortcuts((a: ShortcutAction) => {
    if (a === 'palette') return app.setPaletteOpen(!app.paletteOpen)
    if (app.paletteOpen || anyDialog) return
    if (a === 'new-run') app.openNewRun()
    else if (a === 'help') app.setHelpOpen(true)
    else if (a === 'go-home') navigate(buildHash('home'))
    else if (a === 'go-runs') navigate(buildHash('runs'))
    else if (a === 'go-engines') navigate(buildHash('engines'))
    else if (a === 'go-workflows') navigate(buildHash('workflows'))
    else if (a === 'go-metrics') navigate(buildHash('metrics'))
    else if (a === 'go-integrations') navigate(buildHash('integrations'))
    else if (a === 'go-repositories') navigate(buildHash('repositories'))
    else if (a === 'go-settings') navigate(buildHash('settings'))
    else if (a === 'go-extras') navigate(buildHash('extras'))
  }, app.paletteOpen)

  // Move focus to the page on navigation so keyboard and screen-reader users start at the top.
  useEffect(() => {
    document.getElementById('main')?.focus({ preventScroll: true })
    window.scrollTo?.(0, 0)
  }, [route.name, route.params.id])

  let page: ReactNode
  switch (route.name) {
    case 'home': page = <HomePage />; break
    case 'runs': page = <RunsPage />; break
    case 'run': page = <RunPage key={route.params.id} runId={route.params.id} tab={route.query.tab} />; break
    case 'engines': page = <EnginesPage />; break
    case 'workflows': page = <Page><WorkflowsPage /></Page>; break
    case 'workflow': page = <Page><BuilderPage key={route.params.id} id={route.params.id === 'new' ? null : route.params.id} template={route.query.template} runIntent={route.query.run === '1'} /></Page>; break
    case 'workflow-run': page = <Page><WorkflowRunPage key={route.params.id} id={route.params.id} /></Page>; break
    case 'integrations': page = <Page><IntegrationsPage /></Page>; break
    case 'repositories': page = <Page><RepositoriesPage /></Page>; break
    case 'metrics': page = <MetricsPage />; break
    case 'settings': page = <SettingsPage />; break
    case 'extras': page = <Page><ExtrasPage section={route.params.section} /></Page>; break
    default:
      page = <div className="page"><h1>Page not found</h1><p className="ui-muted">That address doesn&apos;t exist. <a href="#/">Go home</a>.</p></div>
  }

  return (
    <div className="shell">
      <a href="#main" className="skip-link" onClick={e => { e.preventDefault(); document.getElementById('main')?.focus() }}>Skip to content</a>
      <aside className="sidebar">
        <Brand />
        <NavLinks />
        <div className="sidebar__foot">
          <span className="health" role="status">
            <StatusDot tone={app.health === 'ok' ? 'success' : app.health === 'down' ? 'danger' : 'muted'} />
            {app.health === 'ok' ? 'Server connected' : app.health === 'down' ? 'Server unreachable' : 'Connecting…'}
          </span>
          <button type="button" className="sidebar__shortcuts" onClick={() => app.setHelpOpen(true)}>Keyboard shortcuts <Kbd>?</Kbd></button>
        </div>
      </aside>
      <div className="shell__body">
        <header className="topbar">
          <IconButton icon="menu" label="Open navigation" className="topbar__menu" onClick={() => setNav(true)} />
          <span className="topbar__brand"><Brand /></span>
          <button type="button" className="topbar__search" onClick={() => app.setPaletteOpen(true)} aria-label="Open command palette">
            <Icon name="search" size={16} />
            <span>Search or jump to…</span>
            <span className="topbar__keys"><Kbd>Ctrl</Kbd><Kbd>K</Kbd></span>
          </button>
          <Button variant="primary" icon="plus" onClick={app.openNewRun} className="topbar__new"><span className="topbar__new-label">New run</span></Button>
          <ThemeButton />
        </header>
        <DemoBanner />
        <main id="main" tabIndex={-1} className="main">
          {page}
        </main>
      </div>

      <Drawer open={nav} onClose={() => setNav(false)} title="Navigation" side="left" size="sm">
        <NavLinks onNavigate={() => setNav(false)} />
      </Drawer>
      <CommandPalette open={app.paletteOpen} onClose={() => app.setPaletteOpen(false)} commands={commands} />
      <NewRunDialog />
      <HelpDialog />
      <TokenDialog />
    </div>
  )
}

export default function AppShell() {
  return (
    <ToastProvider>
      <AppProvider>
        <Shell />
      </AppProvider>
    </ToastProvider>
  )
}
