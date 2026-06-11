interface XCloseButtonProps {
  onClick: () => void
}

export default function XCloseButton({ onClick }: XCloseButtonProps) {
  return (
    <button className="game-overlay__close" onClick={onClick} aria-label="Close overlay">
      ✕
    </button>
  )
}
