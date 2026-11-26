<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type { ProductionChapterOut, SceneStateResponse } from '@/api/types'
import { useProductionStore } from '@/stores/production'
import { isJobActive } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import {
  AGE_LABELS,
  EMOTION_LABELS,
  labelOf,
  percent,
} from '@/utils/analysis'
import {
  PRESENCE_LABELS,
  REVIEW_SEVERITY_LABELS,
  STAGE_STATUS_LABELS,
  TIME_MODE_LABELS,
  jobStatusLabel,
  jobTypeLabel,
  severityTagType,
  statusTagType,
} from '@/utils/production'

const props = defineProps<{
  bookId: string
  chapters: ProductionChapterOut[]
  chapterId: string | null
  bibleReady: boolean
}>()

const emit = defineEmits<{
  (event: 'update:chapterId', value: string): void
  (event: 'changed'): void
  (event: 'open-reviews'): void
}>()

const production = useProductionStore()
const message = useMessage()

const state = ref<SceneStateResponse | null>(null)
const loading = ref(false)
const actionLoading = ref(false)
const error = ref<string | null>(null)

let pollTimer: ReturnType<typeof setTimeout> | null = null
let lastJobActive = false
let loadToken = 0

const chapterOptions = computed(() =>
  props.chapters.map((chapter) => ({
    label: `${String(chapter.sequence).padStart(2, '0')} · ${chapter.title} · ${
      STAGE_STATUS_LABELS[chapter.scene_status] ?? chapter.scene_status
    }`,
    value: chapter.id,
  })),
)

const selectedChapter = computed(
  () => props.chapters.find((chapter) => chapter.id === props.chapterId) ?? null,
)
const job = computed(() => state.value?.job ?? null)
const jobActive = computed(() => isJobActive(job.value?.status))
const scenes = computed(() => state.value?.scenes ?? [])

