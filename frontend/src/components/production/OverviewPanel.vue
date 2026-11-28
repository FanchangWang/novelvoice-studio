<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NSpin, NTag, useMessage } from 'naive-ui'

import { api } from '@/api/client'
import { useProductionStore } from '@/stores/production'
import type { BibleStateResponse, BookDetail, ProductionOverviewOut } from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, formatNumber } from '@/utils/format'
import { PRODUCTION_STATUS_LABELS, ROLE_TIER_LABELS, jobStatusLabel, jobTypeLabel, statusTagType } from '@/utils/production'

const props = defineProps<{ bookId: string; overview: ProductionOverviewOut }>()
const emit = defineEmits<{
  (event: 'navigate', tab: 'chapters' | 'script' | 'reviews'): void
  (event: 'changed'): void
}>()
const message = useMessage()
const production = useProductionStore()
const book = ref<BookDetail | null>(null)
const bibleState = ref<BibleStateResponse | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const startingBible = ref(false)
const startingAuto = ref(false)
const cancelling = ref(false)
let token = 0

const bible = computed(() => bibleState.value?.bible ?? null)
const job = computed(() => props.overview.job)
const autoStage = computed(() => {
  const stages = job.value?.result?.stages
  const stageEntries = stages && typeof stages === 'object'
    ? Object.entries(stages)
    : []
  if (job.value?.status === 'failed') {
    const failed = stageEntries.find(([, value]) => {
      if (!value || typeof value !== 'object') return false
      return (value as Record<string, unknown>).status === 'failed'
    })
    if (failed) return failed[0]
  }
  const raw = job.value?.result?.stage
  if (typeof raw === 'string') return raw
  const running = stageEntries.find(([, value]) => {
    if (!value || typeof value !== 'object') return false
    return (value as Record<string, unknown>).status === 'running'
  })
  return running?.[0] ?? null
})
const stageLabels: Record<string, string> = {
  bible: '全书分析',
  casting: '角色整理与自动选角',
  script: '逐章生成剧本',
  audio: '自动配音',
}
const stageOrder = ['bible', 'casting', 'script', 'audio']
const stageStatusLabels: Record<string, string> = {
  pending: '等待',
  running: '处理中',
  completed: '已完成',
  failed: '失败',
  skipped: '已跳过',
  blocked: '已阻塞',
  cancelled: '已取消',
}
type AutoStageView = {
  key: string
  label: string
  status: string
  statusLabel: string
  detail: string
  percent: number | null
}
const autoStages = computed<AutoStageView[]>(() => {
  const raw = job.value?.result?.stages
  const stages = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  const currentIndex = autoStage.value ? stageOrder.indexOf(autoStage.value) : -1
  return stageOrder.map((key, index) => {
    const value = stages[key]
    const stage = value && typeof value === 'object' ? value as Record<string, unknown> : {}
    const terminalStatus = job.value?.status === 'completed'
      ? 'completed'
      : job.value?.status === 'failed'
        ? 'failed'
        : job.value?.status === 'cancelled' ? 'cancelled' : null
    let status = typeof stage.status === 'string'
      ? stage.status
      : terminalStatus && autoStage.value === key
        ? terminalStatus
        : terminalStatus === 'failed' && currentIndex >= 0 && index > currentIndex
          ? 'blocked'
          : terminalStatus === 'cancelled' && currentIndex >= 0 && index >= currentIndex
            ? 'cancelled'
            : autoStage.value === key
              ? 'running'
              : currentIndex > index
                ? 'completed'
                : 'pending'
    if (job.value?.status === 'cancelled' && status === 'pending') status = 'cancelled'
    const total = typeof stage.total_items === 'number' ? stage.total_items : 0
    const completed = typeof stage.completed_items === 'number' ? stage.completed_items : 0
    const rawProgress = typeof stage.progress === 'number' ? stage.progress : 0
    const percent = status === 'running' && total > 0
      ? Math.max(0, Math.min(100, Math.round(rawProgress * 100)))
      : null
    let detail = stageStatusLabels[status] ?? status
    if (status === 'pending') detail = '等待前序任务'
    if (status === 'running') detail = percent === null ? '处理中' : `${percent}% · ${completed} / ${total}`
    if (status === 'completed' && total > 0) detail = `${completed} / ${total} 项`
    if (status === 'skipped') detail = '已有结果，无需重复处理'
    if (status === 'blocked') detail = typeof stage.reason === 'string' ? stage.reason : '前置阶段失败'
    if (status === 'failed' && typeof stage.error_message === 'string') detail = stage.error_message
    if (status === 'cancelled') detail = '本次已取消，已完成结果保留'
    return { key, label: stageLabels[key] ?? key, status, statusLabel: stageStatusLabels[status] ?? status, detail, percent }
  })
})
const hasProductionData = computed(() => Boolean(
  job.value
  || bible.value
  || props.overview.bible_ready
  || props.overview.character_count
  || props.overview.cast_count
  || props.overview.script_unit_count
  || props.overview.chapters_with_scripts,
))
const jobIsActive = computed(() => job.value?.status === 'pending' || job.value?.status === 'running')
const systemFailures = computed<Array<{ stage?: unknown; unit_id?: unknown; message: string }>>(() => {
  const raw = job.value?.result?.system_failures
  if (!Array.isArray(raw)) return []
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const value = entry as Record<string, unknown>
    return [{
      stage: value.stage,
      unit_id: value.unit_id,
      message: typeof value.message === 'string' ? value.message : '未知系统错误',
    }]
  })
})
const protagonists = computed(() =>
  (bible.value?.characters ?? []).filter((character) => character.role_tier === 'protagonist'),
)
const jobPercent = computed(() => {
  if (!job.value) return 0
  if (job.value.job_type === 'auto_production' && job.value.result?.stages) {
    const weight = autoStages.value.reduce((total, stage) => {
      if (['completed', 'skipped', 'failed', 'cancelled'].includes(stage.status)) return total + 1
      if (stage.status === 'running') return total + (stage.percent ?? 0) / 100
      return total
    }, 0)
    return Math.round((weight / stageOrder.length) * 100)
  }
  if (!job.value.total_items) return 0
  return Math.round((job.value.completed_items / job.value.total_items) * 100)
})
const lastAutoFailed = ref(false)
watch(
  () => job.value,
  (current) => {
    if (current?.job_type === 'auto_production' && current.status === 'failed') lastAutoFailed.value = true
    if (current?.job_type === 'auto_production' && (current.status === 'running' || current.status === 'pending' || current.status === 'completed')) lastAutoFailed.value = false
  },
  { immediate: true },
)

