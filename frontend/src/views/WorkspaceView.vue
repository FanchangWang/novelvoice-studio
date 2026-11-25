<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NInput,
  NModal,
  NSelect,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import type { ChapterSummary, Paragraph } from '@/api/types'
import AnalysisSummary from '@/components/analysis/AnalysisSummary.vue'
import CharacterReviewPanel from '@/components/analysis/CharacterReviewPanel.vue'
import SegmentReviewPanel from '@/components/analysis/SegmentReviewPanel.vue'
import DubbingPanel from '@/components/dubbing/DubbingPanel.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useSystemStore } from '@/stores/system'
import { useWorkspaceStore } from '@/stores/workspace'
import { errorMessage } from '@/utils/errors'
import {
  formatDateTime,
  formatNumber,
  selectionIsContiguous,
  truncate,
} from '@/utils/format'

const props = defineProps<{ bookId: string }>()

const store = useWorkspaceStore()
const analysis = useAnalysisStore()
const system = useSystemStore()
const router = useRouter()
const dialog = useDialog()
const message = useMessage()

type WorkspaceTab = 'text' | 'segments' | 'characters' | 'dubbing'

const activeTab = ref<WorkspaceTab>('text')
const dubbingTick = ref(0)

const filter = ref('')
const mergeMode = ref(false)
const mergeIds = ref<string[]>([])
const editingTitle = ref(false)
const titleDraft = ref('')
const savingTitle = ref(false)
const showSplit = ref(false)
const showAnalysisPanel = ref(false)
const splitting = ref(false)
const splitParagraph = ref<Paragraph | null>(null)
const splitTitle = ref('')

const filteredChapters = computed(() => {
  const keyword = filter.value.trim().toLowerCase()
  if (!keyword) {
    return store.chapters
  }
  return store.chapters.filter((chapter) =>
    chapter.title.toLowerCase().includes(keyword),
  )
})

const chapterOptions = computed(() =>
  store.chapters.map((chapter) => ({
    label: `${pad(chapter.sequence)} · ${chapter.title}`,
    value: chapter.id,
  })),
)

const paragraphs = computed(() => store.current?.paragraphs ?? [])

