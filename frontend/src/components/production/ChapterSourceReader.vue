<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NAlert, NButton, NInput, NSpin } from 'naive-ui'

import { api } from '@/api/client'
import type { BookDetail, ChapterDetail, ChapterSummary } from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { formatNumber } from '@/utils/format'
import { chapterDisplayTitle } from '@/utils/production'

const props = defineProps<{
  bookId: string
  chapterId: string
  initialQuote?: string
  initialStart?: number
  initialEnd?: number
}>()
const emit = defineEmits<{
  (event: 'update:chapterId', value: string): void
  (event: 'back'): void
}>()

const DIRECTORY_PAGE_SIZE = 100
const book = ref<BookDetail | null>(null)
const chapters = ref<ChapterSummary[]>([])
const chapter = ref<ChapterDetail | null>(null)
const loading = ref(true)
const loadingDirectory = ref(true)
const error = ref<string | null>(null)
const directoryError = ref<string | null>(null)
const keyword = ref('')
const searchInput = ref<InstanceType<typeof NInput> | null>(null)
const directoryScroll = ref<HTMLElement | null>(null)
const activeExcerpt = ref('')
let token = 0
let directoryToken = 0

const paragraphs = computed(() => chapter.value?.paragraphs ?? [])
const targetParagraph = computed(() => {
  const excerpt = activeExcerpt.value
  if (!excerpt) return null
  const direct = paragraphs.value.find((paragraph) => paragraph.text.includes(excerpt))
  if (direct) return direct
  const firstLine = excerpt.split('\n').find((line) => line.trim())?.trim() ?? ''
  return paragraphs.value.find((paragraph) => paragraph.text.includes(firstLine)) ?? null
})
const filteredChapters = computed(() => {
  const needle = keyword.value.trim().toLowerCase()
  return needle
    ? chapters.value.filter((item) => chapterDisplayTitle(item.sequence, item.title).toLowerCase().includes(needle))
    : chapters.value
})
const groupedChapters = computed(() => {
  const groups: Array<{ key: string; title: string; items: ChapterSummary[] }> = []
  for (const item of filteredChapters.value) {
    const title = item.volume_title || '默认卷'
    const last = groups[groups.length - 1]
    if (last && last.title === title) last.items.push(item)
    else groups.push({ key: `${item.volume_number}:${title}`, title, items: [item] })
  }
  return groups
})
const currentIndex = computed(() => chapters.value.findIndex((item) => item.id === props.chapterId))
const previousChapter = computed(() => currentIndex.value > 0 ? chapters.value[currentIndex.value - 1] : null)
const nextChapter = computed(() => currentIndex.value >= 0 && currentIndex.value < chapters.value.length - 1 ? chapters.value[currentIndex.value + 1] : null)
const paragraphElements = new Map<number, HTMLElement>()

function setParagraphElement(index: number, element: unknown): void {
  if (element instanceof HTMLElement) paragraphElements.set(index, element)
  else paragraphElements.delete(index)
}

async function scrollToTarget(): Promise<void> {
  await nextTick()
  const index = targetParagraph.value?.index
  if (index === undefined) {
    document.querySelector<HTMLElement>('.reader-content')?.scrollTo({ top: 0 })
    return
  }
  paragraphElements.get(index)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  searchInput.value?.focus()
}

function highlightedParts(text: string): Array<{ text: string; match: boolean }> {
  const excerpt = activeExcerpt.value
  if (!excerpt || targetParagraph.value?.index === undefined) return [{ text, match: false }]
  const at = text.indexOf(excerpt)
  if (at < 0) return [{ text, match: false }]
  return [
    { text: text.slice(0, at), match: false },
    { text: excerpt, match: true },
    { text: text.slice(at + excerpt.length), match: false },
  ]
}

function syncTargetFromProps(): void {
  const quote = (props.initialQuote ?? '').slice(0, 4000)
  const start = props.initialStart
  const end = props.initialEnd
  if (quote) {
    activeExcerpt.value = quote
  } else if (
    Number.isInteger(start) && Number.isInteger(end)
    && (start ?? -1) >= 0 && (end ?? 0) > (start ?? 0)
    && chapter.value && (end ?? 0) <= chapter.value.text.length
  ) {
    activeExcerpt.value = chapter.value.text.slice(start, end).slice(0, 4000)
  } else {
    activeExcerpt.value = ''
  }
}

