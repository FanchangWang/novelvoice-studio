<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NSpin,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type { BibleStateResponse } from '@/api/types'
import { useProductionStore } from '@/stores/production'
import { isJobActive } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'
import {
  AGE_LABELS,
  BASIS_LABELS,
  GENDER_LABELS,
  labelOf,
  percent,
} from '@/utils/analysis'
import {
  BIBLE_STATUS_LABELS,
  NARRATIVE_MODE_LABELS,
  ROLE_TIER_LABELS,
  TIME_MODE_LABELS,
  jobStatusLabel,
  jobTypeLabel,
  statusTagType,
} from '@/utils/production'

const props = defineProps<{ bookId: string }>()
const emit = defineEmits<{ (event: 'changed'): void }>()

const production = useProductionStore()
const dialog = useDialog()
const message = useMessage()

const state = ref<BibleStateResponse | null>(null)
const loading = ref(false)
const actionLoading = ref(false)
const error = ref<string | null>(null)

let pollTimer: ReturnType<typeof setTimeout> | null = null
let lastJobActive = false

const bible = computed(() => state.value?.bible ?? null)
const job = computed(() => state.value?.job ?? null)
const jobActive = computed(() => isJobActive(job.value?.status))
const jobStatusText = computed(() => {
  const current = job.value
  if (!current) return '未知状态'
  // 单章失败不整单失败：任务会跑完并把失败章节数记在 failed_items。
  if (current.status === 'completed' && current.failed_items > 0) {
    return `部分完成（${current.failed_items} 章待补）`
  }
  if (current.status === 'completed' && current.completed_items < current.total_items) {
    return `部分完成（已覆盖 ${current.completed_items}/${current.total_items} 章）`
  }
  return jobStatusLabel(current.status)
})
const partialJob = computed(() => {
  const current = job.value
  if (!current || jobActive.value) return false
  if (current.status === 'completed') {
    return current.failed_items > 0 || current.completed_items < current.total_items
  }
  if (current.status === 'failed' || current.status === 'cancelled') {
    return current.completed_items > 0 && current.completed_items < current.total_items
  }
  return false
})
const bibleReady = computed(
  () => state.value?.bible_status === 'approved' || state.value?.bible_status === 'needs_review',
)

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
  if (!silent) {
    loading.value = true
  }
  error.value = null
  try {
    const response = await api.getBible(props.bookId)
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
    error.value = errorMessage(err)
    throw err
  } finally {
    loading.value = false
  }
}