const dialogueCount = computed(
  () =>
    paragraphs.value.filter((paragraph) => {
      const text = paragraph.text.trim()
      return /^[“「『"‘']/u.test(text)
    }).length,
)

const selectedMergeChapters = computed(() =>
  store.chapters
    .filter((chapter) => mergeIds.value.includes(chapter.id))
    .sort((a, b) => a.sequence - b.sequence),
)

const canMerge = computed(
  () =>
    mergeIds.value.length >= 2 &&
    selectionIsContiguous(store.chapters, mergeIds.value),
)

const splitPreview = computed(() =>
  truncate(splitParagraph.value?.text ?? '', 180),
)

const chapterCountLabel = computed(() => formatNumber(store.chapters.length))

const currentChapterStats = computed(() => {
  if (!store.currentId) {
    return null
  }
  return analysis.chapterStatus.get(store.currentId) ?? null
})

const tabs = computed(() => [
  {
    key: 'text' as const,
    label: '原文',
    badge: '',
    alert: false,
  },
  {
    key: 'segments' as const,
    label: '片段审核',
    badge: currentChapterStats.value?.segment_count
      ? String(currentChapterStats.value.segment_count)
      : '',
    alert: Boolean(currentChapterStats.value?.review_count),
  },
  {
    key: 'characters' as const,
    label: '角色关系',
    badge: analysis.activeCharacters.length
      ? String(analysis.activeCharacters.length)
      : '',
    alert: analysis.pendingCharacters > 0,
  },
  {
    key: 'dubbing' as const,
    label: '配音生成',
    badge: '',
    alert: false,
  },
])

const modeBadge = computed(() => {
  if (system.mockMode === null) {
    return '检测中'
  }
  return system.mockMode ? '模拟模式' : 'Ollama + 本地语音'
})

function pad(sequence: number): string {
  return String(sequence).padStart(2, '0')
}

function bookStatusLabel(status: string): string {
  if (status === 'ready') {
    return '已导入'
  }
  if (status === 'empty') {
    return '空项目'
  }
  return status
}

function analysisLabel(status: string): string {
  if (status === 'not_started') {
    return '未分析'
  }
  if (status === 'completed' || status === 'done') {
    return '已分析'
  }
  if (status === 'needs_review') {
    return '待审核'
  }
  if (status === 'running') {
    return '分析中'
  }
  if (status === 'failed') {
    return '分析失败'
  }
  return status
}

function isMergeSelected(chapterId: string): boolean {
  return mergeIds.value.includes(chapterId)
}

function isHeadingParagraph(paragraph: Paragraph): boolean {
  const title = store.current?.title.trim()
  if (!title || paragraph.index !== 0) {
    return false
  }
  const text = paragraph.text.trim()
  return text === title || text.endsWith(title)
}

function paragraphTextClass(paragraph: Paragraph): string {
  if (isHeadingParagraph(paragraph)) {
    return 'nv-prose-heading font-semibold'
  }
  const text = paragraph.text.trim()
  if (/^[“「『"‘']/u.test(text)) {
    return 'nv-prose-dialogue'
  }
  return ''
}

function toggleMergeMode(): void {
  mergeMode.value = !mergeMode.value
  mergeIds.value = []
  editingTitle.value = false
}

function handleChapterSelect(chapter: ChapterSummary): void {
  if (mergeMode.value) {
    toggleMergeSelection(chapter.id)
    return
  }
  if (store.currentId !== chapter.id) {
    void store.select(chapter.id)
  }
}

function toggleMergeSelection(chapterId: string): void {
  const isSelected = mergeIds.value.includes(chapterId)
  const next = isSelected
    ? mergeIds.value.filter((id) => id !== chapterId)
    : [...mergeIds.value, chapterId]

  if (next.length > 1 && !selectionIsContiguous(store.chapters, next)) {
    message.warning('只能选择相邻章节进行合并')
    return
  }
  mergeIds.value = next
}

function selectMobileChapter(value: string | null): void {
  if (value) {
    void store.select(value)
  }
}

function confirmMerge(): void {
  if (!canMerge.value) {
    return
  }
  const selected = selectedMergeChapters.value
  const first = selected[0]
  if (!first) {
    return
  }

  dialog.warning({
    title: '合并章节',
    content: `将把所选 ${selected.length} 章合并为一章，保留第一章标题《${first.title}》。合并后可重命名。`,
    positiveText: '合并',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const chapter = await store.mergeSelected(
          selected.map((item) => item.id),
        )
        if (chapter) {
          message.success(`已合并为《${chapter.title}》`)
        }
        mergeMode.value = false
        mergeIds.value = []
      } catch (err) {
        message.error(errorMessage(err))
      }
    },
  })
}

function beginRename(): void {
  if (!store.current) {
    return
  }
  titleDraft.value = store.current.title
  editingTitle.value = true
}

function cancelRename(): void {
  editingTitle.value = false
  titleDraft.value = ''
}

async function saveRename(): Promise<void> {
  if (!store.current) {
    return
  }
  const cleaned = titleDraft.value.trim()
  if (!cleaned) {
    message.warning('章节标题不能为空')
    return
  }
  if (cleaned === store.current.title) {
    editingTitle.value = false
    return
  }

  savingTitle.value = true
  try {
    await store.renameCurrent(cleaned)
    editingTitle.value = false
    message.success('章节已重命名')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    savingTitle.value = false
  }
}

function openSplit(paragraph: Paragraph): void {
  splitParagraph.value = paragraph
  splitTitle.value = `${store.current?.title ?? '章节'}（续）`
  showSplit.value = true
}

async function confirmSplit(): Promise<void> {
  const paragraph = splitParagraph.value
  if (!paragraph) {
    return
  }
  splitting.value = true
  try {
    const chapter = await store.splitCurrent(
      paragraph.index,
      splitTitle.value.trim() || undefined,
    )
    showSplit.value = false
    splitParagraph.value = null
    if (chapter) {
      message.success(`已拆分为《${chapter.title}》`)
    }
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    splitting.value = false
  }
}

