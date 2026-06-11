import type { ButtonHTMLAttributes } from 'react'

interface GameButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}

export default function GameButton({
  variant = 'primary',
  size = 'md',
  className = '',
  children,
  ...props
}: GameButtonProps) {
  return (
    <button
      type="button"
      className={`game-btn game-btn--${variant} game-btn--${size} ${className}`.trim()}
      {...props}
    >
      {children}
    </button>
  )
}
