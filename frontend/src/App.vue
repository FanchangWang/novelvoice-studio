<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  lightTheme,
  zhCN,
} from 'naive-ui'
import { computed } from 'vue'

import AppShell from '@/components/AppShell.vue'
import { useThemeStore } from '@/stores/theme'
import { buildThemeOverrides } from '@/theme/palette'

const theme = useThemeStore()

const naiveTheme = computed(() =>
  theme.resolvedTheme === 'dark' ? darkTheme : lightTheme,
)
const themeOverrides = computed(() => buildThemeOverrides(theme.resolvedTheme))
</script>

<template>
  <NConfigProvider
    :theme="naiveTheme"
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