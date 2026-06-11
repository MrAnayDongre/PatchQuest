import { ButtonHTMLAttributes } from 'react'

interface PixelButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
}

export default function PixelButton({
  variant = 'primary',
  children,
  className = '',
  ...props
}: PixelButtonProps) {
  const variantClass = {
    primary: 'pixel-btn--primary',
    secondary: 'pixel-btn--secondary',
    danger: 'pixel-btn--danger',
    ghost: 'pixel-btn--ghost',
  }[variant]

  return (
    <button type="button" className={`pixel-btn ${variantClass} ${className}`.trim()} {...props}>
      {children}
    </button>
  )
}
