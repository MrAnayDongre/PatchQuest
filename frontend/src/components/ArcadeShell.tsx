import { ReactNode } from 'react'

interface ArcadeShellProps {
  children: ReactNode
}

export default function ArcadeShell({ children }: ArcadeShellProps) {
  return <div className="page-grid">{children}</div>
}
