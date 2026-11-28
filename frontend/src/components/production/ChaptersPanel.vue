<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NEmpty, NModal, NPagination, NSelect, NSwitch, NSpin, NTag, useMessage } from 'naive-ui'

import { api } from '@/api/client'
import type { ChapterSummary, ProductionOverviewOut } from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { CHAPTER_KIND_LABELS, CHAPTER_KIND_OPTIONS } from '@/utils/production'
import { formatNumber } from '@/utils/format'
import ChapterSourceReader from '@/components/production/ChapterSourceReader.vue'

const props = defineProps<{
  bookId: string
  overview: ProductionOverviewOut
  sourceRequest?: {
    chapterId: string
    quote?: string
    start?: number
    end?: number
  } | null
}>()
const emit = defineEmits<{
  (event: 'changed'): void
  (event: 'open-script', chapterId: string): void
  (event: 'source-closed'): void
}>()

type ChapterFilter = 'all' | ChapterSummary['chapter_kind']
const PAGE_SIZE = 20
const message = useMessage()
const filter = ref<ChapterFilter>('all')
const page = ref(1)
const items = ref<ChapterSummary[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const editing = ref<ChapterSummary | null>(null)
const chapterKind = ref<ChapterSummary['chapter_kind']>('story')
const analysisIncluded = ref(true)
const saving = ref(false)
const readingChapterId = ref<string | null>(null)
let requestToken = 0

const filters: Array<{ value: ChapterFilter; label: string }> = [
  { value: 'all', label: '全部章节' },
  { value: 'story', label: '正文' },
  { value: 'extra', label: '番外' },
  { value: 'author_note', label: '作者说明' },
  { value: 'unclassified', label: '未分类' },
]
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
const productionChapters = computed(() => new Map(
  props.overview.chapters.map((chapter) => [chapter.id, chapter]),
))

async function load(): Promise<void> {
  const token = ++requestToken
  loading.value = true
  error.value = null
  try {
    const response = await api.listChapters(props.bookId, {
      page: page.value,
      pageSize: PAGE_SIZE,
      kind: filter.value === 'all' ? undefined : filter.value,
    })
    if (token !== requestToken) return
    items.value = response.items
    total.value = response.total
  } catch (err) {
    if (token === requestToken) error.value = errorMessage(err)
  } finally {
    if (token === requestToken) loading.value = false
  }
}

function setFilter(next: ChapterFilter): void {
  if (filter.value === next) return
  filter.value = next
  page.value = 1
  void load()
}

function setPage(next: number): void {
  if (next < 1 || next > pageCount.value || next === page.value) return
  page.value = next
  void load()
}

function openSettings(chapter: ChapterSummary): void {
  editing.value = chapter
  chapterKind.value = chapter.chapter_kind === 'unclassified' ? 'story' : chapter.chapter_kind
  analysisIncluded.value = chapter.analysis_included
}

function closeSettings(): void {
  if (!saving.value) editing.value = null
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') closeSettings()
}

async function saveSettings(): Promise<void> {
  const chapter = editing.value
  if (!chapter) return
  saving.value = true
  try {
    await api.updateChapter(chapter.id, {
      chapter_kind: chapterKind.value,
      classification_status: 'confirmed',
      analysis_included: analysisIncluded.value,
    })
    message.success('章节分类已保存')
    editing.value = null
    await load()
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

function openSource(chapter: ChapterSummary): void {
  readingChapterId.value = chapter.id
}

function closeSource(): void {
  readingChapterId.value = null
  emit('source-closed')
}

const sourceTarget = computed(() => {
  const request = props.sourceRequest
  if (!request || request.chapterId !== readingChapterId.value) return {}
  return {
    initialQuote: request.quote,
    initialStart: request.start,
    initialEnd: request.end,
  }
})

function statusLabel(chapter: ChapterSummary): string {
  if (chapter.chapter_kind === 'unclassified') return '等待分类'
  if (!chapter.analysis_included) return '保留原文，不参与制作'
  const progress = productionChapters.value.get(chapter.id)
  if (!progress || progress.script_status === 'not_started') return '等待生成剧本'
  if (progress.audio_ready_units > 0 && progress.audio_ready_units >= progress.tts_unit_count) return '音频已生成'
  return progress.script_status === 'completed' ? '剧本已完成' : '制作中'
}

watch(() => props.bookId, (next, previous) => {
  page.value = 1
  readingChapterId.value = null
  if (previous && next !== previous) emit('source-closed')
  void load()
}, { immediate: true })
watch(() => props.sourceRequest, (request) => {
  if (request?.chapterId) readingChapterId.value = request.chapterId
})
watch(chapterKind, (kind) => {
  if (kind === 'unclassified') analysisIncluded.value = false
})
watch(editing, (value) => {
  document.removeEventListener('keydown', onKeydown)
  if (value) document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  requestToken += 1
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <ChapterSourceReader
    v-if="readingChapterId"
    :book-id="bookId"
    :chapter-id="readingChapterId"
    v-bind="sourceTarget"
    @update:chapter-id="readingChapterId = $event"
    @back="closeSource"
  />
  <section v-else class="nv-card overflow-hidden rounded-xl">
    <header class="flex flex-wrap items-end justify-between gap-3 border-b border-ink-700/80 px-4 py-4">
      <div>
        <p class="eyebrow">CHAPTERS / 章节</p>
        <h2 class="mt-1 font-display text-lg text-fog-100">章节管理</h2>
        <p class="mt-1 text-[11px] text-fog-500">按原书顺序保留全部章节；未分类章节不阻断其他章节的制作。</p>
      </div>
      <span class="font-mono text-[11px] text-fog-400">全书 {{ formatNumber(overview.chapter_count) }} 章</span>
    </header>

    <div class="flex flex-wrap gap-1.5 border-b border-ink-700/60 px-4 py-3" role="group" aria-label="章节类型筛选">
      <NButton
        v-for="item in filters"
        :key="item.value"
        size="tiny"
        :type="filter === item.value ? 'primary' : 'tertiary'"
        :aria-pressed="filter === item.value"
        :data-filter="item.value"
        @click="setFilter(item.value)"
      >
        {{ item.label }}
      </NButton>
    </div>

    <NAlert v-if="error" type="error" class="mx-4 mt-4">
      <div class="flex items-center justify-between gap-3">
        <span>{{ error }}</span>
        <NButton size="tiny" @click="load">重试</NButton>
      </div>
    </NAlert>

    <div v-if="loading && !items.length" class="flex items-center justify-center py-16 text-xs text-fog-400">
      <NSpin size="small" class="mr-2" /> 正在读取章节…
    </div>
    <NEmpty v-else-if="!items.length" description="当前筛选下没有章节" class="py-16" />
    <div v-else class="overflow-x-auto">
      <table class="w-full min-w-[820px] text-left text-[11px]">
        <thead class="border-b border-ink-700/70 text-[9px] uppercase tracking-[0.14em] text-fog-500">
          <tr>
            <th class="w-16 px-4 py-3 font-medium">序号</th>
            <th class="px-2 py-3 font-medium">章节</th>
            <th class="px-2 py-3 font-medium">分类</th>
            <th class="px-2 py-3 font-medium">制作状态</th>
            <th class="px-4 py-3 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-ink-700/50">
          <tr v-for="chapter in items" :key="chapter.id" class="align-top hover:bg-ink-850/50">
            <td class="px-4 py-4 font-mono text-fog-500">{{ chapter.sequence }}</td>
            <td class="max-w-[300px] px-2 py-4">
              <p class="text-fog-200">{{ chapter.title }}</p>
              <p class="mt-1 text-[9px] text-fog-600">
                {{ chapter.volume_title || '默认卷' }} · {{ formatNumber(chapter.char_count) }} 字
              </p>
              <p v-if="chapter.chapter_kind === 'unclassified'" class="mt-2 max-w-[380px] text-[10px] leading-5 text-brass-300">
                需要先确认是正文、番外还是作者说明。确认前不会生成分析、剧本或音频。
              </p>
              <p v-else-if="!chapter.analysis_included" class="mt-2 text-[10px] leading-5 text-fog-500">
                已保留在原书中，当前不参与分析。若与正文强相关，可在“分类与纳入”中开启。
              </p>
            </td>
            <td class="px-2 py-4">
              <NTag size="tiny" :bordered="false" :type="chapter.chapter_kind === 'unclassified' ? 'warning' : 'default'">
                {{ CHAPTER_KIND_LABELS[chapter.chapter_kind] ?? chapter.chapter_kind }}
              </NTag>
            </td>
            <td class="px-2 py-4 text-[10px] text-fog-400">{{ statusLabel(chapter) }}</td>
            <td class="px-4 py-4">
              <div class="flex flex-wrap justify-end gap-1">
                <NButton size="tiny" quaternary @click="openSource(chapter)">查看原文</NButton>
                <NButton size="tiny" quaternary @click="openSettings(chapter)">分类与纳入</NButton>
                <NButton
                  v-if="chapter.chapter_kind !== 'unclassified' && chapter.analysis_included"
                  size="tiny"
                  secondary
                  @click="emit('open-script', chapter.id)"
                >
                  查看剧本
                </NButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="flex flex-wrap items-center justify-between gap-3 border-t border-ink-700/70 px-4 py-3">
      <span class="text-[10px] text-fog-500">共 {{ formatNumber(total) }} 章</span>
      <NPagination :page="page" :page-count="pageCount" :page-size="PAGE_SIZE" size="small" @update:page="setPage" />
    </footer>

    <NModal
      :show="editing !== null"
      preset="card"
      title="章节分类与纳入"
      :bordered="false"
      :style="{ width: '480px', maxWidth: 'calc(100vw - 32px)' }"
      @update:show="(value) => { if (!value) closeSettings() }"
    >
      <template v-if="editing">
        <p class="text-[12px] text-fog-200">{{ editing.title }}</p>
        <p class="mt-1 text-[10px] text-fog-500">{{ editing.volume_title || '默认卷' }} · 第 {{ editing.sequence }} 章</p>
        <label class="mt-4 block text-[11px] text-fog-400" for="chapters-kind">章节分类</label>
        <NSelect id="chapters-kind" v-model:value="chapterKind" :options="CHAPTER_KIND_OPTIONS" class="mt-2" />
        <div class="mt-4 flex items-start justify-between gap-4 rounded-lg bg-ink-900/60 px-3 py-2.5">
          <div>
            <p class="text-[11px] text-fog-200">纳入分析与剧本</p>
            <p class="mt-1 text-[10px] leading-5 text-fog-500">关闭后保留原文与章节，但全书分析、剧本和音频任务会跳过它。</p>
          </div>
          <NSwitch v-model:value="analysisIncluded" size="small" :disabled="chapterKind === 'unclassified'" class="mt-1 shrink-0" />
        </div>
        <NAlert v-if="chapterKind !== 'story'" type="info" :bordered="false" class="mt-3">
          {{ CHAPTER_KIND_LABELS[chapterKind] }}会保留在原书中；只有确认与正文强相关时才建议纳入制作。
        </NAlert>
      </template>
      <template #footer>
        <div class="flex justify-end gap-2">
          <NButton :disabled="saving" @click="closeSettings">取消</NButton>
          <NButton type="primary" :loading="saving" @click="saveSettings">保存</NButton>
        </div>
      </template>
    </NModal>
  </section>
</template>
