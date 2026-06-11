import ThemeToggle from './ThemeToggle'

interface TopNavProps {
  currentPage: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onNavigate: (page: any) => void
  backendOnline?: boolean | null
}

export default function TopNav({ currentPage, onNavigate, backendOnline }: TopNavProps) {
  const links = [
    { key: 'home', label: 'Mission' },
    { key: 'dashboard', label: 'Console' },
    { key: 'scheduler', label: 'Queue' },
    { key: 'calendar', label: 'Calendar' },
    { key: 'search', label: 'Research' },
    { key: 'memory', label: 'Archive' },
    { key: 'safety', label: 'Warden' },
    { key: 'settings', label: 'Config' },
  ]

  return (
    <nav className="top-nav">
      <div className="top-nav__brand">
        <span className="top-nav__logo" aria-hidden />
        PATCHQUEST
      </div>
      <div className="top-nav__links">
        {links.map((link) => (
          <button
            key={link.key}
            type="button"
            className={`top-nav__link ${currentPage === link.key ? 'top-nav__link--active' : ''}`}
            onClick={() => onNavigate(link.key)}
          >
            {link.label}
          </button>
        ))}
      </div>
      <div className="top-nav__right">
        <ThemeToggle compact />
        {backendOnline !== null && (
          <div className="top-nav__status">
            <span className={`top-nav__dot ${backendOnline ? '' : 'top-nav__dot--offline'}`} />
            {backendOnline ? 'Online' : 'Offline'}
          </div>
        )}
      </div>
    </nav>
  )
}
