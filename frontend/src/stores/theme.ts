import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { THEME_PALETTES, type ResolvedTheme } from '@/theme/palette'

export type ThemeMode = 'system' | 'light' | 'dark'

export const THEME_STORAGE_KEY = 'novelvoice.theme-mode'
const DARK_QUERY = '(prefers-color-scheme: dark)'
const THEME_MODES: ThemeMode[] = ['system', 'light', 'dark']

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string' && THEME_MODES.includes(value as ThemeMode)
}

function readStoredMode(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isThemeMode(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia(DARK_QUERY).matches
  } catch {
    return false
  }
}

function applyTheme(theme: ResolvedTheme): void {
  const root = document.documentElement
  root.dataset.theme = theme
  root.style.colorScheme = theme
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', THEME_PALETTES[theme].appBg)
  }
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readStoredMode())
  const systemDark = ref(systemPrefersDark())
  const resolvedTheme = computed<ResolvedTheme>(() =>
    mode.value === 'system' ? (systemDark.value ? 'dark' : 'light') : mode.value,
  )

  if (typeof window.matchMedia === 'function') {
    const media = window.matchMedia(DARK_QUERY)
    const onChange = (event: MediaQueryListEvent) => {
      systemDark.value = event.matches
    }
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', onChange)
    } else if (typeof media.addListener === 'function') {
      // 兼容旧版 WebView。
      media.addListener(onChange)
    }
  }

  watch(
    mode,
    (value) => {
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, value)
      } catch {
        // 隐私模式下 localStorage 不可用，主题仍在本次会话生效。
      }
    },
    { immediate: false },
  )

  watch(resolvedTheme, (value) => applyTheme(value), { immediate: true })

  function setMode(value: ThemeMode): void {
    mode.value = value
  }

  return { mode, systemDark, resolvedTheme, setMode }
})