import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, healthCheck, listRuns } from '../api/client'
import { getMe, type WorkspaceInfo } from '../api/platform'
import { listWorkflows, type WorkflowSummary } from '../api/workflows'
import type { Run } from '../api/types'
import { usePolling } from '../hooks/useAsync'
import type { PaletteCommand } from '../lib/palette'
import { LOCAL_WORKSPACE } from '../lib/platform'

export type Health = 'checking' | 'ok' | 'down'

interface AppContextValue {
  runs: Run[]
  runsLoaded: boolean
  runsError: ApiError | null
  refreshRuns: () => Promise<void>
  health: Health
  workflows: WorkflowSummary[]
  refreshWorkflows: () => Promise<void>
  newRunOpen: boolean
  openNewRun: () => void
  closeNewRun: () => void
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
  helpOpen: boolean
  setHelpOpen: (open: boolean) => void
  workspaces: WorkspaceInfo[]
  /** False until /api/me answered; pages wait for it before asking for workspace data. */
  workspacesLoaded: boolean
  workspaceError: ApiError | null
  workspaceId: string | null
  setWorkspaceId: (id: string) => void
  /** Whether the chosen workspace grants a permission such as 'connector.manage'. */
  can: (permission: string) => boolean
  /** The run view registers the actions available for the run on screen. */
  runCommands: PaletteCommand[]
  setRunCommands: (cmds: PaletteCommand[]) => void
}

const WORKSPACE_KEY = 'patchquest.workspace'
const storedWorkspace = (): string | null => {
  try {
    return window.localStorage.getItem(WORKSPACE_KEY)
  } catch {
    return null
  }
}

const Ctx = createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [runsLoaded, setRunsLoaded] = useState(false)
  const [runsError, setRunsError] = useState<ApiError | null>(null)
  const [health, setHealth] = useState<Health>('checking')
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  const [workspacesLoaded, setWorkspacesLoaded] = useState(false)
  const [workspaceError, setWorkspaceError] = useState<ApiError | null>(null)
  const [chosen, setChosen] = useState<string | null>(storedWorkspace)
  const [newRunOpen, setNewRunOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [runCommands, setRunCommands] = useState<PaletteCommand[]>([])

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await listRuns())
      setRunsError(null)
      setHealth('ok')
    } catch (err) {
      const e = err instanceof ApiError ? err : new ApiError(0, String(err), 'network')
      setRunsError(e)
      if (e.status === 0 || e.status >= 500) setHealth('down')
      else if (e.status === 401) setHealth('ok')
    } finally {
      setRunsLoaded(true)
    }
  }, [])

  const refreshWorkflows = useCallback(async () => {
    try {
      setWorkflows(await listWorkflows())
    } catch {
      // the Workflows page explains permission and connection problems itself
    }
  }, [])

  useEffect(() => {
    void refreshWorkflows()
    void refreshRuns()
    healthCheck()
      .then(() => setHealth('ok'))
      .catch(() => setHealth(h => (h === 'ok' ? h : 'down')))
  }, [refreshRuns])

  useEffect(() => {
    getMe()
      .then(me => setWorkspaces(me.workspaces))
      // A server without /api/me is a single-user local server: the implicit local workspace, everything allowed.
      .catch(err => {
        if (err instanceof ApiError && err.status === 404) setWorkspaces([LOCAL_WORKSPACE])
        else setWorkspaceError(err instanceof ApiError ? err : new ApiError(0, String(err), 'network'))
      })
      .finally(() => setWorkspacesLoaded(true))
  }, [])

  const workspaceId = workspaces.find(w => w.id === chosen)?.id ?? workspaces[0]?.id ?? null
  const setWorkspaceId = useCallback((id: string) => {
    setChosen(id)
    try {
      window.localStorage.setItem(WORKSPACE_KEY, id)
    } catch {
      // the choice just won't survive a reload
    }
  }, [])
  const can = useCallback((permission: string) => !!workspaces.find(w => w.id === workspaceId)?.permissions.includes(permission), [workspaces, workspaceId])

  usePolling(() => void refreshRuns(), 5000)

  const value = useMemo<AppContextValue>(
    () => ({
      runs,
      runsLoaded,
      runsError,
      refreshRuns,
      health,
      workflows,
      refreshWorkflows,
      newRunOpen,
      openNewRun: () => setNewRunOpen(true),
      closeNewRun: () => setNewRunOpen(false),
      paletteOpen,
      setPaletteOpen,
      helpOpen,
      setHelpOpen,
      workspaces,
      workspacesLoaded,
      workspaceError,
      workspaceId,
      setWorkspaceId,
      can,
      runCommands,
      setRunCommands,
    }),
    [workspaces, workspacesLoaded, workspaceError, workspaceId, setWorkspaceId, can, runs, runsLoaded, runsError, refreshRuns, health, workflows, refreshWorkflows, newRunOpen, paletteOpen, helpOpen, runCommands],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useApp(): AppContextValue {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useApp must be used inside AppProvider')
  return ctx
}