async function start(): Promise<void> {
  actionLoading.value = true
  error.value = null
  try {
    await api.startBible(props.bookId)
    message.success(
      partialJob.value ? '已从断点继续生成设定集' : '已开始生成全书设定集',
    )
    await load(true)
    await production.refresh()
  } catch (err) {
    error.value = errorMessage(err)
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function confirmStart(): void {
  if (!bible.value) {
    void start()
    return
  }
  dialog.warning({
    title: partialJob.value ? '继续生成设定集' : '重新生成设定集',
    content: partialJob.value
      ? '会从上次中断的章节继续，已完成的章节不会重跑；新版本保留旧版本的已完成结果。'
      : '会基于当前章节重新提取人物、别名、人物弧与时间线，生成新的设定集版本。旧版本和已有分场不会自动删除。',
    positiveText: partialJob.value ? '继续生成' : '重新生成',
    negativeText: '取消',
    onPositiveClick: () => start(),
  })
}

async function finalizeNow(): Promise<void> {
  actionLoading.value = true
  error.value = null
  try {
    await api.finalizeBible(props.bookId)
    message.success('正在收尾，完成后即可用这一版开始选角')
    await load(true)
    await production.refresh()
  } catch (err) {
    error.value = errorMessage(err)
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function confirmFinalize(): void {
  dialog.warning({
    title: '用当前进度先出一版？',
    content: `已完成 ${job.value?.completed_items ?? 0} 章，将停止继续抽取并合并成可用设定集；剩余章节以后可以继续补。`,
    positiveText: '收尾出可用版',
    negativeText: '继续跑',
    onPositiveClick: () => finalizeNow(),
  })
}

async function cancel(): Promise<void> {
  actionLoading.value = true
  try {
    await api.cancelBible(props.bookId)
    message.success('已请求取消设定集任务')
    await load(true)
    await production.refresh()
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

onMounted(() => {
  void load().catch(() => undefined)
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="space-y-4">
    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="eyebrow">BOOK BIBLE / 设定集</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <h2 class="font-display text-xl text-fog-100">全书人物与剧情时间线</h2>
            <NTag
              size="small"
              :bordered="false"
              :type="statusTagType(state?.bible_status ?? 'not_started')"
            >
              {{ BIBLE_STATUS_LABELS[state?.bible_status ?? 'not_started'] ?? state?.bible_status }}
            </NTag>
            <NTag v-if="bible" size="small" :bordered="false">
              v{{ bible.version }} · {{ percent(bible.confidence) }}
            </NTag>
            <NTag v-if="bible?.model" size="small" :bordered="false">
              {{ bible.model }}
            </NTag>
          </div>
          <p class="mt-2 max-w-2xl text-[11px] leading-5 text-fog-500">
            设定集是分场、剧本、选角的共同依据。它只读取本机原文与本地模型，不联网。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <NButton size="small" quaternary :loading="loading" @click="load()">刷新</NButton>
          <NButton
            v-if="jobActive"
            size="small"
            secondary
            :loading="actionLoading"
            @click="cancel"
          >
            取消任务
          </NButton>
          <NButton
            v-if="jobActive && (job?.completed_items ?? 0) > 0"
            size="small"
            type="primary"
            ghost
            :loading="actionLoading"
            @click="confirmFinalize"
          >
            先收尾出可用版
          </NButton>
          <NButton
            v-else
            size="small"
            type="primary"
            :loading="actionLoading"
            @click="confirmStart"
          >
            {{ partialJob ? '继续生成' : bible ? '重新生成' : '生成设定集' }}
          </NButton>
        </div>
      </div>

      <div v-if="job" class="mt-4 rounded-lg border border-brass-600/25 bg-brass-500/[0.04] px-3.5 py-2.5">
        <div class="flex flex-wrap items-center gap-3">
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="jobActive ? 'animate-pulse bg-brass-400' : job.status === 'failed' ? 'bg-rust-500' : 'bg-moss-400'"
          />
          <span class="text-[11px] text-brass-200">
            {{ jobTypeLabel(job.job_type) }} · {{ jobStatusText }} ·
            {{ Math.round((job.progress || 0) * 100) }}%
          </span>
          <span class="text-[10px] text-fog-500">
            {{ job.completed_items }} / {{ job.total_items }}
          </span>
          <span v-if="job.error_message" class="text-[10px] text-rust-400">
            {{ job.error_message }}
          </span>
        </div>
        <div v-if="jobActive" class="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
          <div
            class="h-full rounded-full bg-brass-400 transition-all"
            :style="{ width: `${Math.round((job.progress || 0) * 100)}%` }"
          />
        </div>
      </div>

      <NAlert v-if="error" type="error" :bordered="false" class="mt-3">
        {{ error }}
      </NAlert>
    </section>

    <div v-if="loading && !state" class="flex items-center justify-center py-24 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取设定集…
    </div>

    <div
      v-else-if="!bible"
      class="flex min-h-[300px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
    >
      <h3 class="font-display text-xl text-fog-200">还没有设定集</h3>
      <p class="mt-2 max-w-md text-[11px] leading-6 text-fog-500">
        生成设定集会通读已导入的章节，整理角色标准名、别名、人物弧、关系与剧情时间线。
        如果使用真实模型，这一步通常最耗时。
      </p>
      <NButton class="mt-4" type="primary" :loading="actionLoading" @click="start">
        生成全书设定集
      </NButton>
    </div>

    <template v-else>
      <NAlert v-if="bible.warnings.length" type="warning" :bordered="false">
        <p v-for="warning in bible.warnings" :key="warning" class="text-[11px] leading-5">
          {{ warning }}
        </p>
      </NAlert>

      <section class="nv-card rounded-xl px-4 py-3.5">
        <div class="flex flex-wrap items-center gap-x-5 gap-y-2 text-[10px] text-fog-500">
          <span>共 {{ bible.characters.length }} 个角色</span>
          <span>{{ bible.relationships.length }} 条关系</span>
          <span>{{ bible.timeline.chapters.length }} 章时间线</span>
          <span>
            叙事：
            {{ NARRATIVE_MODE_LABELS[bible.timeline.narrative_mode] ?? bible.timeline.narrative_mode }}
          </span>
          <span class="ml-auto">{{ formatDateTime(bible.updated_at) }}</span>
        </div>
        <p v-if="bible.summary" class="mt-2.5 border-t border-ink-700/70 pt-2.5 text-[12px] leading-6 text-fog-300">
          {{ bible.summary }}
        </p>
        <div v-if="bible.timeline.notes.length" class="mt-2 flex flex-wrap gap-2">
          <span
            v-for="note in bible.timeline.notes"
            :key="note"
            class="rounded border border-ink-600 bg-ink-850 px-2 py-1 text-[10px] text-fog-400"
          >
            {{ note }}
          </span>
        </div>
      </section>

      <section>
        <div class="flex items-center justify-between">
          <h3 class="font-display text-base text-fog-100">角色</h3>
          <span class="text-[10px] text-fog-500">按重要度排序</span>
        </div>
        <div class="mt-2 grid items-start gap-3 xl:grid-cols-2">
          <article
            v-for="character in bible.characters"
            :key="character.id"
            class="nv-card rounded-xl px-4 py-3.5"
          >
            <div class="flex items-start gap-3">
              <div
                class="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-ink-600 bg-ink-800 font-display text-sm text-fog-200"
              >
                {{ character.canonical_name.slice(0, 1) }}
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <h4 class="font-display text-base text-fog-100">{{ character.canonical_name }}</h4>
                  <NTag size="tiny" :bordered="false">
                    {{ ROLE_TIER_LABELS[character.role_tier] ?? character.role_tier }}
                  </NTag>
                  <NTag size="tiny" :bordered="false" type="info">
                    {{ labelOf(GENDER_LABELS, character.gender) }} ·
                    {{ labelOf(AGE_LABELS, character.age_range) }}
                  </NTag>
                  <NTag
                    v-if="character.is_provisional"
                    size="tiny"
                    :bordered="false"
                    type="warning"
                  >
                    待确认
                  </NTag>
                  <NTooltip trigger="hover">
                    <template #trigger>
                      <span class="font-mono text-[9px] text-fog-600">
                        {{ percent(character.confidence) }}
                      </span>
                    </template>
                    角色归属置信度
                  </NTooltip>
                </div>
                <p v-if="character.identity_description" class="mt-1.5 text-[11px] leading-5 text-fog-400">
                  {{ character.identity_description }}
                </p>
                <div v-if="character.personality.length" class="mt-1.5 flex flex-wrap gap-1">
                  <span
                    v-for="tag in character.personality"
                    :key="tag"
                    class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-fog-400"
                  >
                    {{ tag }}
                  </span>
                </div>
                <p
                  v-if="character.aliases.length"
                  class="mt-1.5 text-[10px] leading-5 text-fog-500"
                >
                  别名：
                  <span v-for="(alias, index) in character.aliases" :key="alias.name">
                    {{ alias.name }}（{{ labelOf(BASIS_LABELS, alias.basis) }}）{{ index < character.aliases.length - 1 ? '、' : '' }}
                  </span>
                </p>
                <p v-if="character.voice_description" class="mt-1 text-[10px] leading-5 text-brass-300/90">
                  声线建议：{{ character.voice_description }}
                </p>
              </div>
            </div>

            <div v-if="character.arcs.length" class="mt-3 space-y-1.5 border-t border-ink-700/70 pt-2.5">
              <p class="eyebrow">人物弧</p>
              <div
                v-for="arc in character.arcs"
                :key="arc.id"
                class="rounded-lg bg-ink-900/60 px-2.5 py-2"
              >
                <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                  <span class="text-fog-200">{{ arc.label }}</span>
                  <span class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-fog-400">
                    {{ labelOf(AGE_LABELS, arc.age_stage) }}
                  </span>
                  <span v-if="arc.story_time_start || arc.story_time_end" class="text-fog-500">
                    {{ arc.story_time_start || '—' }} → {{ arc.story_time_end || '—' }}
                  </span>
                  <span class="ml-auto font-mono text-[9px] text-fog-600">{{ percent(arc.confidence) }}</span>
                </div>
                <p v-if="arc.summary" class="mt-1 text-[10px] leading-5 text-fog-400">
                  {{ arc.summary }}
                </p>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="grid gap-3 lg:grid-cols-2">
        <div class="nv-card rounded-xl px-4 py-3.5">
          <div class="flex items-center justify-between">
            <h3 class="font-display text-base text-fog-100">关系</h3>
            <span class="text-[10px] text-fog-500">{{ bible.relationships.length }} 条</span>
          </div>
          <div v-if="!bible.relationships.length" class="py-6 text-center text-[10px] text-fog-500">
            暂未整理出角色关系。
          </div>
          <div v-else class="mt-2 space-y-1.5">
            <div
              v-for="relationship in bible.relationships"
              :key="relationship.id"
              class="rounded-lg bg-ink-900/60 px-2.5 py-2 text-[10px]"
            >
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-fog-200">{{ relationship.source_name ?? '—' }}</span>
                <span class="text-fog-600">→</span>
                <span class="text-fog-200">{{ relationship.target_name ?? '—' }}</span>
                <span class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-brass-300">
                  {{ relationship.relationship_type }}
                </span>
                <span class="ml-auto font-mono text-[9px] text-fog-600">
                  {{ percent(relationship.confidence) }}
                </span>
              </div>
              <p v-if="relationship.description" class="mt-1 leading-5 text-fog-400">
                {{ relationship.description }}
              </p>
            </div>
          </div>
        </div>

        <div class="nv-card rounded-xl px-4 py-3.5">
          <div class="flex items-center justify-between">
            <h3 class="font-display text-base text-fog-100">剧情时间线</h3>
            <span class="text-[10px] text-fog-500">{{ bible.timeline.chapters.length }} 章</span>
          </div>
          <div v-if="!bible.timeline.chapters.length" class="py-6 text-center text-[10px] text-fog-500">
            暂未整理时间线。
          </div>
          <div v-else class="mt-2 max-h-[420px] space-y-1.5 overflow-y-auto pr-1">
            <div
              v-for="chapter in bible.timeline.chapters"
              :key="chapter.chapter_id"
              class="rounded-lg bg-ink-900/60 px-2.5 py-2"
            >
              <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                <span class="font-mono text-fog-600">{{ chapter.sequence }}</span>
                <span class="min-w-0 flex-1 truncate text-fog-200">{{ chapter.title }}</span>
                <span class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-lake-400">
                  {{ TIME_MODE_LABELS[chapter.time_mode] ?? chapter.time_mode }}
                </span>
                <span v-if="chapter.story_time_label" class="text-[9px] text-fog-500">
                  {{ chapter.story_time_label }}
                </span>
              </div>
              <p v-if="chapter.summary" class="mt-1 line-clamp-2 text-[10px] leading-5 text-fog-400">
                {{ chapter.summary }}
              </p>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>