async function analyzeCurrentChapter(): Promise<void> {
  const chapterId = store.currentId
  if (!chapterId || analysis.analyzingChapter) {
    return
  }
  try {
    const result = await analysis.analyzeChapter(chapterId)
    await store.refreshChapters()
    await store.select(chapterId)
    if (activeTab.value === 'segments') {
      await analysis.loadSegments(chapterId)
    }
    if (activeTab.value === 'dubbing') {
      dubbingTick.value += 1
    }
    message.success(
      `分析完成：${result.segments_written} 个片段、${result.characters_added} 个新角色`,
    )
    if (result.chapter.analysis_status === 'needs_review') {
      message.info('存在低置信度结果，已进入审核列表')
    }
  } catch (err) {
    message.error(errorMessage(err))
    try {
      await store.refreshChapters()
      await store.select(chapterId)
    } catch {
      // 章节可能在分析期间被修改，保持当前错误提示
    }
  }
}

function retryChapter(): void {
  if (store.currentId) {
    void store.select(store.currentId)
  }
}

function refreshWorkspace(): void {
  void (async () => {
    await store.loadBook(props.bookId, store.currentId ?? undefined)
    if (!store.book) {
      return
    }
    const tasks: Array<Promise<unknown>> = [
      analysis.loadStatus(props.bookId),
      analysis.loadCharacters(props.bookId, true),
      analysis.loadRelationships(props.bookId),
    ]
    if (activeTab.value === 'segments' && store.currentId) {
      tasks.push(analysis.loadSegments(store.currentId, {}, true))
    }
    await Promise.allSettled(tasks)
  })()
}

function resetLocalState(): void {
  filter.value = ''
  mergeMode.value = false
  mergeIds.value = []
  editingTitle.value = false
  titleDraft.value = ''
  showSplit.value = false
  showAnalysisPanel.value = false
  splitParagraph.value = null
  splitTitle.value = ''
}

watch(
  () => props.bookId,
  async (bookId) => {
    resetLocalState()
    activeTab.value = 'text'
    analysis.reset()
    if (bookId) {
      await store.loadBook(bookId)
      if (store.book?.id === bookId) {
        await Promise.allSettled([
          analysis.loadStatus(bookId),
          analysis.loadCharacters(bookId),
          analysis.loadRelationships(bookId),
        ])
      }
    }
  },
  { immediate: true },
)

watch(
  () => analysis.jobFinishedTick,
  async () => {
    try {
      await store.refreshChapters()
      if (store.currentId) {
        await store.select(store.currentId)
        if (activeTab.value === 'segments') {
          await analysis.loadSegments(store.currentId, {}, true)
        }
      }
      if (activeTab.value === 'characters' && props.bookId) {
        await analysis.loadCharacters(props.bookId, true)
      }
      if (activeTab.value === 'dubbing') {
        dubbingTick.value += 1
      }
      message.success('全书分析任务已结束')
    } catch (err) {
      message.error(errorMessage(err))
    }
  },
)

watch(
  () => store.currentId,
  () => {
    editingTitle.value = false
    showSplit.value = false
    splitParagraph.value = null
  },
)

