interface GameControlsHintProps {
  text: string
}

export default function GameControlsHint({ text }: GameControlsHintProps) {
  return <p className="game-controls-hint">{text}</p>
}
