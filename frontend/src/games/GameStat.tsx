interface GameStatProps {
  label: string
  value: string | number
}

export default function GameStat({ label, value }: GameStatProps) {
  return (
    <div className="game-stat">
      <span className="game-stat__label">{label}</span>
      <span className="game-stat__value">{value}</span>
    </div>
  )
}
