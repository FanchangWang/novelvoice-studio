<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import type { ProductionChapterOut, ReviewItemOut } from '@/api/types'
import ChaptersPanel from '@/components/production/ChaptersPanel.vue'
import CharactersPanel from '@/components/production/CharactersPanel.vue'
import OverviewPanel from '@/components/production/OverviewPanel.vue'
import ReviewInbox from '@/components/production/ReviewInbox.vue'
import ScriptPanel from '@/components/production/ScriptPanel.vue'
import { useProductionStore } from '@/stores/production'
import { errorMessage } from '@/utils/errors'
import {
  PRODUCTION_STATUS_LABELS,
  statusTagType,
  type ProductionTab,
} from '@/utils/production'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const production = useProductionStore()

const tab = ref<ProductionTab>('overview')
const scriptChapterId = ref<string | null>(null)
const reviewChapterId = ref<string | null>(null)
const scriptFocusUnitId = ref<string | null>(null)
const characterFocusId = ref<string | null>(null)
const sourceRequest = ref<{
  chapterId: string
  quote?: string
  start?: number
  end?: number
} | null>(null)

const bookId = computed(() => {
  const raw = route.params.bookId
  return typeof raw === 'string' ? raw : ''
})
const overview = computed(() => production.overview)
const autoProductionActive = computed(() => Boolean(
  overview.value?.job && ['pending', 'running'].includes(overview.value.job.status),
))
const autoStages = computed<Record<string, unknown>>(() => {
  const result = overview.value?.job?.job_type === 'auto_production' ? overview.value.job.result : null
  const stages = result?.stages
  return stages && typeof stages === 'object' ? stages as Record<string, unknown> : {}
})

const stageOrder = ['bible', 'casting', 'script', 'audio']
type PanelStage = 'ready' | 'waiting' | 'running' | 'blocked'
function panelStage(key: 'casting' | 'script'): PanelStage {
  const value = autoStages.value[key]
  const rawStatus = value && typeof value === 'object'
    ? (value as Record<string, unknown>).status
    : null
  if (rawStatus === 'running') return 'running'
  if (rawStatus === 'pending' || rawStatus === 'cancelled') return 'waiting'
  if (rawStatus === 'blocked') return 'blocked'
  if (typeof rawStatus === 'string') return 'ready'

  const stages = autoStages.value
  const rawCurrent = overview.value?.job?.result?.stage
  const current = typeof rawCurrent === 'string'
    ? rawCurrent
    : Object.entries(stages).find(([, stage]) => {
      if (!stage || typeof stage !== 'object') return false
      const status = (stage as Record<string, unknown>).status
      return status === 'running' || status === 'failed'
    })?.[0]
  const currentIndex = current ? stageOrder.indexOf(current) : -1
  const keyIndex = stageOrder.indexOf(key)
  if (currentIndex >= 0) {
    if (currentIndex > keyIndex) return 'ready'
    if (currentIndex === keyIndex) {
      return overview.value?.job?.status === 'failed' || overview.value?.job?.status === 'completed'
        ? 'ready'
        : 'running'
    }
    return 'waiting'
  }
  if (autoProductionActive.value) return 'waiting'
  if (key === 'casting' && !overview.value?.bible_ready) return 'waiting'
  if (key === 'script' && !overview.value?.script_unit_count) return 'waiting'
  return 'ready'
}
function panelStageMessage(key: 'casting' | 'script'): { title: string; detail: string } {
  const status = panelStage(key)
  if (status === 'running') {
    return key === 'casting'
      ? { title: '正在整理角色与分配声音', detail: '任务完成后，角色会按主角、重要配角和群演出现在这里。' }
      : { title: '正在生成逐章剧本', detail: '全书分析和选角已完成，剧本正在按章节顺序生成。完成后会自动继续配音。' }
  }
  if (status === 'blocked') {
    return { title: '前序任务尚未完成', detail: '请先在总览处理前置阶段；完成后本页会自动展示可用结果。' }
  }
  if (key === 'script') {
    return { title: '剧本还未开放', detail: '请先运行一键自动制作；处理到剧本阶段后，才能查看和调整逐章剧本。' }
  }
  return { title: '角色与声音还未整理', detail: '全书分析完成后会自动整理角色并选角，无需先手动创建角色。' }
}

const tabs = computed<
  Array<{ key: ProductionTab; label: string; count?: number; tone?: 'warning' | 'default' }>
>(() => [
  { key: 'overview', label: '总览' },
  { key: 'chapters', label: '章节' },
  { key: 'script', label: '剧本' },
  { key: 'casting', label: '角色' },
  {
    key: 'reviews',
    label: '异常',
    count: overview.value?.blocking_reviews || undefined,
    tone: overview.value?.blocking_reviews ? 'warning' : 'default',
  },
])