async function load(silent = false): Promise<void> {
  const current = ++token
  if (!silent) loading.value = true
  if (!silent) error.value = null
  try {
    const [bookResult, bibleResult] = await Promise.all([
      api.getBook(props.bookId),
      api.getBible(props.bookId),
    ])
    if (current !== token) return
    book.value = bookResult
    bibleState.value = bibleResult
  } catch (err) {
    if (current === token) error.value = errorMessage(err)
  } finally {
    if (current === token) loading.value = false
  }
}

async function startBible(): Promise<void> {
  startingBible.value = true
  try {
    await startAuto(true)
  } finally {
    startingBible.value = false
  }
}

async function startAuto(refreshBible = false): Promise<void> {
  startingAuto.value = true
  try {
    await production.startAutoProduction(refreshBible)
    message.success(refreshBible ? '已重新分析全书并继续自动制作' : '一键自动制作已启动')
    emit('changed')
    await load(true)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    startingAuto.value = false
  }
}

async function cancelAuto(): Promise<void> {
  cancelling.value = true
  try {
    await production.cancelCurrentJob()
    message.success('已取消自动制作，已完成的结果会保留')
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    cancelling.value = false
  }
}

watch(() => props.bookId, () => {
  lastAutoFailed.value = false
  void load()
}, { immediate: true })
watch(
  [() => props.overview.bible_version, () => production.jobFinishedTick],
  () => void load(true),
)
onBeforeUnmount(() => { token += 1 })
</script>

