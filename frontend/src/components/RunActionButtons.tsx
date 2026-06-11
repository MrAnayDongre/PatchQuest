import PixelButton from './PixelButton'
import { shortRunId } from '../lib/runId'

interface RunActionButtonsProps {
  runId: string
  onOpenConsole: (runId: string) => void
  onViewReport: (runId: string) => void
  compact?: boolean
  showRunId?: boolean
}

export default function RunActionButtons({
  runId,
  onOpenConsole,
  onViewReport,
  compact = false,
  showRunId = false,
}: RunActionButtonsProps) {
  const sizeClass = compact ? 'pixel-btn--sm' : ''
  return (
    <div className="run-actions">
      {showRunId && (
        <span className="run-actions__id mono" title={runId}>
          {shortRunId(runId)}
        </span>
      )}
      <PixelButton variant="secondary" className={sizeClass} onClick={() => onOpenConsole(runId)}>
        Open Console
      </PixelButton>
      <PixelButton variant="secondary" className={sizeClass} onClick={() => onViewReport(runId)}>
        View Report
      </PixelButton>
      <PixelButton variant="ghost" className={sizeClass} onClick={() => onOpenConsole(runId)}>
        View Events
      </PixelButton>
    </div>
  )
}
