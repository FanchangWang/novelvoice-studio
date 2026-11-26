import type { GlobalThemeOverrides } from 'naive-ui'

export type ResolvedTheme = 'light' | 'dark'

export interface ThemePalette {
  appBg: string
  surface1: string
  surface2: string
  surface3: string
  surfaceHover: string
  textPrimary: string
  textSecondary: string
  textMuted: string
  border: string
  borderStrong: string
  accent: string
  accentHover: string
  accentPressed: string
  onAccent: string
  focusRing: string
  success: string
  warning: string
  danger: string
  info: string
  scrollbarThumb: string
  scrollbarThumbHover: string
}

// 与 src/styles.css 的 CSS variables 保持一致；Naive UI 需要具体色值才能推导状态色。
export const THEME_PALETTES: Record<ResolvedTheme, ThemePalette> = {
  light: {
    appBg: '#f5f6f8',
    surface1: '#ffffff',
    surface2: '#edf0f3',
    surface3: '#e4e9ef',
    surfaceHover: '#e9edf2',
    textPrimary: '#171b22',
    textSecondary: '#3f4b59',
    textMuted: '#596676',
    border: '#d4dae1',
    borderStrong: '#7f8c9a',
    accent: '#80520e',
    accentHover: '#96641a',
    accentPressed: '#6b440b',
    onAccent: '#ffffff',
    focusRing: '#80520e',
    success: '#2f6b43',
    warning: '#8a5a12',
    danger: '#a33f2d',
    info: '#25688f',
    scrollbarThumb: '#b8c2cd',
    scrollbarThumbHover: '#98a5b3',
  },
  dark: {
    appBg: '#171a1f',
    surface1: '#20252c',
    surface2: '#292f38',
    surface3: '#323a45',
    surfaceHover: '#2b323b',
    textPrimary: '#f2f4f7',
    textSecondary: '#c8d0da',
    textMuted: '#aeb9c6',
    border: '#3c4652',
    borderStrong: '#6a7886',
    accent: '#e7b85c',
    accentHover: '#f0c97a',
    accentPressed: '#d2a246',
    onAccent: '#1a1305',
    focusRing: '#e7b85c',
    success: '#86b593',
    warning: '#e7b85c',
    danger: '#d98d7c',
    info: '#83a9c6',
    scrollbarThumb: '#4e5b69',
    scrollbarThumbHover: '#637183',
  },
}

const fontFamily =
  '"Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'

function mixHex(color: string, ratio: number, base: string): string {
  const parse = (value: string): [number, number, number] => {
    const hex = value.replace('#', '')
    return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)]
  }
  const [r1, g1, b1] = parse(color)
  const [r2, g2, b2] = parse(base)
  const channel = (a: number, b: number): string =>
    Math.round(a * ratio + b * (1 - ratio))
      .toString(16)
      .padStart(2, '0')
  return `#${channel(r1, r2)}${channel(g1, g2)}${channel(b1, b2)}`
}

