import { describe, expect, it, beforeEach, vi } from 'vitest'
import { applyTheme, readStoredThemeMode, resolveTheme, THEME_STORAGE_KEY } from './theme'

describe('theme', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  it('persists dark/light mode choice', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'light')
    expect(readStoredThemeMode()).toBe('light')
    const resolved = applyTheme('light')
    expect(resolved).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('resolves system theme from matchMedia', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: true,
      media: '',
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as MediaQueryList)
    expect(resolveTheme('system')).toBe('light')
  })

  it('applies dark theme tokens attribute', () => {
    applyTheme('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })
})
