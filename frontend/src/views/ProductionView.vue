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

import type { ProductionChapterOut } from '@/api/types'
import BiblePanel from '@/components/production/BiblePanel.vue'
import CastingPanel from '@/components/production/CastingPanel.vue'
import PlanPanel from '@/components/production/PlanPanel.vue'
import ReviewInbox from '@/components/production/ReviewInbox.vue'
import ScenesPanel from '@/components/production/ScenesPanel.vue'
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

const tab = ref<ProductionTab>('plan')
const sceneChapterId = ref<string | null>(null)
const scriptChapterId = ref<string | null>(null)

const bookId = computed(() => {
  const raw = route.params.bookId
  return typeof raw === 'string' ? raw : ''
})
const overview = computed(() => production.overview)

const tabs = computed<
  Array<{ key: ProductionTab; label: string; count?: number; tone?: 'warning' | 'default' }>
>(() => [
  { key: 'plan', label: '方案' },
  { key: 'bible', label: '设定集' },
  { key: 'scenes', label: '分场' },
  { key: 'script', label: '剧本' },
  { key: 'casting', label: '选角' },
  {
    key: 'reviews',
    label: '异常',
    count: overview.value?.open_reviews,
    tone: overview.value?.blocking_reviews || overview.value?.warning_reviews ? 'warning' : 'default',
  },
])

function firstSceneChapter(chapters: ProductionChapterOut[]): string | null {
  return (
    chapters.find(
      (chapter) => chapter.scene_status === 'not_started' || chapter.scene_count === 0,
    )?.id ??
    chapters.find((chapter) => chapter.scene_status === 'needs_review')?.id ??
    chapters[0]?.id ??
    null
  )
}

function firstScriptChapter(chapters: ProductionChapterOut[]): string | null {
  return (
    chapters.find(
      (chapter) => chapter.scene_count > 0 && chapter.unit_count === 0,
    )?.id ??
    chapters.find((chapter) => chapter.script_status === 'needs_review')?.id ??
    chapters[0]?.id ??
    null
  )
}

function syncChapterDefaults(): void {
  const chapters = overview.value?.chapters ?? []
  if (!chapters.length) {
    sceneChapterId.value = null
    scriptChapterId.value = null
    return
  }
  if (!chapters.some((chapter) => chapter.id === sceneChapterId.value)) {
    sceneChapterId.value = firstSceneChapter(chapters)
  }
  if (!chapters.some((chapter) => chapter.id === scriptChapterId.value)) {
    scriptChapterId.value = firstScriptChapter(chapters)
  }
}

function setTab(next: ProductionTab, updateRoute = true): void {
  tab.value = next
  if (updateRoute) {
    const query = { ...route.query, tab: next === 'plan' ? undefined : next }
    void router.replace({ query })
  }
}

function openChapter(chapterId: string, target: 'scenes' | 'script'): void {
  if (target === 'scenes') {
    sceneChapterId.value = chapterId
  } else {
    scriptChapterId.value = chapterId
  }
  setTab(target)
}

function openReviews(): void {
  setTab('reviews')
}

function parseTab(): ProductionTab {
  const raw = typeof route.query.tab === 'string' ? route.query.tab : ''
  const allowed: ProductionTab[] = ['plan', 'bible', 'scenes', 'script', 'casting', 'reviews']
  return (allowed as string[]).includes(raw) ? (raw as ProductionTab) : 'plan'
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
              <NTag v-if="overview?.job" size="small" :bordered="false" type="info">
                任务进行中
              </NTag>
            </div>
            <h1 class="mt-1.5 truncate font-display text-2xl text-fog-100">
              {{ overview?.title ?? '制作台' }}
            </h1>
            <p class="mt-1 text-[10px] text-fog-500">
              导入原文 → 设定集 → 分场 → 剧本 → 选角 → 生成音频
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
          <NButton
            size="small"
            secondary
            @click="router.push({ name: 'workspace', params: { bookId } })"
          >
            原文与校对
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
          @click="setTab(item.key)"
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
          <PlanPanel
            v-if="tab === 'plan'"
            :overview="overview"
            @navigate="setTab"
            @open-chapter="openChapter"
          />

          <BiblePanel
            v-else-if="tab === 'bible'"
            :book-id="bookId"
            @changed="onPanelChanged"
          />

          <ScenesPanel
            v-else-if="tab === 'scenes'"
            v-model:chapter-id="sceneChapterId"
            :book-id="bookId"
            :chapters="overview.chapters"
            :bible-ready="overview.bible_ready"
            @changed="onPanelChanged"
            @open-reviews="openReviews"
          />

          <ScriptPanel
            v-else-if="tab === 'script'"
            v-model:chapter-id="scriptChapterId"
            :book-id="bookId"
            :chapters="overview.chapters"
            @changed="onPanelChanged"
            @open-reviews="openReviews"
            @open-scenes="setTab('scenes')"
          />

          <CastingPanel
            v-else-if="tab === 'casting'"
            :book-id="bookId"
            :bible-ready="overview.bible_ready"
            @changed="onPanelChanged"
            @open-reviews="openReviews"
          />

          <ReviewInbox
            v-else-if="tab === 'reviews'"
            :book-id="bookId"
            heading="本书异常收件箱"
            hint="设定集、分场、剧本与选角中需要你判断的节点都会汇总到这里。"
            @changed="onPanelChanged"
          />
        </template>
      </div>
    </div>
  </div>
</template>