onBeforeUnmount(() => {
  store.reset()
  analysis.reset()
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header
      class="flex h-16 shrink-0 items-center gap-3 border-b border-ink-700/80 bg-ink-950/55 px-4 backdrop-blur md:px-5"
    >
      <button
        type="button"
        class="nv-focus flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] text-fog-400 transition-colors hover:bg-ink-800 hover:text-fog-200"
        @click="router.push('/library')"
      >
        <span aria-hidden="true">←</span>
        书库
      </button>
      <span class="h-4 w-px bg-ink-700" />

      <div v-if="store.book" class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="truncate font-display text-lg text-fog-100">
            {{ store.book.title }}
          </h1>
          <NTag size="small" :bordered="false" type="success">
            {{ bookStatusLabel(store.book.status) }}
          </NTag>
          <span
            v-if="system.mockMode !== null"
            class="rounded border px-1.5 py-0.5 text-[9px] tracking-wide"
            :class="
              system.mockMode
                ? 'border-brass-600/25 bg-brass-500/[0.08] text-brass-300'
                : 'border-moss-500/25 bg-moss-400/[0.08] text-moss-400'
            "
          >
            {{ modeBadge }}
          </span>
        </div>
        <p class="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] text-fog-500">
          <span>{{ store.book.author || '未署名' }}</span>
          <span class="text-fog-600">·</span>
          <span>{{ chapterCountLabel }} 章</span>
          <span class="text-fog-600">·</span>
          <span class="font-mono uppercase">{{ store.book.source_encoding || 'EMPTY' }}</span>
        </p>
      </div>
      <div v-else class="min-w-0 flex-1">
        <p class="text-xs text-fog-400">工作区</p>
      </div>

      <NButton
        size="small"
        quaternary
        :loading="store.loadingBook"
        @click="refreshWorkspace"
      >
        刷新
      </NButton>
    </header>

    <div
      v-if="store.loadingBook && !store.book"
      class="flex min-h-0 flex-1 items-center justify-center"
    >
      <div class="flex items-center gap-3 text-xs text-fog-400">
        <NSpin size="small" />
        正在打开书稿…
      </div>
    </div>

    <div
      v-else-if="!store.book"
      class="flex min-h-0 flex-1 flex-col items-center justify-center px-8 text-center"
    >
      <div class="grid h-12 w-12 place-items-center rounded-full border border-rust-400/35 bg-rust-400/10 text-rust-400">
        !
      </div>
      <h2 class="mt-4 font-display text-xl text-fog-200">无法打开这本书</h2>
      <p class="mt-2 max-w-md text-xs leading-6 text-fog-500">
        {{ store.error || '书籍不存在，或已经被移入回收站。' }}
      </p>
      <div class="mt-5 flex gap-2">
        <NButton @click="router.push('/library')">返回书库</NButton>
        <NButton type="primary" @click="refreshWorkspace">重新加载</NButton>
      </div>
    </div>

    <div v-else class="flex min-h-0 flex-1 flex-col">
      <div
        class="flex h-11 shrink-0 items-center gap-1 overflow-x-auto border-b border-ink-700/80 bg-ink-950/40 px-2 md:px-4"
      >
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="nv-focus relative flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-[11px] transition-colors"
          :class="
            activeTab === tab.key
              ? 'bg-ink-800 text-fog-100'
              : 'text-fog-500 hover:bg-ink-850 hover:text-fog-200'
          "
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
          <span
            v-if="tab.badge"
            class="rounded bg-ink-700/80 px-1 py-0.5 font-mono text-[8px]"
            :class="tab.alert ? 'text-brass-300' : 'text-fog-400'"
          >
            {{ tab.badge }}
          </span>
          <span
            v-if="tab.alert"
            class="h-1 w-1 rounded-full bg-brass-400"
            title="有待审核内容"
          />
        </button>

        <div class="ml-auto flex shrink-0 items-center gap-2 pl-2">
          <span
            v-if="analysis.jobActive"
            class="hidden items-center gap-1.5 text-[9px] text-brass-300 sm:flex"
          >
            <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-brass-400" />
            全书分析
            {{ analysis.currentJob?.completed_items }}/{{ analysis.currentJob?.total_items }}
          </span>
          <NButton
            size="tiny"
            quaternary
            @click="showAnalysisPanel = true"
          >
            AI 分析
          </NButton>
          <NButton
            size="tiny"
            :loading="analysis.analyzingChapter"
            :disabled="!store.currentId || analysis.jobActive"
            :title="analysis.jobActive ? '全书分析进行中，完成或取消后可重跑本章' : ''"
            @click="analyzeCurrentChapter"
          >
            {{ currentChapterStats?.segment_count ? '重跑本章' : '分析本章' }}
          </NButton>
        </div>
      </div>

      <div class="flex min-h-0 flex-1">
      <aside
        class="hidden w-[288px] shrink-0 flex-col border-r border-ink-700/70 bg-ink-900/35 md:flex"
      >
        <div class="shrink-0 border-b border-ink-700/70 p-3">
          <div class="flex items-center justify-between gap-2">
            <p class="eyebrow">章节 · {{ store.chapters.length }}</p>
            <NButton
              size="tiny"
              :type="mergeMode ? 'primary' : 'default'"
              :secondary="!mergeMode"
              @click="toggleMergeMode"
            >
              {{ mergeMode ? '退出合并' : '合并章节' }}
            </NButton>
          </div>
          <NInput
            v-model:value="filter"
            size="small"
            clearable
            placeholder="筛选章节标题"
            class="mt-3"
          />
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto p-2">
          <button
            v-for="chapter in filteredChapters"
            :key="chapter.id"
            type="button"
            class="nv-focus group relative mb-0.5 flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors"
            :class="
              mergeMode
                ? isMergeSelected(chapter.id)
                  ? 'bg-brass-500/10 text-fog-100'
                  : 'text-fog-400 hover:bg-ink-850'
                : store.currentId === chapter.id
                  ? 'bg-ink-800 text-fog-100'
                  : 'text-fog-400 hover:bg-ink-850 hover:text-fog-200'
            "
            @click="handleChapterSelect(chapter)"
          >
            <span
              v-if="mergeMode"
              class="mt-[3px] grid h-3.5 w-3.5 shrink-0 place-items-center rounded-[3px] border"
              :class="
                isMergeSelected(chapter.id)
                  ? 'border-brass-400 bg-brass-500 text-ink-950'
                  : 'border-ink-600'
              "
            >
              <svg
                v-if="isMergeSelected(chapter.id)"
                width="9"
                height="9"
                viewBox="0 0 10 10"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="m2 5 2 2 4-4" />
              </svg>
            </span>
            <span
              v-else
              class="mt-0.5 w-5 shrink-0 text-right font-mono text-[9px]"
              :class="store.currentId === chapter.id ? 'text-brass-400' : 'text-fog-600'"
            >
              {{ pad(chapter.sequence) }}
            </span>

            <span class="min-w-0 flex-1">
              <span class="block truncate text-[12px] leading-5">{{ chapter.title }}</span>
              <span class="mt-0.5 flex items-center gap-1.5 text-[9px] text-fog-500">
                <span>{{ formatNumber(chapter.char_count) }} 字</span>
                <span class="text-fog-600">·</span>
                <span>{{ analysisLabel(chapter.analysis_status) }}</span>
              </span>
            </span>

            <span
              v-if="!mergeMode && store.currentId === chapter.id"
              class="absolute bottom-2 left-0 top-2 w-[2px] rounded-full bg-brass-400"
            />
          </button>

          <p v-if="filteredChapters.length === 0" class="px-3 py-6 text-center text-[11px] text-fog-500">
            没有匹配的章节
          </p>
        </div>

        <div v-if="mergeMode" class="shrink-0 border-t border-ink-700/80 bg-ink-900/60 p-3">
          <p class="text-[10px] text-fog-400">
            已选 {{ mergeIds.length }} 章
            <span v-if="mergeIds.length >= 2 && !canMerge" class="text-rust-400">
              · 只能合并相邻章节
            </span>
          </p>
          <div class="mt-2 flex gap-2">
            <NButton size="small" class="flex-1" @click="toggleMergeMode">退出</NButton>
            <NButton
              size="small"
              type="primary"
              class="flex-1"
              :disabled="!canMerge"
              @click="confirmMerge"
            >
              合并所选
            </NButton>
          </div>
        </div>
      </aside>

      <section class="flex min-w-0 flex-1 flex-col">
        <div class="flex shrink-0 items-center gap-2 border-b border-ink-700/70 bg-ink-900/30 px-3 py-2 md:hidden">
          <NSelect
            size="small"
            :value="store.currentId"
            :options="chapterOptions"
            placeholder="选择章节"
            class="min-w-0 flex-1"
            @update:value="selectMobileChapter"
          />
          <NButton
            size="small"
            :type="mergeMode ? 'primary' : 'default'"
            :secondary="!mergeMode"
            @click="toggleMergeMode"
          >
            {{ mergeMode ? '退出' : '合并' }}
          </NButton>
        </div>

        <div v-if="activeTab === 'text'" class="min-h-0 flex-1 overflow-y-auto">
          <div class="mx-auto w-full max-w-3xl px-5 py-7 md:px-8">
            <NAlert v-if="store.error" type="error" :bordered="false" class="mb-4">
              <div class="flex items-center justify-between gap-4">
                <span class="text-xs">{{ store.error }}</span>
                <NButton v-if="store.currentId" size="tiny" quaternary @click="retryChapter">
                  重试本章
                </NButton>
              </div>
            </NAlert>

            <div
              v-if="store.loadingChapter"
              class="flex min-h-[280px] items-center justify-center gap-3 text-xs text-fog-400"
            >
              <NSpin size="small" />
              正在加载章节…
            </div>

            <div
              v-else-if="!store.current"
              class="flex min-h-[360px] flex-col items-center justify-center text-center"
            >
              <div class="grid h-12 w-12 place-items-center rounded-full border border-ink-600 text-brass-400/80">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v18H6.5A1.5 1.5 0 0 1 5 19.5v-15Z" />
                  <path d="M8.5 3v18" />
                </svg>
              </div>
              <h2 class="mt-4 font-display text-xl text-fog-200">
                {{ store.chapters.length ? '请选择一个章节' : '空项目还没有章节' }}
              </h2>
              <p class="mt-2 max-w-md text-xs leading-6 text-fog-500">
                {{
                  store.chapters.length
                    ? '从左侧章节列表或上方下拉框选择。'
                    : '阶段 1 通过 TXT 导入章节；空项目暂时只保留书目信息。返回书库可重新导入。'
                }}
              </p>
              <NButton class="mt-5" @click="router.push('/library')">返回书库</NButton>
            </div>

            <template v-else>
              <div class="flex items-start justify-between gap-4">
                <div class="min-w-0 flex-1">
                  <p class="eyebrow">CHAPTER {{ pad(store.current.sequence) }}</p>

                  <div v-if="editingTitle" class="mt-2 flex flex-wrap items-center gap-2">
                    <NInput
                      v-model:value="titleDraft"
                      size="small"
                      :maxlength="255"
                      class="max-w-sm"
                      @keyup.enter="saveRename"
                    />
                    <NButton size="small" type="primary" :loading="savingTitle" @click="saveRename">
                      保存
                    </NButton>
                    <NButton size="small" quaternary :disabled="savingTitle" @click="cancelRename">
                      取消
                    </NButton>
                  </div>
                  <div v-else class="mt-2 flex flex-wrap items-baseline gap-3">
                    <h2 class="font-display text-2xl leading-snug text-fog-100">
                      {{ store.current.title }}
                    </h2>
                    <button
                      type="button"
                      class="nv-focus rounded px-1 text-[10px] tracking-wide text-fog-500 transition-colors hover:text-brass-300"
                      @click="beginRename"
                    >
                      重命名
                    </button>
                  </div>
                </div>

                <div class="hidden shrink-0 flex-col items-end gap-1.5 sm:flex">
                  <NTag size="small" :bordered="false">
                    {{ analysisLabel(store.current.analysis_status) }}
                  </NTag>
                  <span class="font-mono text-[10px] text-fog-500">
                    {{ formatNumber(store.current.char_count) }} 字
                  </span>
                </div>
              </div>

              <div class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-ink-700/70 py-2 text-[10px] text-fog-500">
                <span>{{ paragraphs.length }} 段</span>
                <span>{{ dialogueCount }} 段对白</span>
                <span>更新于 {{ formatDateTime(store.current.updated_at) }}</span>
                <span v-if="mergeMode" class="text-brass-400">
                  合并模式：在左侧选择相邻章节
                </span>
              </div>

              <article class="mt-5 space-y-0.5">
                <div
                  v-for="paragraph in paragraphs"
                  :key="paragraph.index"
                  class="group flex gap-2 rounded-lg px-1.5 py-1.5 transition-colors hover:bg-ink-850/45 sm:gap-3"
                >
                  <span class="w-5 shrink-0 select-none pt-2 text-right font-mono text-[9px] text-fog-600 sm:w-6">
                    {{ String(paragraph.index + 1).padStart(2, '0') }}
                  </span>
                  <p
                    class="nv-prose min-w-0 flex-1 whitespace-pre-wrap break-words"
                    :class="paragraphTextClass(paragraph)"
                  >
                    {{ paragraph.text }}
                  </p>
                  <div class="w-9 shrink-0 pt-1.5 text-right sm:w-12">
                    <button
                      v-if="paragraph.index >= 1 && !isHeadingParagraph(paragraph)"
                      type="button"
                      class="nv-focus rounded border border-transparent px-1 py-1 text-[9px] text-fog-600 opacity-100 transition-all hover:border-ink-600 hover:text-brass-300 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100"
                      @click="openSplit(paragraph)"
                    >
                      拆分
                    </button>
                  </div>
                </div>
              </article>

              <p
                v-if="paragraphs.length <= 1"
                class="mt-6 rounded-lg border border-ink-700 bg-ink-850/40 px-4 py-3 text-[11px] leading-5 text-fog-500"
              >
                本章只有一个段落，无法再拆分。可以在原文中补充分段后重新导入。
              </p>

              <div class="mt-9 flex flex-wrap items-center justify-between gap-2 border-t border-ink-700/70 pt-4">
                <p class="font-mono text-[9px] text-fog-600">
                  原文偏移 {{ store.current.source_start_offset }} – {{ store.current.source_end_offset }}
                </p>
                <p class="text-[10px] text-fog-500">
                  正文只读 · 可重命名 / 拆分 / 合并 · 分析不会修改原文
                </p>
              </div>
            </template>
          </div>
        </div>

        <SegmentReviewPanel
          v-else-if="activeTab === 'segments'"
          :chapter-id="store.currentId"
          @analyze="analyzeCurrentChapter"
        />
        <CharacterReviewPanel
          v-else-if="activeTab === 'characters'"
          :book-id="props.bookId"
          @analyze="analyzeCurrentChapter"
        />
        <DubbingPanel
          v-else
          :key="`${store.currentId ?? 'none'}:${dubbingTick}`"
          :book-id="props.bookId"
          :chapter-id="store.currentId"
          @analyze="analyzeCurrentChapter"
        />
      </section>

      <aside
        v-if="activeTab === 'text'"
        class="hidden w-[300px] shrink-0 flex-col overflow-y-auto border-l border-ink-700/70 bg-ink-900/35 xl:flex"
      >
        <div class="p-5">
          <p class="eyebrow">BOOK / 书稿</p>
          <h3 class="mt-3 font-display text-lg leading-6 text-fog-100">
            {{ store.book.title }}
          </h3>
          <p class="mt-1 text-[11px] text-fog-500">
            {{ store.book.author || '未署名' }} · {{ store.book.language }}
          </p>
          <p class="mt-3 text-[11px] leading-6 text-fog-400">
            {{ store.book.description || '暂无简介。' }}
          </p>

          <dl class="mt-5 space-y-3 border-t border-ink-700/70 pt-4 text-[11px]">
            <div class="flex items-baseline justify-between gap-3">
              <dt class="text-fog-500">状态</dt>
              <dd class="text-fog-200">{{ bookStatusLabel(store.book.status) }}</dd>
            </div>
            <div class="flex items-baseline justify-between gap-3">
              <dt class="text-fog-500">编码</dt>
              <dd class="font-mono uppercase text-fog-200">{{ store.book.source_encoding || '—' }}</dd>
            </div>
            <div class="flex items-baseline justify-between gap-3">
              <dt class="text-fog-500">总字数</dt>
              <dd class="font-mono text-fog-200">{{ formatNumber(store.book.source_char_count) }}</dd>
            </div>
            <div class="flex items-baseline justify-between gap-3">
              <dt class="text-fog-500">章节</dt>
              <dd class="font-mono text-fog-200">{{ formatNumber(store.book.chapter_count) }}</dd>
            </div>
            <div class="flex items-baseline justify-between gap-3">
              <dt class="text-fog-500">导入时间</dt>
              <dd class="text-fog-300">{{ formatDateTime(store.book.created_at) }}</dd>
            </div>
          </dl>
        </div>

        <div class="border-t border-ink-700/70 p-5">
          <p class="eyebrow">本章结构</p>
          <div class="mt-3 grid grid-cols-2 gap-2">
            <div class="rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2.5">
              <p class="font-mono text-base text-fog-100">{{ paragraphs.length }}</p>
              <p class="mt-0.5 text-[9px] text-fog-500">段落</p>
            </div>
            <div class="rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2.5">
              <p class="font-mono text-base text-fog-100">{{ dialogueCount }}</p>
              <p class="mt-0.5 text-[9px] text-fog-500">对白段</p>
            </div>
            <div class="col-span-2 rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2.5">
              <div class="flex items-baseline justify-between gap-3">
                <span class="text-[9px] text-fog-500">分析状态</span>
                <span class="text-[11px] text-fog-300">
                  {{ store.current ? analysisLabel(store.current.analysis_status) : '—' }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="store.book.import_warnings.length" class="border-t border-ink-700/70 p-5">
          <p class="eyebrow">导入提示</p>
          <ul class="mt-3 max-h-40 space-y-2 overflow-y-auto pr-1 text-[10px] leading-5 text-fog-400">
            <li
              v-for="warning in store.book.import_warnings"
              :key="warning"
              class="flex gap-2"
            >
              <span class="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-brass-500/70" />
              <span>{{ warning }}</span>
            </li>
          </ul>
        </div>

        <div class="mt-auto space-y-3 border-t border-ink-700/70 p-5">
          <AnalysisSummary :book-id="props.bookId" />
          <p class="flex items-center gap-1.5 text-[9px] text-fog-600">
            <span
              class="h-1.5 w-1.5 rounded-full"
              :class="system.mockMode === false ? 'bg-moss-400' : 'bg-brass-400'"
            />
            {{ modeBadge }} · v{{ system.version }}
          </p>
        </div>
      </aside>
      </div>
    </div>

    <NModal
      v-model:show="showSplit"
      preset="card"
      title="从段落拆分章节"
      :bordered="false"
      :mask-closable="!splitting"
      :style="{ width: '580px', maxWidth: 'calc(100vw - 32px)' }"
    >
      <div class="space-y-4">
        <p class="text-xs leading-6 text-fog-300">
          将把第
          <span class="font-mono text-brass-300">
            {{ splitParagraph ? splitParagraph.index + 1 : '—' }}
          </span>
          段及之后的内容移动到新章节，原章节保留前面的段落。
        </p>

        <div class="max-h-32 overflow-y-auto rounded-lg border border-ink-700 bg-ink-900/70 px-3 py-2.5">
          <p class="whitespace-pre-wrap font-display text-[13px] leading-6 text-fog-300">
            {{ splitPreview || '（空段落）' }}
          </p>
        </div>

        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">新章节标题</span>
          <NInput
            v-model:value="splitTitle"
            :maxlength="255"
            placeholder="留空则自动使用“原标题（续）”"
            @keyup.enter="confirmSplit"
          />
        </label>

        <p class="text-[10px] leading-5 text-fog-500">
          拆分后章节序号会自动顺延；若章节已有生成片段或音频，后端会拒绝此操作。
        </p>
      </div>

      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <NButton :disabled="splitting" @click="showSplit = false">取消</NButton>
          <NButton type="primary" :loading="splitting" @click="confirmSplit">
            拆分章节
          </NButton>
        </div>
      </template>
    </NModal>

    <NDrawer
      v-model:show="showAnalysisPanel"
      placement="right"
      :width="360"
    >
      <NDrawerContent title="AI 分析与全书任务" closable :native-scrollbar="false">
        <AnalysisSummary :book-id="props.bookId" />
      </NDrawerContent>
    </NDrawer>
  </div>
</template>