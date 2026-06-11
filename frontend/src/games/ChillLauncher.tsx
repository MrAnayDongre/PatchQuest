import GameShell from './GameShell'

interface ChillLauncherProps {
  onExit: () => void
}

const LINKS = [
  { href: 'https://www.youtube.com', label: 'YouTube', tone: 'red' },
  { href: 'https://www.reddit.com', label: 'Reddit', tone: 'orange' },
]

export default function ChillLauncher({ onExit }: ChillLauncherProps) {
  return (
    <GameShell
      title="Chill Zone"
      description="Take a break while PatchQuest orchestrates your mission."
      state="running"
      ambient
      onRestart={onExit}
      onExit={onExit}
    >
      <div className="game-chill">
        <p className="game-chill__note">External links open in a new tab only when clicked.</p>
        <div className="game-chill__links">
          {LINKS.map(link => (
            <a
              key={link.href}
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className={`game-chill__link game-chill__link--${link.tone}`}
            >
              {link.label} ↗
            </a>
          ))}
        </div>
      </div>
    </GameShell>
  )
}
