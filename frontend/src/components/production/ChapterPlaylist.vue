<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NTag } from 'naive-ui'

import { api } from '@/api/client'
import type { ScriptUnitOut } from '@/api/types'
import { AUDIO_STATUS_LABELS, UNIT_TYPE_LABELS } from '@/utils/production'

const props = defineProps<{ units: ScriptUnitOut[] }>()
const audio = ref<HTMLAudioElement | null>(null)
const playing = ref(false)
const currentId = ref<string | null>(null)
const playbackError = ref<string | null>(null)
const TTS_TYPES = new Set(['narration', 'dialogue', 'inner_monologue'])
let pauseTimer: ReturnType<typeof setTimeout> | null = null
let generation = 0

const ttsUnits = computed(() => props.units.filter((unit) => TTS_TYPES.has(unit.unit_type)))
const playableUnits = computed(() =>
  ttsUnits.value.filter(
    (unit) => ['ready', 'stale'].includes(unit.audio_status) && Boolean(unit.current_audio_asset_id),
  ),
)
const missingUnits = computed(() => ttsUnits.value.length - playableUnits.value.length)
const staleUnits = computed(() =>
  playableUnits.value.filter((unit) => unit.audio_status === 'stale'),
)
const currentUnit = computed(
  () => playableUnits.value.find((unit) => unit.id === currentId.value) ?? null,
)
const completedCount = computed(() => {
  const index = playableUnits.value.findIndex((unit) => unit.id === currentId.value)
  return Math.max(0, index)
})

function clearPause(): void {
  if (pauseTimer) clearTimeout(pauseTimer)
  pauseTimer = null
}

function stop(): void {
  generation += 1
  clearPause()
  playing.value = false
  currentId.value = null
  playbackError.value = null
  if (audio.value) {
    audio.value.pause()
    audio.value.removeAttribute('src')
    audio.value.load()
  }
}

function scheduleAdvance(run: number, delay: number): void {
  clearPause()
  pauseTimer = setTimeout(() => {
    if (run !== generation || !playing.value) return
    const index = playableUnits.value.findIndex((unit) => unit.id === currentId.value)
    const next = playableUnits.value[index + 1]
    if (!next) {
      stop()
      return
    }
    currentId.value = next.id
    void playCurrent(run)
  }, Math.max(0, Math.min(delay, 30_000)))
}

async function playCurrent(run: number): Promise<void> {
  const unit = currentUnit.value
  if (!unit || !audio.value) {
    stop()
    return
  }
  audio.value.src = api.scriptUnitAudioUrl(unit.id, unit.current_audio_asset_id)
  audio.value.load()
  try {
    await audio.value.play()
    if (run !== generation) audio.value.pause()
  } catch {
    if (run === generation) {
      playing.value = false
      playbackError.value = '浏览器未能播放这段音频，请检查单元音频后重试。'
    }
  }
}

async function play(): Promise<void> {
  if (!playableUnits.value.length || playing.value) return
  generation += 1
  const run = generation
  playing.value = true
  playbackError.value = null
  currentId.value = playableUnits.value[0].id
  await playCurrent(run)
}

function onAudioError(): void {
  if (playing.value && currentId.value) {
    playing.value = false
    playbackError.value = '音频加载失败，请检查该单元的生成状态。'
  }
}

function onEnded(): void {
  const next = playableUnits.value.findIndex((unit) => unit.id === currentId.value) + 1
  const unit = currentUnit.value
  const delay =
    (unit?.pause_after_ms ?? 0) + (playableUnits.value[next]?.pause_before_ms ?? 0)
  scheduleAdvance(generation, delay)
}

function toggle(): void {
  if (!playing.value) {
    void play()
    return
  }
  if (audio.value?.paused) {
    void audio.value.play().catch(() => {
      playbackError.value = '无法继续播放，请重新从头试听。'
    })
  } else {
    audio.value?.pause()
  }
}

watch(
  () => props.units,
  stop,
)

onBeforeUnmount(stop)
</script>

<template>
  <section class="rounded-xl border border-ink-700 bg-ink-900/40 px-4 py-3">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-display text-sm text-fog-100">整章顺序试听</h3>
          <NTag size="tiny" :bordered="false" :type="missingUnits ? 'warning' : 'success'">
            {{ playableUnits.length }} / {{ ttsUnits.length }} 段可播放
          </NTag>
        </div>
        <p class="mt-1 text-[10px] leading-5 text-fog-500">
          按剧本顺序播放已有语音，并保留单元前后停顿。音效、环境与停顿 cue 目前只做标记，不合成音效。
        </p>
      </div>
      <div class="flex items-center gap-2">
        <NButton size="tiny" type="primary" :disabled="!playableUnits.length" @click="toggle">
          {{ playing ? (audio?.paused ? '继续' : '暂停') : '从头试听' }}
        </NButton>
        <NButton size="tiny" quaternary :disabled="!playing && !currentId" @click="stop">停止</NButton>
      </div>
    </div>
    <NAlert v-if="missingUnits" type="warning" :bordered="false" class="mt-2">
      有 {{ missingUnits }} 段尚未生成可用音频，试听会跳过这些段，不属于完整整章试听。
    </NAlert>
    <NAlert v-if="staleUnits.length" type="warning" :bordered="false" class="mt-2">
      {{ staleUnits.length }} 段使用旧输入生成的音频，建议重新生成后再做最终试听。
    </NAlert>
    <NAlert v-if="playbackError" type="error" :bordered="false" class="mt-2">
      {{ playbackError }}
    </NAlert>
    <div v-if="currentUnit" class="mt-2 text-[10px] text-fog-400">
      当前第 {{ completedCount + 1 }} / {{ playableUnits.length }} 段 ·
      {{ UNIT_TYPE_LABELS[currentUnit.unit_type] ?? currentUnit.unit_type }} ·
      {{ currentUnit.speaker_name || '未知说话人' }} ·
      音频 {{ AUDIO_STATUS_LABELS[currentUnit.audio_status] ?? currentUnit.audio_status }}
    </div>
    <audio
      ref="audio"
      preload="none"
      class="mt-2 h-8 w-full"
      @ended="onEnded"
      @error="onAudioError"
    />
  </section>
</template>
