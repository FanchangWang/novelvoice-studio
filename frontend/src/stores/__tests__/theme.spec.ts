import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { THEME_STORAGE_KEY, useThemeStore } from '@/stores/theme'

type MediaListener = (event: MediaQueryListEvent) => void

let systemDark = false
let mediaListeners: MediaListener[] = []

function installMatchMedia(): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => {
      const media = {
        matches: query === '(prefers-color-scheme: dark)' ? systemDark : false,
        media: query,
        onchange: null,
        addEventListener: (_type: string, listener: MediaListener) => {
          if (!mediaListeners.includes(listener)) {
            mediaListeners.push(listener)
          }
        },
        removeEventListener: (_type: string, listener: MediaListener) => {
          mediaListeners = mediaListeners.filter((item) => item !== listener)
        },
        addListener: (listener: MediaListener) => {
          if (!mediaListeners.includes(listener)) {
            mediaListeners.push(listener)
          }
        },
        removeListener: (listener: MediaListener) => {
          mediaListeners = mediaListeners.filter((item) => item !== listener)
        },
        dispatchEvent: () => false,
      }
      return media as unknown as MediaQueryList
    }),
  )
}

function emitSystemTheme(matches: boolean): void {
  systemDark = matches
  for (const listener of mediaListeners) {
    listener({ matches } as MediaQueryListEvent)
  }
}

beforeEach(() => {
  systemDark = false
  mediaListeners = []
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.style.colorScheme = ''
  installMatchMedia()
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('theme store', () => {
  it('defaults to system and renders the operating-system theme', () => {
    const theme = useThemeStore()

    expect(theme.mode).toBe('system')
    expect(theme.resolvedTheme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.style.colorScheme).toBe('light')
  })

  it('restores a saved dark preference before rendering', () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark')

    const theme = useThemeStore()

    expect(theme.mode).toBe('dark')
    expect(theme.resolvedTheme).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.style.colorScheme).toBe('dark')
  })

  it('persists explicit modes and ignores operating-system changes', async () => {
    const theme = useThemeStore()
    theme.setMode('light')
    await nextTick()

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')

    emitSystemTheme(true)
    await nextTick()

    expect(theme.systemDark).toBe(true)
    expect(theme.resolvedTheme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('follows operating-system changes in system mode', async () => {
    const theme = useThemeStore()
    expect(theme.resolvedTheme).toBe('light')

    emitSystemTheme(true)
    await nextTick()

    expect(theme.mode).toBe('system')
    expect(theme.resolvedTheme).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')

    emitSystemTheme(false)
    await nextTick()

    expect(theme.resolvedTheme).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})