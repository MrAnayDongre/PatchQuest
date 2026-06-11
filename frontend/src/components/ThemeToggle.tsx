import { useTheme } from '../theme/ThemeProvider'
import type { ThemeMode } from '../theme/theme'

interface ThemeToggleProps {
  compact?: boolean
}

const MODES: { id: ThemeMode; label: string }[] = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'system', label: 'System' },
]

export default function ThemeToggle({ compact = false }: ThemeToggleProps) {
  const { mode, resolved, setMode } = useTheme()

  return (
    <div className={`theme-toggle ${compact ? 'theme-toggle--compact' : ''}`} role="group" aria-label="Theme mode">
      {!compact && <span className="theme-toggle__label">Theme</span>}
      <div className="theme-toggle__options">
        {MODES.map(m => (
          <button
            key={m.id}
            type="button"
            className={`theme-toggle__btn ${mode === m.id ? 'theme-toggle__btn--active' : ''}`}
            onClick={() => setMode(m.id)}
            aria-pressed={mode === m.id}
            title={`${m.label} mode${m.id === 'system' ? ` (${resolved})` : ''}`}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  )
}