function stopPolling(): void {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll(): void {
  if (!jobActive.value || pollTimer) {
    return
  }
  pollTimer = setTimeout(async () => {
    pollTimer = null
    await load(true).catch(() => undefined)
    schedulePoll()
  }, 1800)
}

async function load(silent = false): Promise<void> {
  const chapterId = props.chapterId
  if (!chapterId) {
    state.value = null
    return
  }
  const token = ++loadToken
  if (!silent) {
    loading.value = true
  }
  error.value = null
  try {
    const response = await api.getScenes(chapterId)
    if (token !== loadToken) {
      return
    }
    state.value = response
    const active = isJobActive(response.job?.status)
    if (active) {
      schedulePoll()
    } else {
      stopPolling()
      if (lastJobActive) {
        emit('changed')
        await production.refresh()
      } else if (response.job?.status === 'failed' && response.job.error_message) {
        error.value = response.job.error_message
      }
    }
    lastJobActive = active
  } catch (err) {
    if (token === loadToken) {
      error.value = errorMessage(err)
    }
    throw err
  } finally {
    if (token === loadToken) {
      loading.value = false
    }
  }
}

async function start(): Promise<void> {
  const chapterId = props.chapterId
  if (!chapterId) {
    return
  }
  actionLoading.value = true
  error.value = null
  try {
    await api.startScenes(chapterId)
    message.success('已开始生成章节分场')
    await load(true)
    await production.refresh()
  } catch (err) {
    error.value = errorMessage(err)
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

async function cancel(): Promise<void> {
  const chapterId = props.chapterId
  if (!chapterId) {
    return
  }
  actionLoading.value = true
  try {
    await api.cancelScenes(chapterId)
    message.success('已请求取消分场任务')
    await load(true)
    await production.refresh()
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function statusLabel(status: string): string {
  return STAGE_STATUS_LABELS[status] ?? status
}

watch(
  () => props.chapterId,
  () => {
    stopPolling()
    lastJobActive = false
    state.value = null
    if (props.chapterId) {
      void load().catch(() => undefined)
    }
  },
)

watch(
  () => props.chapters,
  (chapters) => {
    if (!props.chapterId && chapters.length) {
      emit('update:chapterId', chapters[0].id)
    }
  },
  { immediate: true },
)

onMounted(() => {
  if (props.chapterId) {
    void load().catch(() => undefined)
  }
})

onBeforeUnmount(() => {
  stopPolling()
  loadToken += 1
})
</script>

<template>
  <div class="space-y-4">
    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="eyebrow">SCENES / 分场</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <h2 class="font-display text-xl text-fog-100">场景与角色状态</h2>
            <NTag
              v-if="state"
              size="small"
              :bordered="false"
              :type="statusTagType(state.scene_status)"
            >
              {{ statusLabel(state.scene_status) }}
            </NTag>
            <NTag v-if="state?.scene_version" size="small" :bordered="false">
              v{{ state.scene_version }} · {{ scenes.length }} 场
            </NTag>
            <NTag v-if="state?.open_reviews" size="small" :bordered="false" type="warning">
              异常 {{ state.open_reviews }}
            </NTag>
          </div>
          <p class="mt-2 max-w-2xl text-[11px] leading-5 text-fog-500">
            分场按原文顺序切分场景，并记录每个角色在场景中的年龄阶段、情绪、身体与认知状态。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <NSelect
            :value="chapterId"
            :options="chapterOptions"
            size="small"
            class="w-64"
            placeholder="选择章节"
            :disabled="!chapterOptions.length"
            @update:value="emit('update:chapterId', $event)"
          />
          <NButton size="small" quaternary :loading="loading" @click="load()">刷新</NButton>
          <NButton
            v-if="jobActive"
            size="small"
            secondary
            :loading="actionLoading"
            @click="cancel"
          >
            取消
          </NButton>
          <NButton
            v-else
            size="small"
            type="primary"
            :disabled="!chapterId || !bibleReady"
            :loading="actionLoading"
            @click="start"
          >
            {{ scenes.length ? '重新分场' : '生成分场' }}
          </NButton>
        </div>
      </div>

      <p v-if="!bibleReady" class="mt-3 rounded-lg border border-brass-600/25 bg-brass-500/[0.05] px-3 py-2 text-[10px] text-brass-300">
        先完成并确认全书设定集，才能生成分场。
      </p>

      <div v-if="job" class="mt-3 rounded-lg border border-ink-700 bg-ink-900/50 px-3.5 py-2.5">
        <div class="flex flex-wrap items-center gap-3">
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="jobActive ? 'animate-pulse bg-brass-400' : job.status === 'failed' ? 'bg-rust-500' : 'bg-moss-400'"
          />
          <span class="text-[11px] text-fog-300">
            {{ jobTypeLabel(job.job_type) }} · {{ jobStatusLabel(job.status) }} ·
            {{ Math.round((job.progress || 0) * 100) }}%
          </span>
          <span class="text-[10px] text-fog-500">{{ job.completed_items }} / {{ job.total_items }}</span>
          <span v-if="job.error_message" class="text-[10px] text-rust-400">{{ job.error_message }}</span>
        </div>
        <div v-if="jobActive" class="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
          <div
            class="h-full rounded-full bg-brass-400 transition-all"
            :style="{ width: `${Math.round((job.progress || 0) * 100)}%` }"
          />
        </div>
      </div>

      <NAlert v-if="error" type="error" :bordered="false" class="mt-3">{{ error }}</NAlert>
    </section>

    <div v-if="!chapterId" class="rounded-xl border border-dashed border-ink-700 px-6 py-16 text-center text-[11px] text-fog-500">
      本书还没有章节。
    </div>

    <div v-else-if="loading && !state" class="flex items-center justify-center py-24 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取分场…
    </div>

    <div
      v-else-if="!scenes.length"
      class="flex min-h-[280px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
    >
      <h3 class="font-display text-lg text-fog-200">本章还没有分场</h3>
      <p class="mt-2 max-w-md text-[11px] leading-6 text-fog-500">
        生成后会得到场景切分、地点、时间、氛围与角色状态快照。重跑会生成新版本，旧版本保留。
      </p>
      <NButton
        class="mt-4"
        type="primary"
        :disabled="!bibleReady"
        :loading="actionLoading"
        @click="start"
      >
        生成本章分场
      </NButton>
    </div>

    <template v-else>
      <div class="flex items-center justify-between">
        <div>
          <h3 class="font-display text-base text-fog-100">
            {{ selectedChapter?.title ?? '章节' }}
          </h3>
          <p class="mt-0.5 text-[10px] text-fog-500">
            {{ scenes.length }} 个场景 · 版本 v{{ state?.scene_version }}
          </p>
        </div>
        <NButton size="tiny" quaternary @click="emit('open-reviews')">查看本章异常</NButton>
      </div>

      <div class="space-y-3">
        <article
          v-for="scene in scenes"
          :key="scene.id"
          class="nv-card rounded-xl px-4 py-3.5"
        >
          <div class="flex flex-wrap items-start gap-3">
            <div class="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-ink-600 bg-ink-800 font-mono text-[11px] text-brass-300">
              {{ String(scene.scene_index + 1).padStart(2, '0') }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-1.5">
                <h4 class="font-display text-base text-fog-100">
                  {{ scene.title || `场景 ${scene.scene_index + 1}` }}
                </h4>
                <NTag v-if="scene.location" size="tiny" :bordered="false">
                  {{ scene.location }}
                </NTag>
                <NTag size="tiny" :bordered="false" type="info">
                  {{ TIME_MODE_LABELS[scene.time_mode] ?? scene.time_mode }}
                </NTag>
                <NTag v-if="scene.mood" size="tiny" :bordered="false" type="warning">
                  {{ scene.mood }}
                </NTag>
                <span v-if="scene.story_time_label" class="text-[10px] text-fog-500">
                  {{ scene.story_time_label }}
                </span>
                <span class="ml-auto font-mono text-[9px] text-fog-600">
                  {{ percent(scene.confidence) }}
                </span>
              </div>
              <p v-if="scene.summary" class="mt-1.5 text-[11px] leading-5 text-fog-400">
                {{ scene.summary }}
              </p>

              <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[9px] text-fog-600">
                <span>原文 {{ scene.source_start_offset }}–{{ scene.source_end_offset }}</span>
                <span v-if="scene.pov_character_name">视角：{{ scene.pov_character_name }}</span>
                <span>{{ STAGE_STATUS_LABELS[scene.analysis_status] ?? scene.analysis_status }}</span>
              </div>

              <div v-if="scene.characters.length" class="mt-2.5 grid gap-1.5 xl:grid-cols-2">
                <div
                  v-for="character in scene.characters"
                  :key="character.id"
                  class="rounded-lg border border-ink-700/70 bg-ink-900/50 px-2.5 py-2"
                >
                  <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                    <span class="text-fog-200">{{ character.character_name ?? character.character_id }}</span>
                    <span class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-fog-400">
                      {{ PRESENCE_LABELS[character.presence] ?? character.presence }}
                    </span>
                    <span class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-lake-400">
                      {{ labelOf(AGE_LABELS, character.age_stage) }}
                    </span>
                    <span class="ml-auto font-mono text-[9px] text-fog-600">
                      {{ percent(character.confidence) }}
                    </span>
                  </div>
                  <div class="mt-1 space-y-0.5 text-[9px] leading-4 text-fog-500">
                    <p v-if="character.emotional_state">
                      情绪：{{ EMOTION_LABELS[character.emotional_state] ?? character.emotional_state }}
                    </p>
                    <p v-if="character.physical_state">身体：{{ character.physical_state }}</p>
                    <p v-if="character.relationship_state">关系：{{ character.relationship_state }}</p>
                    <p v-if="character.knowledge_state">认知：{{ character.knowledge_state }}</p>
                    <p v-if="character.disguise_state">伪装：{{ character.disguise_state }}</p>
                    <p v-if="character.voice_note" class="text-brass-300/80">
                      声音：{{ character.voice_note }}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </article>
      </div>

      <section
        v-if="state?.review_items.length"
        class="nv-card rounded-xl px-4 py-3.5"
      >
        <div class="flex items-center justify-between">
          <h3 class="font-display text-base text-fog-100">本章待处理异常</h3>
          <NButton size="tiny" quaternary @click="emit('open-reviews')">去收件箱</NButton>
        </div>
        <div class="mt-2 space-y-1.5">
          <button
            v-for="item in state.review_items"
            :key="item.id"
            type="button"
            class="flex w-full items-start gap-2 rounded-lg bg-ink-900/60 px-2.5 py-2 text-left"
            :title="item.code"
            @click="emit('open-reviews')"
          >
            <NTag size="tiny" :bordered="false" :type="severityTagType(item.severity)">
              {{ REVIEW_SEVERITY_LABELS[item.severity] ?? item.severity }}
            </NTag>
            <span class="min-w-0 flex-1 text-[10px] leading-5 text-fog-300">{{ item.title }}</span>
          </button>
        </div>
      </section>
    </template>
  </div>
</template>