export function buildThemeOverrides(theme: ResolvedTheme): GlobalThemeOverrides {
  const palette = THEME_PALETTES[theme]
  // Alert 的浅色底由 baseColor 混合而来；固定用卡片色，避免深色主题下黑底叠加出糊色。
  const softBase = palette.surface1
  const soft = (color: string, ratio: number): string => mixHex(color, ratio, softBase)

  return {
    common: {
      fontFamily,
      fontSizeTiny: '14px',
      fontSizeSmall: '14px',
      fontSizeMedium: '15px',
      fontSizeLarge: '16px',
      fontSizeHuge: '18px',
      heightTiny: '32px',
      heightSmall: '36px',
      heightMedium: '40px',
      heightLarge: '44px',
      heightHuge: '48px',
      borderRadius: '10px',
      borderRadiusSmall: '8px',
      primaryColor: palette.accent,
      primaryColorHover: palette.accentHover,
      primaryColorPressed: palette.accentPressed,
      primaryColorSuppl: palette.accentHover,
      successColor: palette.success,
      warningColor: palette.warning,
      errorColor: palette.danger,
      infoColor: palette.info,
      bodyColor: palette.appBg,
      cardColor: palette.surface1,
      modalColor: palette.surface1,
      popoverColor: palette.surface2,
      tableColor: palette.surface1,
      inputColor: palette.surface1,
      actionColor: palette.surface2,
      borderColor: palette.border,
      dividerColor: palette.border,
      textColorBase: palette.textPrimary,
      textColor1: palette.textPrimary,
      textColor2: palette.textSecondary,
      textColor3: palette.textMuted,
      textColorDisabled: palette.textMuted,
      placeholderColor: palette.textMuted,
      closeIconColor: palette.textMuted,
      closeIconColorHover: palette.textPrimary,
      scrollbarColor: palette.scrollbarThumb,
      scrollbarColorHover: palette.scrollbarThumbHover,
      scrollbarWidth: '8px',
    },
    Button: {
      textColorPrimary: palette.onAccent,
      textColorHoverPrimary: palette.onAccent,
      textColorPressedPrimary: palette.onAccent,
      textColorFocusPrimary: palette.onAccent,
      fontWeight: '600',
      borderRadiusMedium: '8px',
      borderRadiusSmall: '7px',
      borderRadiusTiny: '6px',
    },
    Card: {
      color: palette.surface1,
      borderColor: palette.border,
      borderRadius: '14px',
    },
    Modal: {
      color: palette.surface1,
    },
    Dialog: {
      color: palette.surface1,
    },
    Popover: {
      color: palette.surface2,
    },
    Input: {
      color: palette.surface1,
      colorFocus: palette.surface1,
      border: `1px solid ${palette.borderStrong}`,
      borderHover: `1px solid ${palette.textMuted}`,
      borderFocus: `1px solid ${palette.accent}`,
      boxShadowFocus: `0 0 0 2px ${palette.focusRing}33`,
      caretColor: palette.accent,
      textColor: palette.textPrimary,
      placeholderColor: palette.textMuted,
    },
    Select: {
      peers: {
        InternalSelection: {
          color: palette.surface1,
          colorActive: palette.surface1,
          textColor: palette.textPrimary,
          placeholderColor: palette.textMuted,
          border: `1px solid ${palette.borderStrong}`,
          borderHover: `1px solid ${palette.textMuted}`,
          borderActive: `1px solid ${palette.accent}`,
          borderFocus: `1px solid ${palette.accent}`,
          boxShadowActive: `0 0 0 2px ${palette.focusRing}33`,
          boxShadowFocus: `0 0 0 2px ${palette.focusRing}33`,
          arrowColor: palette.textMuted,
        },
      },
    },
    Radio: {
      buttonColor: palette.surface1,
      buttonColorActive: palette.accent,
      buttonTextColor: palette.textSecondary,
      buttonTextColorActive: palette.onAccent,
      buttonBorderColor: palette.borderStrong,
    },
    // 复选框架只有 14px，继承 10px 圆角会变成圆形，和单选框混淆。
    Checkbox: {
      borderRadius: '4px',
    },
    Tag: {
      borderRadius: '6px',
      // 浅色下 warning 标签的琥珀字压在 15% 同色底上只有 4.45:1，压暗一档。
      textColorWarning: theme === 'light' ? '#6d460c' : palette.warning,
    },
    Tooltip: {
      color: palette.surface3,
      textColor: palette.textPrimary,
    },
    Alert: {
      color: palette.surface2,
      border: `1px solid ${palette.border}`,
      titleTextColor: palette.textPrimary,
      contentTextColor: palette.textSecondary,
      iconColor: palette.textMuted,
      colorInfo: soft(palette.info, 0.09),
      borderInfo: `1px solid ${soft(palette.info, 0.3)}`,
      titleTextColorInfo: palette.textPrimary,
      contentTextColorInfo: palette.textSecondary,
      iconColorInfo: palette.info,
      colorSuccess: soft(palette.success, 0.09),
      borderSuccess: `1px solid ${soft(palette.success, 0.3)}`,
      titleTextColorSuccess: palette.textPrimary,
      contentTextColorSuccess: palette.textSecondary,
      iconColorSuccess: palette.success,
      colorWarning: soft(palette.warning, 0.1),
      borderWarning: `1px solid ${soft(palette.warning, 0.32)}`,
      titleTextColorWarning: palette.textPrimary,
      contentTextColorWarning: palette.textSecondary,
      iconColorWarning: palette.warning,
      colorError: soft(palette.danger, 0.09),
      borderError: `1px solid ${soft(palette.danger, 0.3)}`,
      titleTextColorError: palette.textPrimary,
      contentTextColorError: palette.textSecondary,
      iconColorError: palette.danger,
    },
  }
}