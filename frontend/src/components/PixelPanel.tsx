import { CSSProperties, ReactNode } from 'react'

interface PixelPanelProps {
  title?: string
  glow?: boolean
  children: ReactNode
  className?: string
  style?: CSSProperties
}

export default function PixelPanel({ title, glow, children, className = '', style }: PixelPanelProps) {
  return (
    <div className={`pixel-panel ${glow ? 'pixel-panel--glow' : ''} ${className}`} style={style}>
      {title && <div className="section-title">{title}</div>}
      {children}
    </div>
  )
}
