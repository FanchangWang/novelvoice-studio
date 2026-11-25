<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  zhCN,
  type GlobalThemeOverrides,
} from 'naive-ui'

import AppShell from '@/components/AppShell.vue'

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#c99a45',
    primaryColorHover: '#dfb266',
    primaryColorPressed: '#a77c2f',
    primaryColorSuppl: '#dfb266',
    successColor: '#5f916e',
    warningColor: '#c99a45',
    errorColor: '#b96a59',
    infoColor: '#83a9c6',
    bodyColor: '#0a0b0d',
    cardColor: '#121519',
    modalColor: '#121519',
    popoverColor: '#171b20',
    tableColor: '#121519',
    inputColor: '#0e1013',
    actionColor: '#171b20',
    borderColor: '#232931',
    dividerColor: '#232931',
    textColorBase: '#e9ebef',
    textColor1: '#e9ebef',
    textColor2: '#ccd1d9',
    textColor3: '#818a97',
    textColorDisabled: '#4b535f',
    placeholderColor: '#626b78',
    fontFamily:
      '"Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
    borderRadius: '10px',
    borderRadiusSmall: '8px',
    heightMedium: '36px',
    heightLarge: '42px',
  },
  Button: {
    textColorPrimary: '#171207',
    textColorHoverPrimary: '#171207',
    textColorPressedPrimary: '#171207',
    fontWeight: '600',
    borderRadiusMedium: '8px',
  },
  Card: {
    color: '#121519',
    borderColor: '#232931',
    borderRadius: '14px',
  },
  Modal: {
    color: '#121519',
  },
  Input: {
    color: '#0e1013',
    colorFocus: '#0e1013',
    border: '1px solid #303844',
    borderHover: '1px solid #4b535f',
    borderFocus: '1px solid #c99a45',
    boxShadowFocus: '0 0 0 2px rgba(201, 154, 69, 0.16)',
  },
  Radio: {
    buttonColor: '#0e1013',
    buttonColorActive: '#c99a45',
    buttonTextColor: '#a7afbb',
    buttonTextColorActive: '#171207',
    buttonBorderColor: '#303844',
  },
  Tag: {
    borderRadius: '6px',
  },
}
</script>

<template>
  <NConfigProvider
    :theme="darkTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <NMessageProvider :max="3" placement="top-right">
      <NDialogProvider>
        <AppShell />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>