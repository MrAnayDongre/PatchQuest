import { useEffect } from 'react'

/** Prevent arrow/space keys from scrolling the page while a game is active. */
export function useGameKeyboardLock(active: boolean) {
  useEffect(() => {
    if (!active) return
    const block = (e: KeyboardEvent) => {
      const keys = [' ', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']
      if (keys.includes(e.key)) e.preventDefault()
    }
    window.addEventListener('keydown', block, { passive: false })
    return () => window.removeEventListener('keydown', block)
  }, [active])
}