function firstScriptChapter(chapters: ProductionChapterOut[]): string | null {
  return chapters.find((chapter) => chapter.analysis_included)?.id ?? null
}

function syncChapterDefaults(): void {
  const chapters = overview.value?.chapters ?? []
  const included = chapters.filter((chapter) => chapter.analysis_included)
  if (!included.some((chapter) => chapter.id === scriptChapterId.value)) {
    scriptChapterId.value = firstScriptChapter(chapters)
  }
}

function setTab(next: ProductionTab, updateRoute = true): void {
  tab.value = next
  if (next !== 'reviews') {
    reviewChapterId.value = null
  }
  if (updateRoute) {
    const query = { ...route.query, tab: next === 'overview' ? undefined : next }
    void router.replace({ query })
  }
}

function openChapter(chapterId: string): void {
  scriptChapterId.value = chapterId
  setTab('script')
}

function openReviews(chapterId: string | null = null): void {
  setTab('reviews')
  reviewChapterId.value = chapterId
}

function openLocation(item: ReviewItemOut): void {
  const location = item.location
  if (location.unit_id) {
    if (location.chapter_id) scriptChapterId.value = location.chapter_id
    scriptFocusUnitId.value = location.unit_id
    setTab('script')
    return
  }
  if (location.character_id) {
    characterFocusId.value = location.character_id
    setTab('casting')
    return
  }
  if (location.chapter_id) {
    if (item.action_type === 'chapter_classification') {
      setTab('chapters')
      return
    }
    sourceRequest.value = {
      chapterId: location.chapter_id,
      ...(location.quote ? { quote: location.quote } : {}),
      ...(typeof location.source_start_offset === 'number' ? { start: location.source_start_offset } : {}),
      ...(typeof location.source_end_offset === 'number' ? { end: location.source_end_offset } : {}),
    }
    setTab('chapters')
    return
  }
  message.warning('该项没有可定位的内容位置，请在总览查看后台任务状态')
}

function parseTab(): ProductionTab {
  const raw = typeof route.query.tab === 'string' ? route.query.tab : ''
  const allowed: ProductionTab[] = ['overview', 'chapters', 'script', 'casting', 'reviews']
  return (allowed as string[]).includes(raw) ? (raw as ProductionTab) : 'overview'
}

async function load(showMessage = false): Promise<void> {
  if (!bookId.value) {
    return
  }
  try {
    await production.load(bookId.value)
    syncChapterDefaults()
  } catch (err) {
    if (showMessage) {
      message.error(errorMessage(err))
    }
  }
}

async function refreshAll(): Promise<void> {
  await load(true)
}

async function onPanelChanged(): Promise<void> {
  await production.refresh()
  syncChapterDefaults()
}

watch(
  () => overview.value?.chapters,
  syncChapterDefaults,
  { deep: true },
)

watch(
  () => route.query.tab,
  () => {
    const next = parseTab()
    if (next !== tab.value) {
      tab.value = next
    }
  },
)

watch(
  () => bookId.value,
  (next, previous) => {
    if (!next) {
      return
    }
    if (previous && next !== previous) {
      production.reset()
    }
    tab.value = parseTab()
    void load(true)
  },
)

onMounted(() => {
  tab.value = parseTab()
  void load(true)
})

