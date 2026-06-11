interface ProgressBarProps {
  progress: number
  status: 'ready' | 'running' | 'blocked' | 'failed' | 'complete'
  onClick?: () => void
}

export default function ProgressBar({ progress, status, onClick }: ProgressBarProps) {
  const statusLabels: Record<string, string> = {
    ready: 'READY',
    running: 'RUNNING',
    blocked: 'NEEDS APPROVAL',
    failed: 'FAILED',
    complete: 'COMPLETED',
  }

  const statusColors: Record<string, string> = {
    ready: 'var(--text-secondary)',
    running: 'var(--accent-cyan)',
    blocked: 'var(--accent-yellow)',
    failed: 'var(--accent-red)',
    complete: 'var(--accent-green)',
  }

  return (
    <div
      className={`completion-bar ${status === 'complete' ? 'completion-bar--complete' : ''} ${status === 'failed' ? 'completion-bar--failed' : ''}`}
      onClick={onClick}
      role="progressbar"
      aria-valuenow={Math.round(progress * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`Run progress: ${Math.round(progress * 100)}% - ${statusLabels[status]}`}
    >
      <span className="completion-bar__label">{Math.round(progress * 100)}%</span>
      <div className="completion-bar__track">
        <div className="completion-bar__fill" style={{ width: `${progress * 100}%` }} />
      </div>
      <span className="completion-bar__status" style={{ color: statusColors[status] }}>
        {statusLabels[status]}
      </span>
    </div>
  )
}