async function loadDirectory(): Promise<void> {
  const current = ++directoryToken
  loadingDirectory.value = true
  directoryError.value = null
  const all: ChapterSummary[] = []
  try {
    let page = 1
    while (true) {
      const result = await api.listChapters(props.bookId, { page, pageSize: DIRECTORY_PAGE_SIZE })
      if (current !== directoryToken) return
      all.push(...result.items)
      chapters.value = [...all]
      if (result.items.length < DIRECTORY_PAGE_SIZE) break
      page += 1
    }
  } catch (err) {
    if (current === directoryToken) directoryError.value = errorMessage(err)
  } finally {
    if (current === directoryToken) loadingDirectory.value = false
  }
}

async function load(): Promise<void> {
  const current = ++token
  loading.value = true
  error.value = null
  try {
    const chapterResult = await api.getChapter(props.chapterId)
    if (chapterResult.book_id !== props.bookId) throw new Error('章节不属于当前书籍')
    if (current !== token) return
    chapter.value = chapterResult
    syncTargetFromProps()
    if (!book.value) book.value = await api.getBook(props.bookId)
    if (current !== token) return
    await scrollToTarget()
  } catch (err) {
    if (current === token) error.value = errorMessage(err)
  } finally {
    if (current === token) loading.value = false
  }
}

async function openChapter(chapterId: string): Promise<void> {
  if (chapterId === props.chapterId) return
  activeExcerpt.value = ''
  emit('update:chapterId', chapterId)
}

async function move(direction: -1 | 1): Promise<void> {
  const target = direction === -1 ? previousChapter.value : nextChapter.value
  if (target) await openChapter(target.id)
}

function keepDirectorySelectionVisible(): void {
  const active = directoryScroll.value?.querySelector<HTMLElement>('[aria-current="page"]')
  active?.scrollIntoView({ block: 'nearest' })
}

watch(() => props.chapterId, () => {
  void load()
  void nextTick(keepDirectorySelectionVisible)
}, { immediate: true })
watch(() => props.bookId, () => {
  book.value = null
  chapter.value = null
  chapters.value = []
  void loadDirectory()
}, { immediate: true })
watch([() => props.initialQuote, () => props.initialStart, () => props.initialEnd], () => {
  syncTargetFromProps()
  void scrollToTarget()
})
</script>

