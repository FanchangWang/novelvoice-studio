<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  NAlert,
  NButton,
  NProgress,
  NSpin,
  useDialog,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type { GeneralVoicePresetOut } from '@/api/types'
import { useSystemStore } from '@/stores/system'
import { errorMessage } from '@/utils/errors'

const message = useMessage()
const dialog = useDialog()
const system = useSystemStore()
const items = ref<GeneralVoicePresetOut[]>([])
const loading = ref(false)
const generating = ref(false)
const stopping = ref(false)
const error = ref<string | null>(null)
const failures = ref<Record<string, string>>({})
const completed = ref(0)
let cancelled = false
let disposed = false

const missing = computed(() => items.value.filter((item) => !item.actor_id))
const readyCount = computed(() => items.value.filter((item) => item.actor_status === 'ready').length)
const percent = computed(() => (items.value.length ? Math.round((completed.value / items.value.length) * 100) : 0))

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const result = await api.listGeneralVoicePresets()
    if (disposed) return
    items.value = result.items
    completed.value = result.ready_count
  } catch (err) {
    if (!disposed) error.value = errorMessage(err)
  } finally {
    if (!disposed) loading.value = false
  }
}

async function generate(only?: GeneralVoicePresetOut): Promise<void> {
  if (generating.value) return
  cancelled = false
  stopping.value = false
  generating.value = true
  error.value = null
  try {
    const queue = only ? [only] : missing.value
    for (const preset of queue) {
      if (cancelled || disposed) break
      try {
        const actor = await api.generateGeneralVoicePreset(preset.preset_id)
        if (disposed) return
        preset.actor_id = actor.id
        preset.actor_status = actor.status
        delete failures.value[preset.preset_id]
        completed.value = items.value.filter((item) => item.actor_status === 'ready').length
      } catch (err) {
        if (!disposed) failures.value[preset.preset_id] = errorMessage(err)
      }
    }
    if (!disposed) {
      const failedCount = Object.keys(failures.value).length
      if (failedCount) message.warning(`${failedCount} 条通用声线生成失败，可逐条重试`)
      else if (!cancelled) message.success('通用声线已准备完成')
      else message.info('已停止后续生成，当前声线会保留')
    }
  } finally {
    if (!disposed) {
      generating.value = false
      stopping.value = false
    }
  }
}

function requestGenerate(only?: GeneralVoicePresetOut): void {
  if (system.mockMode) {
    void generate(only)
    return
  }
  dialog.warning({
    title: only ? '生成这条通用声线？' : '生成缺失的通用声线？',
    content: '将逐条调用本机声音设计服务，不会下载模型或调用云服务。真实模型生成较慢，可在两条声线之间停止；已经生成的结果会保留。',
    positiveText: '开始生成',
    negativeText: '取消',
    onPositiveClick: () => generate(only),
  })
}

onBeforeUnmount(() => {
  disposed = true
  cancelled = true
})

void load()
void system.refresh()
</script>

<template>
  <section class="nv-card rounded-xl px-5 py-4">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p class="eyebrow">GENERAL VOICES / 通用声线</p>
        <h2 class="mt-1 font-display text-xl text-fog-100">首批 36 条通用配音员</h2>
        <p class="mt-2 max-w-2xl text-[11px] leading-6 text-fog-400">
          覆盖儿童到老年、男声与女声，每个年龄段三种音色。自动选角优先使用这些声音；书籍专属声线只在没有合适通用声线时生成。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <NButton size="small" quaternary :loading="loading" :disabled="generating" @click="load">刷新</NButton>
        <NButton v-if="generating" size="small" secondary @click="stopping = true; cancelled = true">
          停止后续生成
        </NButton>
        <NButton v-else size="small" type="primary" :disabled="!missing.length" @click="requestGenerate()">
          {{ readyCount ? '生成缺失声线' : '生成全部通用声线' }}
        </NButton>
      </div>
    </div>

    <NAlert v-if="error" type="error" :bordered="false" class="mt-3">
      <div class="flex items-center justify-between gap-3"><span>{{ error }}</span><NButton size="tiny" @click="load">重试</NButton></div>
    </NAlert>

    <div v-if="generating || completed" class="mt-4">
      <div class="flex items-center justify-between text-[10px] text-fog-500">
        <span>{{ generating ? (stopping ? '正在完成当前声线，随后停止' : '正在逐条生成…') : '准备进度' }}</span>
        <span class="font-mono">{{ completed }} / {{ items.length || 36 }}</span>
      </div>
      <NProgress class="mt-1" :percentage="percent" :show-indicator="false" :processing="generating" />
    </div>

    <div v-if="loading && !items.length" class="flex justify-center py-10"><NSpin size="small" /></div>
    <div v-else class="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
      <article v-for="preset in items" :key="preset.preset_id" class="rounded-lg border border-ink-700 bg-ink-900/40 px-3 py-2.5">
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <p class="truncate text-[11px] text-fog-200">{{ preset.display_name }}</p>
            <p class="mt-1 text-[9px] text-fog-500">{{ preset.timbre_tags.slice(0, 3).join(' · ') }}</p>
          </div>
          <NButton v-if="!preset.actor_id" size="tiny" quaternary :loading="generating" @click="requestGenerate(preset)">生成</NButton>
          <span v-else class="text-[9px]" :class="preset.actor_status === 'ready' ? 'text-moss-400' : 'text-brass-300'">
            {{ preset.actor_status === 'ready' ? '已就绪' : '已存在' }}
          </span>
        </div>
        <p v-if="failures[preset.preset_id]" class="mt-1.5 text-[9px] leading-4 text-rust-400">{{ failures[preset.preset_id] }}</p>
      </article>
    </div>
  </section>
</template>
