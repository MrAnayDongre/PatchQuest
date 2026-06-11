import PixelPanel from '../components/PixelPanel'
import XCloseButton from '../components/XCloseButton'
import { PHASE_LABELS } from '../lib/phases'

interface Props {
  phases: { phase: string; status: string }[]
  onClose: () => void
}

export default function CompletionStatusPanel({ phases, onClose }: Props) {
  const terminal = phases.filter(p => ['complete', 'skipped', 'failed'].includes(p.status)).length
  const total = phases.length

  return (
    <div style={{
      position: 'fixed', bottom: '70px', left: '50%', transform: 'translateX(-50%)',
      zIndex: 300, minWidth: '400px',
    }}>
      <PixelPanel glow>
        <div style={{ position: 'relative' }}>
          <XCloseButton onClick={onClose} />
          <div className="section-title" style={{ marginBottom: '0.75rem' }}>
            Mission Progress — {terminal}/{total} Phases
          </div>
          {phases.map(p => (
            <div key={p.phase} style={{ display: 'flex', justifyContent: 'space-between', padding: '0.3rem 0', fontSize: '0.8rem' }}>
              <span>{PHASE_LABELS[p.phase as keyof typeof PHASE_LABELS] || p.phase}</span>
              <span className={`status-badge status-badge--${p.status}`}>{p.status}</span>
            </div>
          ))}
        </div>
      </PixelPanel>
    </div>
  )
}