onBeforeUnmount(() => {
  production.reset()
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header class="shrink-0 border-b border-ink-700/80 bg-ink-950/40 px-5 pb-0 pt-6 backdrop-blur md:px-8">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="flex min-w-0 items-start gap-3">
          <NButton size="small" quaternary class="mt-0.5" @click="router.push('/library')">
            ← 书库
          </NButton>
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <p class="eyebrow">PRODUCTION / 制作台</p>
              <NTag
                v-if="overview"
                size="small"
                :bordered="false"
                :type="statusTagType(overview.derived_status)"
              >
                {{ PRODUCTION_STATUS_LABELS[overview.derived_status] ?? overview.derived_status }}
              </NTag>
              <NTag v-if="overview?.job && ['pending', 'running'].includes(overview.job.status)" size="small" :bordered="false" type="info">
                任务进行中
              </NTag>
            </div>
            <h1 class="mt-1.5 truncate font-display text-2xl text-fog-100">
              {{ overview?.title ?? '制作台' }}
            </h1>
            <p class="mt-1 text-[10px] text-fog-500">
              导入原文 → 章节整理 → 全书分析 → 角色与选角 → 剧本 → 自动配音
              <span class="mx-1.5 text-fog-600">·</span>
              {{ overview?.chapter_count ?? 0 }} 章
              <span class="mx-1.5 text-fog-600">·</span>
              {{ overview?.character_count ?? 0 }} 角色
              <span class="mx-1.5 text-fog-600">·</span>
              {{ overview?.script_unit_count ?? 0 }} 剧本单元
            </p>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <NButton
            size="small"
            quaternary
            :loading="production.loading"
            @click="refreshAll"
          >
            刷新
          </NButton>
        </div>
      </div>

      <nav class="mt-5 flex flex-wrap items-end gap-1">
        <button
          v-for="item in tabs"
          :key="item.key"
          type="button"
          class="relative flex items-center gap-1.5 rounded-t-lg px-3.5 py-2 text-[11px] transition-colors"
          :class="
            tab === item.key
              ? 'bg-ink-900/80 text-fog-100'
              : 'text-fog-500 hover:bg-ink-900/40 hover:text-fog-300'
          "
          @click="item.key === 'reviews' ? openReviews() : setTab(item.key)"
        >
          {{ item.label }}
          <span
            v-if="item.count"
            class="rounded px-1 py-0.5 font-mono text-[9px]"
            :class="
              item.tone === 'warning'
                ? 'bg-brass-500/15 text-brass-300'
                : 'bg-ink-700 text-fog-400'
            "
          >
            {{ item.count }}
          </span>
          <span
            v-if="tab === item.key"
            class="absolute inset-x-2 bottom-0 h-[2px] rounded-full bg-brass-400"
          />
        </button>
      </nav>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-6xl px-5 py-6 md:px-8">
        <NAlert v-if="production.error" type="error" :bordered="false" class="mb-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <span class="text-[11px]">{{ production.error }}</span>
            <NButton size="tiny" quaternary @click="load(true)">重试</NButton>
          </div>
        </NAlert>

        <div v-if="!overview && production.loading" class="flex items-center justify-center py-28 text-xs text-fog-400">
          <NSpin size="small" class="mr-3" />
          正在读取制作进度…
        </div>

        <div
          v-else-if="!overview"
          class="flex min-h-[360px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
        >
          <h2 class="font-display text-xl text-fog-200">无法读取这本书</h2>
          <p class="mt-2 text-[11px] text-fog-500">
            {{ production.error || '书籍可能已被删除，或后端尚未启动。' }}
          </p>
          <NButton class="mt-4" @click="router.push('/library')">返回书库</NButton>
        </div>

        <template v-else>
          <OverviewPanel
            v-if="tab === 'overview'"
            :book-id="bookId"
            :overview="overview"
            @changed="onPanelChanged"
            @navigate="setTab"
          />

          <ChaptersPanel
            v-else-if="tab === 'chapters'"
            :book-id="bookId"
            :overview="overview"
            :source-request="sourceRequest"
            @changed="onPanelChanged"
            @open-script="openChapter"
            @source-closed="sourceRequest = null"
          />

          <section
            v-else-if="tab === 'script' && panelStage('script') !== 'ready'"
            class="flex min-h-[320px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
          >
            <NSpin v-if="panelStage('script') === 'running'" size="small" class="mb-4" />
            <h2 class="font-display text-xl text-fog-200">{{ panelStageMessage('script').title }}</h2>
            <p class="mt-2 max-w-md text-[11px] leading-6 text-fog-500">{{ panelStageMessage('script').detail }}</p>
          </section>

          <ScriptPanel
            v-else-if="tab === 'script'"
            v-model:chapter-id="scriptChapterId"
            :book-id="bookId"
            :chapters="overview.chapters"
            :focus-unit-id="scriptFocusUnitId"
            :auto-script-running="panelStage('script') === 'running'"
            :auto-production-active="autoProductionActive"
            :auto-script-stage="panelStage('script')"
            @changed="onPanelChanged"
            @open-reviews="openReviews"
          />

          <section
            v-else-if="tab === 'casting' && panelStage('casting') !== 'ready'"
            class="flex min-h-[320px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
          >
            <NSpin v-if="panelStage('casting') === 'running'" size="small" class="mb-4" />
            <h2 class="font-display text-xl text-fog-200">{{ panelStageMessage('casting').title }}</h2>
            <p class="mt-2 max-w-md text-[11px] leading-6 text-fog-500">{{ panelStageMessage('casting').detail }}</p>
          </section>

          <CharactersPanel
            v-else-if="tab === 'casting'"
            :book-id="bookId"
            :bible-ready="overview.bible_ready"
            :focus-character-id="characterFocusId"
            @changed="onPanelChanged"
          />

          <ReviewInbox
            v-else-if="tab === 'reviews'"
            :book-id="bookId"
            v-model:chapter-filter="reviewChapterId"
            :chapters="overview.chapters"
            heading="本书异常收件箱"
            hint="只汇总 AI 无法自动处理、确实需要你判断的内容问题；系统故障在任务状态中显示。"
            @changed="onPanelChanged"
            @open-location="openLocation"
          />
        </template>
      </div>
    </div>
  </div>
</template>