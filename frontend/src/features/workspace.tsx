import type { ReactNode } from 'react'
import { useApp } from '../app/AppContext'
import { EmptyState, Field, Select, Skeleton } from '../design/primitives'
import { ErrorNotice } from './common'

/** Only appears when the person belongs to more than one workspace. */
export function WorkspaceSelect() {
  const { workspaces, workspaceId, setWorkspaceId } = useApp()
  if (workspaces.length < 2) return null
  return (
    <Field label="Workspace" className="workspace-select">
      {p => <Select {...p} value={workspaceId ?? ''} onChange={e => setWorkspaceId(e.target.value)}>{workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</Select>}
    </Field>
  )
}

/** Waits for /api/me, then renders its children with the chosen workspace id. */
export function WorkspaceGate({ children }: { children: (workspaceId: string) => ReactNode }) {
  const { workspacesLoaded, workspaceError, workspaceId } = useApp()
  if (!workspacesLoaded) return <Skeleton width="100%" height={160} />
  if (workspaceError) return <ErrorNotice error={workspaceError} subject="your workspaces" onRetry={() => window.location.reload()} />
  if (!workspaceId) return <EmptyState icon="shield" title="You don't belong to a workspace yet">Ask a workspace owner to add you, then reload this page.</EmptyState>
  return <>{children(workspaceId)}</>
}