<template>
  <div class="flex h-full min-h-[560px] flex-col overflow-hidden rounded-xl border border-ink-700/80 bg-ink-950/30">
    <header class="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-ink-700/80 px-4 py-3 md:px-5">
      <div class="min-w-0">
        <NButton size="tiny" quaternary @click="emit('back')">← 返回章节管理</NButton>
        <h2 class="mt-1 truncate font-display text-lg text-fog-100">{{ chapter ? chapterDisplayTitle(chapter.sequence, chapter.title) : '章节原文' }}</h2>
        <p class="mt-0.5 text-[10px] text-fog-500">{{ book?.title }} · {{ chapter?.volume_title || '默认卷' }} · {{ formatNumber(chapter?.char_count ?? 0) }} 字</p>
      </div>
      <div class="flex items-center gap-2">
        <span class="mr-1 font-mono text-[10px] text-fog-500">{{ currentIndex >= 0 ? `${currentIndex + 1} / ${chapters.length}` : '目录读取中' }}</span>
        <NButton size="small" :disabled="!previousChapter" @click="move(-1)">上一章</NButton>
        <NButton size="small" :disabled="!nextChapter" @click="move(1)">下一章</NButton>
        <NButton size="small" :loading="loading" @click="load">刷新</NButton>
      </div>
    </header>

    <NAlert v-if="error" type="error" :bordered="false" class="mx-4 mt-3">{{ error }} <NButton size="tiny" class="ml-2" @click="load">重试</NButton></NAlert>
    <NAlert v-if="activeExcerpt && chapter && !targetParagraph" type="warning" :bordered="false" class="mx-4 mt-3">
      未在当前原文中找到完全匹配的片段；本章可能已更新，请对照异常描述检查。
    </NAlert>

    <div class="flex min-h-0 flex-1 flex-col md:flex-row">
      <aside class="flex min-h-0 shrink-0 flex-col border-b border-ink-700/80 bg-ink-900/45 md:w-60 md:border-b-0 md:border-r">
        <div class="shrink-0 border-b border-ink-700/60 p-3">
          <div class="mb-2 flex items-center justify-between">
            <p class="eyebrow">CONTENTS / 目录</p>
            <span class="text-[10px] text-fog-500">{{ chapters.length }} 章</span>
          </div>
          <NInput ref="searchInput" v-model:value="keyword" size="small" placeholder="搜索章节标题" clearable />
        </div>
        <div ref="directoryScroll" class="min-h-0 max-h-[30vh] flex-1 overflow-y-auto p-2 md:max-h-none">
          <div v-if="loadingDirectory" class="flex items-center justify-center py-8 text-xs text-fog-500"><NSpin size="small" class="mr-2" />正在读取目录…</div>
          <p v-else-if="directoryError" class="px-2 py-4 text-[11px] text-rust-300">{{ directoryError }} <button type="button" class="nv-focus ml-1 underline" @click="loadDirectory">重试</button></p>
          <p v-else-if="!filteredChapters.length" class="px-2 py-4 text-[11px] text-fog-500">没有匹配的章节。</p>
          <section v-for="group in groupedChapters" :key="group.key" class="mb-3">
            <h3 class="px-2 py-1 text-[10px] font-medium text-fog-500">{{ group.title }}</h3>
            <button
              v-for="item in group.items"
              :key="item.id"
              type="button"
              class="nv-focus mb-0.5 w-full rounded-md px-2 py-2 text-left text-[11px] leading-5 transition-colors hover:bg-ink-800"
              :class="item.id === chapterId ? 'bg-brass-500/10 text-brass-200' : 'text-fog-400'"
              :aria-current="item.id === chapterId ? 'page' : undefined"
              @click="openChapter(item.id)"
            >{{ chapterDisplayTitle(item.sequence, item.title) }}</button>
          </section>
        </div>
      </aside>

      <main class="reader-content min-h-0 min-w-0 flex-1 overflow-y-auto scroll-smooth">
        <div v-if="loading && !chapter" class="flex items-center justify-center py-24 text-xs text-fog-400"><NSpin size="small" class="mr-2" />正在读取原文…</div>
        <article v-else-if="chapter" class="mx-auto max-w-3xl px-5 py-6 md:px-8 md:py-8">
          <p
            v-for="paragraph in paragraphs"
            :key="paragraph.index"
            :ref="(element) => setParagraphElement(paragraph.index, element)"
            :id="`source-paragraph-${paragraph.index}`"
            class="group grid scroll-mt-6 gap-2 border-b border-ink-700/30 py-3 last:border-0 md:grid-cols-[48px_1fr]"
            :class="targetParagraph?.index === paragraph.index ? 'rounded-lg bg-brass-500/[0.08]' : ''"
          >
            <span class="font-mono text-[9px] text-fog-600" :title="`原文偏移 ${paragraph.start_offset}–${paragraph.end_offset}`">{{ String(paragraph.index + 1).padStart(2, '0') }}</span>
            <span class="whitespace-pre-wrap break-words text-[13px] leading-7 text-fog-200"><template v-for="(part, partIndex) in highlightedParts(paragraph.text)" :key="partIndex"><mark v-if="part.match" class="rounded bg-brass-400/30 px-0.5 text-fog-100">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></span>
          </p>
          <div class="mt-6 flex items-center justify-between border-t border-ink-700/70 pt-4">
            <NButton size="small" :disabled="!previousChapter" @click="move(-1)">上一章</NButton>
            <NButton size="small" :disabled="!nextChapter" @click="move(1)">下一章</NButton>
          </div>
        </article>
      </main>
    </div>
  </div>
</template>