<template>
  <div class="space-y-4">
    <NAlert v-if="error" type="error" :bordered="false">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <span>{{ error }}</span>
        <NButton size="tiny" @click="load()">重试</NButton>
      </div>
    </NAlert>

    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="eyebrow">BOOK / 书籍资料</p>
          <h2 class="mt-1 font-display text-xl text-fog-100">{{ overview.title }}</h2>
          <p class="mt-1 text-[11px] text-fog-400">
            {{ book?.author || '作者未填写' }} · {{ book?.language || 'zh-CN' }} · {{ formatNumber(book?.source_char_count ?? 0) }} 字
          </p>
          <p v-if="book?.description" class="mt-3 max-w-3xl text-[12px] leading-6 text-fog-300">{{ book.description }}</p>
          <p v-else class="mt-3 text-[11px] text-fog-600">尚未填写书籍简介。</p>
        </div>
        <div class="text-right text-[10px] text-fog-500">
          <p>书籍 ID</p>
          <p class="mt-1 font-mono text-fog-300">{{ overview.book_id }}</p>
          <p v-if="book" class="mt-2">更新于 {{ formatDateTime(book.updated_at) }}</p>
        </div>
      </div>
      <div class="mt-4 grid gap-3 border-t border-ink-700/70 pt-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <p class="text-[10px] text-fog-500">原书章节</p>
          <p class="mt-1 font-mono text-lg text-fog-100">{{ formatNumber(book?.chapter_count ?? 0) }}</p>
        </div>
        <div>
          <p class="text-[10px] text-fog-500">参与制作</p>
          <p class="mt-1 font-mono text-lg text-fog-100">{{ formatNumber(overview.chapter_count) }}</p>
        </div>
        <div>
          <p class="text-[10px] text-fog-500">角色</p>
          <p class="mt-1 font-mono text-lg text-fog-100">{{ formatNumber(overview.character_count) }}</p>
        </div>
        <div>
          <p class="text-[10px] text-fog-500">当前状态</p>
          <NTag class="mt-1.5" size="small" :bordered="false" :type="statusTagType(overview.derived_status)">
            {{ PRODUCTION_STATUS_LABELS[overview.derived_status] ?? overview.derived_status }}
          </NTag>
        </div>
      </div>
    </section>

    <section v-if="job" class="nv-card rounded-xl px-4 py-3" aria-label="后台任务进度">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <p class="shrink-0 text-[11px] font-medium text-fog-200">{{ jobTypeLabel(job.job_type) }}</p>
          <NTag size="tiny" :bordered="false" :type="statusTagType(job.status)">{{ jobStatusLabel(job.status) }}</NTag>
          <span v-if="autoStage" class="text-[10px] text-brass-300">{{ stageLabels[autoStage] ?? autoStage }}</span>
        </div>
        <div class="flex min-w-[120px] flex-1 items-center gap-2" role="progressbar" :aria-valuenow="jobPercent" aria-valuemin="0" aria-valuemax="100" :aria-label="`总进度 ${jobPercent}%`">
          <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700"><div class="h-full rounded-full bg-brass-400 transition-all" :style="{ width: `${jobPercent}%` }" /></div>
          <span class="w-9 text-right font-mono text-[11px] text-brass-300">{{ jobPercent }}%</span>
        </div>
        <NButton v-if="jobIsActive" size="tiny" :loading="cancelling" @click="cancelAuto">中止</NButton>
        <NButton v-else-if="job.status === 'failed'" size="tiny" secondary :loading="startingAuto" @click="startAuto()">重试未完成项</NButton>
        <NButton v-else-if="job.status === 'cancelled'" size="tiny" secondary :loading="startingAuto" @click="startAuto()">继续未完成项</NButton>
      </div>
      <ol v-if="job.job_type === 'auto_production'" class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-ink-700/60 pt-2.5">
        <li v-for="stage in autoStages" :key="stage.key" class="flex min-w-0 flex-1 items-center gap-2" :title="stage.detail">
          <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="stage.status === 'running' ? 'animate-pulse bg-brass-400' : stage.status === 'completed' ? 'bg-moss-400' : stage.status === 'failed' ? 'bg-rust-500' : 'bg-fog-700'" />
          <span class="shrink-0 text-[10px]" :class="stage.status === 'running' ? 'text-brass-200' : stage.status === 'failed' ? 'text-rust-300' : 'text-fog-400'">{{ stage.label }}</span>
          <span class="truncate text-[10px] text-fog-500">{{ stage.percent === null ? stage.statusLabel : `${stage.percent}%` }}</span>
        </li>
      </ol>
      <div v-if="job.error_message || systemFailures.length" class="mt-2 border-t border-ink-700/60 pt-2 text-[10px] leading-5 text-rust-300">
        <details>
          <summary class="nv-focus cursor-pointer rounded-sm">查看失败详情{{ systemFailures.length ? `（${systemFailures.length} 项）` : '' }}</summary>
          <p v-if="job.error_message" class="mt-1">{{ job.error_message }}</p>
          <ul v-if="systemFailures.length" class="mt-1 space-y-1">
            <li v-for="(failure, index) in systemFailures" :key="index">
              <span>{{ stageLabels[String(failure.stage)] ?? failure.stage ?? '制作' }}</span>
              <template v-if="failure.unit_id"> · 单元 {{ String(failure.unit_id).slice(-8).toUpperCase() }}</template>
              ：{{ failure.message }}
            </li>
          </ul>
        </details>
      </div>
    </section>

    <section v-if="!hasProductionData" class="nv-card rounded-xl px-5 py-8 text-center">
      <p class="eyebrow">AUTO PRODUCTION / 自动制作</p>
      <h2 class="mt-2 font-display text-xl text-fog-100">从导入到有声书，一次完成</h2>
      <p class="mx-auto mt-3 max-w-xl text-[12px] leading-6 text-fog-400">
        全书分析、角色整理与选角、逐章剧本、自动配音将按顺序执行。完成后的结果会保留，无需逐步确认。
      </p>
      <NButton
        class="mt-5"
        size="small"
        type="primary"
        :loading="startingAuto"
        :disabled="!overview.chapter_count || production.busyAction !== null"
        @click="startAuto()"
      >一键自动制作</NButton>
      <p v-if="!overview.chapter_count" class="mt-3 text-[10px] text-brass-300">请先在章节页确认并纳入正文</p>
    </section>

    <template v-else>
    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="eyebrow">STORY / 全书大纲</p>
          <h2 class="mt-1 font-display text-base text-fog-100">已导入内容的整体故事</h2>
        </div>
        <div class="flex flex-wrap items-center justify-end gap-2">
          <NButton v-if="bible && !job" size="small" :loading="startingBible" @click="startBible">重新分析并续作</NButton>
          <NButton
            v-if="job?.job_type === 'auto_production' && !jobIsActive && job.status !== 'failed'"
            size="small"
            :loading="startingAuto"
            :disabled="!overview.chapter_count || production.busyAction !== null"
            @click="startAuto()"
          >检查并续作</NButton>
          <NButton
            v-if="!job"
            size="small"
            type="primary"
            :loading="startingAuto"
            :disabled="!overview.chapter_count || production.busyAction !== null"
            @click="startAuto()"
          >{{ lastAutoFailed ? '从当前进度重试' : '一键自动制作' }}</NButton>
          <NButton v-else size="tiny" quaternary @click="emit('navigate', 'script')">查看剧本</NButton>
        </div>
      </div>
      <div v-if="loading && !bible" class="flex items-center justify-center py-10 text-xs text-fog-400"><NSpin size="small" class="mr-2" />正在读取分析结果…</div>
      <p v-else-if="bible?.summary" class="mt-4 whitespace-pre-wrap text-[12px] leading-7 text-fog-300">{{ bible.summary }}</p>
      <div v-else class="mt-4 rounded-lg border border-dashed border-ink-700 px-4 py-8 text-center">
        <p class="text-[12px] text-fog-300">还没有全书大纲</p>
        <p class="mt-1 text-[10px] leading-5 text-fog-500">分析已导入的正文后生成；后续更新章节会刷新这份总结。</p>
      </div>
    </section>

    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex items-end justify-between gap-3">
        <div>
          <p class="eyebrow">LEADS / 主角经历</p>
          <h2 class="mt-1 font-display text-base text-fog-100">主要角色的基本经历</h2>
        </div>
        <span class="text-[10px] text-fog-500">{{ protagonists.length }} 位主角</span>
      </div>
      <div v-if="!protagonists.length" class="mt-4 rounded-lg border border-dashed border-ink-700 px-4 py-8 text-center text-[11px] text-fog-500">
        全书分析完成后，在这里查看主角及其重要经历。
      </div>
      <div v-else class="mt-4 space-y-4">
        <article v-for="character in protagonists" :key="character.id" class="border-b border-ink-700/60 pb-4 last:border-0 last:pb-0">
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="font-display text-base text-fog-100">{{ character.canonical_name }}</h3>
            <NTag size="tiny" :bordered="false">{{ ROLE_TIER_LABELS[character.role_tier] }}</NTag>
            <span class="text-[10px] text-fog-500">{{ character.gender }} · {{ character.age_range }}</span>
          </div>
          <p v-if="character.identity_description" class="mt-2 text-[11px] leading-6 text-fog-400">{{ character.identity_description }}</p>
          <ol v-if="character.arcs.length" class="mt-3 space-y-2">
            <li v-for="arc in character.arcs" :key="arc.id" class="flex gap-3">
              <span class="mt-0.5 font-mono text-[9px] text-brass-400">{{ String(arc.arc_index + 1).padStart(2, '0') }}</span>
              <div>
                <p class="text-[11px] text-fog-300">{{ arc.label || `${arc.age_stage}阶段` }}</p>
                <p v-if="arc.summary" class="mt-1 text-[10px] leading-5 text-fog-500">{{ arc.summary }}</p>
              </div>
            </li>
          </ol>
          <p v-else class="mt-2 text-[10px] text-fog-600">暂无经历摘要。</p>
        </article>
      </div>
    </section>

    <section v-if="!job" class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="eyebrow">TASKS / 后台任务</p>
        <div class="flex flex-wrap items-center gap-4 text-[11px] text-fog-400">
          <button type="button" class="nv-focus rounded-sm hover:text-fog-100" @click="emit('navigate', 'chapters')">剧本 {{ overview.chapters_with_scripts }} / {{ overview.chapter_count }} 章</button>
          <button type="button" class="nv-focus rounded-sm hover:text-fog-100" @click="emit('navigate', 'script')">音频 {{ overview.audio_ready_units }} / {{ overview.tts_unit_count }} 单元</button>
          <button type="button" class="nv-focus rounded-sm hover:text-fog-100" @click="emit('navigate', 'reviews')">待处理异常 {{ overview.open_reviews }} 条</button>
        </div>
      </div>
    </section>
    </template>
  </div>
</template>
