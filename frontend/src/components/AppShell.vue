<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import StatusDot from '@/components/StatusDot.vue'
import { useSystemStore } from '@/stores/system'

const system = useSystemStore()
const route = useRoute()

const navItems = [
  { to: '/library', label: '书库', hint: '导入与制作台', icon: 'library' as const },
  { to: '/voices', label: '声音库', hint: '音色演员', icon: 'voices' as const },
  { to: '/inbox', label: '异常收件箱', hint: '集中处理异常', icon: 'inbox' as const },
  { to: '/jobs', label: '任务队列', hint: '生成与重试', icon: 'jobs' as const },
  { to: '/settings', label: '设置', hint: '状态与配置', icon: 'settings' as const },
]

function isActive(path: string): boolean {
  if (path === '/library') {
    return (
      route.path === '/library' ||
      route.path === '/trash' ||
      route.path.startsWith('/books/')
    )
  }
  return route.path === path
}

function providerLabel(): string {
  if (system.error && !system.status) {
    return '后端未连接'
  }
  if (system.mockMode === null) {
    return '状态检测中'
  }
  return system.mockMode ? '模拟模式' : 'Ollama + 本地语音'
}

function providerTone(): 'ok' | 'warn' | 'error' | 'idle' {
  if (system.error && !system.status) {
    return 'error'
  }
  if (system.mockMode === null) {
    return 'idle'
  }
  return system.mockMode ? 'warn' : 'ok'
}

function providerHint(): string {
  if (system.error && !system.status) {
    return '请确认后端已启动'
  }
  if (system.mockMode === null) {
    return '正在读取 Provider 状态'
  }
  return system.mockMode ? '不加载模型，返回确定性结果' : 'Ollama + Qwen3-TTS / CosyVoice3'
}

onMounted(() => {
  void system.refresh()
})
</script>

<template>
  <div class="flex h-full min-h-0 bg-ink-950 text-fog-200">
    <aside
      class="relative z-20 flex w-64 shrink-0 flex-col border-r border-ink-700/80 bg-ink-900/75 backdrop-blur"
    >
      <div class="flex items-center gap-3 px-5 pb-5 pt-6">
        <div
          class="nv-brand-mark grid h-10 w-10 place-items-center rounded-lg border border-brass-600/60 bg-brass-500/10 font-display text-lg text-brass-300"
        >
          声
        </div>
        <div class="min-w-0">
          <p class="font-display text-lg tracking-wide text-fog-100">
            NovelVoice
          </p>
          <p class="mt-0.5 text-xs font-semibold uppercase tracking-[0.34em] text-fog-500">
            Studio
          </p>
        </div>
      </div>

      <div class="mx-5 h-px bg-gradient-to-r from-brass-600/40 via-ink-700 to-transparent" />

      <nav class="mt-5 flex flex-1 flex-col gap-1 px-3">
        <p class="eyebrow px-3 pb-2">Workbench</p>
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="group relative flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors duration-150"
          :class="
            isActive(item.to)
              ? 'bg-ink-800 text-fog-100'
              : 'text-fog-400 hover:bg-ink-850 hover:text-fog-200'
          "
        >
          <span
            v-if="isActive(item.to)"
            class="absolute bottom-2 left-0 top-2 w-[2px] rounded-full bg-brass-400"
          />
          <AppIcon
            :name="item.icon"
            :size="19"
            :class="isActive(item.to) ? 'text-brass-300' : 'text-fog-500 group-hover:text-fog-300'"
          />
          <span class="flex min-w-0 flex-col">
            <span class="text-sm font-medium leading-5">{{ item.label }}</span>
            <span class="text-xs leading-5 text-fog-500">{{ item.hint }}</span>
          </span>
        </RouterLink>
      </nav>

      <div class="px-4 pb-5">
        <RouterLink
          to="/settings"
          class="block rounded-xl border border-ink-700/90 bg-ink-850/70 px-3.5 py-3 transition-colors hover:border-ink-600"
        >
          <div class="flex items-center gap-2">
            <StatusDot
              :status="providerTone()"
              :pulse="providerTone() === 'warn'"
              size="sm"
            />
            <span class="text-xs font-medium text-fog-200">{{ providerLabel() }}</span>
            <span
              v-if="system.mockMode"
              class="ml-auto rounded border border-brass-600/30 bg-brass-500/10 px-1.5 py-0.5 text-xs tracking-wide text-brass-300"
            >
              MOCK
            </span>
          </div>
          <p class="mt-1.5 text-xs leading-5 text-fog-500">
            {{ providerHint() }}
          </p>
          <p v-if="system.error && !system.status" class="mt-1 text-xs leading-5 text-rust-400">
            {{ system.error }}
          </p>
        </RouterLink>

        <div class="mt-3 flex items-center justify-between px-1 text-xs text-fog-600">
          <span>v{{ system.version }}</span>
          <a
            class="transition-colors hover:text-fog-300"
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
          >
            API 文档
          </a>
        </div>
      </div>
    </aside>

    <main class="relative flex min-w-0 flex-1 flex-col overflow-hidden">
      <div class="pointer-events-none absolute inset-0 nv-grid opacity-[0.22]" />
      <div class="relative z-10 flex min-h-0 flex-1 flex-col">
        <RouterView />
      </div>
    </main>
  </div>
</template>