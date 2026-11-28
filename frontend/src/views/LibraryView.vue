<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NInput,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import type { BookSummary } from '@/api/types'
import ImportBookModal from '@/components/ImportBookModal.vue'
import { useLibraryStore } from '@/stores/library'
import { useSystemStore } from '@/stores/system'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, formatNumber } from '@/utils/format'

const library = useLibraryStore()
const system = useSystemStore()
const router = useRouter()
const dialog = useDialog()
const message = useMessage()

const showImport = ref(false)
const importTarget = ref<string | null>(null)
const search = ref('')
const viewMode = ref<'cards' | 'list'>('cards')

const filteredBooks = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) {
    return library.books
  }
  return library.books.filter((book) => {
    const haystack = `${book.title} ${book.author ?? ''}`.toLowerCase()
    return haystack.includes(keyword)
  })
})

const emptyTitle = computed(() => {
  if (search.value.trim()) {
    return '没有匹配的书籍'
  }
  return '书库还是空的'
})

const emptyHint = computed(() => {
  if (search.value.trim()) {
    return '换一个关键词，或清空搜索。'
  }
  return '导入一本 TXT 小说开始制作。'
})

function statusLabel(status: string): string {
  if (status === 'ready') {
    return '已导入'
  }
  if (status === 'empty') {
    return '空项目'
  }
  return status
}

function statusTagType(status: string): 'success' | 'warning' | 'default' {
  if (status === 'ready') {
    return 'success'
  }
  if (status === 'empty') {
    return 'warning'
  }
  return 'default'
}

function openBook(book: BookSummary): void {
  void router.push({ name: 'production', params: { bookId: book.id } })
}

function openImport(book: BookSummary | null = null): void {
  importTarget.value = book?.id ?? null
  showImport.value = true
}

function handleRowKey(event: KeyboardEvent, book: BookSummary): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    openBook(book)
  }
}

function handleDelete(book: BookSummary): void {
  dialog.warning({
    title: '移入回收站',
    content: `《${book.title}》及其章节会从书库隐藏，可在独立回收站中恢复。源文件不会被删除。`,
    positiveText: '移入回收站',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await library.removeBook(book.id)
        message.success('已移入回收站')
      } catch (err) {
        message.error(errorMessage(err))
      }
    },
  })
}

function handleImported(bookId: string): void {
  void library.fetchBooks()
  void router.push({ name: 'production', params: { bookId } })
}

onMounted(() => {
  void library.fetchBooks()
  void system.refresh()
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header class="shrink-0 px-8 pb-5 pt-8">
      <div class="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p class="eyebrow">LIBRARY / 书库</p>
          <h1 class="mt-3 font-display text-3xl leading-tight text-fog-100">
            你的有声书工作台
          </h1>
          <p class="mt-2 max-w-xl text-sm leading-6 text-fog-400">
            导入小说 TXT 后进入制作台。章节、角色、剧本与音频只保存在本机。
          </p>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <span
            v-if="system.mockMode"
            class="mr-1 rounded-md border border-brass-600/30 bg-brass-500/[0.08] px-2.5 py-1.5 text-[10px] tracking-wide text-brass-300"
          >
            模拟模式 · 不调用模型
          </span>
          <NButton type="primary" @click="openImport()">导入 TXT</NButton>
        </div>
      </div>

      <div class="mt-7 flex flex-wrap items-center gap-x-8 gap-y-3 border-y border-ink-700/70 py-3">
        <div class="flex items-baseline gap-2">
          <span class="font-mono text-lg text-fog-100">{{ formatNumber(library.total) }}</span>
          <span class="text-[10px] uppercase tracking-[0.16em] text-fog-500">在库书籍</span>
        </div>
        <div class="h-4 w-px bg-ink-700" />
        <div class="flex items-baseline gap-2">
          <span class="font-mono text-lg text-fog-100">{{ formatNumber(library.totalChapters) }}</span>
          <span class="text-[10px] uppercase tracking-[0.16em] text-fog-500">章节</span>
        </div>
        <div class="h-4 w-px bg-ink-700" />
        <div class="flex items-baseline gap-2">
          <span class="font-mono text-lg text-fog-100">{{ formatNumber(library.totalCharacters) }}</span>
          <span class="text-[10px] uppercase tracking-[0.16em] text-fog-500">正文字数</span>
        </div>
        <div class="ml-auto flex items-center gap-2 text-[10px] text-fog-500">
          <span class="h-1.5 w-1.5 rounded-full bg-moss-400/80" />
          本地 SQLite · 音频不会存入数据库
        </div>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto px-8 pb-12">
      <div class="flex flex-wrap items-center justify-between gap-3 pb-4">
        <div class="flex items-center gap-3">
          <NInput
            v-model:value="search"
            clearable
            placeholder="搜索书名 / 作者"
            class="w-64"
          />
          <span class="whitespace-nowrap text-[10px] text-fog-500">
            {{ formatNumber(filteredBooks.length) }} 本
          </span>
        </div>

        <div class="flex items-center gap-3">
          <div class="flex items-center rounded-lg border border-ink-700 p-0.5" role="group" aria-label="书籍展示方式">
            <NButton
              size="tiny"
              :type="viewMode === 'cards' ? 'primary' : 'tertiary'"
              :aria-pressed="viewMode === 'cards'"
              @click="viewMode = 'cards'"
            >
              卡片
            </NButton>
            <NButton
              size="tiny"
              :type="viewMode === 'list' ? 'primary' : 'tertiary'"
              :aria-pressed="viewMode === 'list'"
              @click="viewMode = 'list'"
            >
              列表
            </NButton>
          </div>
          <RouterLink
            to="/trash"
            class="nv-focus inline-flex items-center gap-2 rounded-lg border border-ink-700 px-3 py-1.5 text-[11px] text-fog-300 transition-colors hover:border-ink-600 hover:text-fog-100"
          >
            回收站
            <span class="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-fog-400">
              {{ formatNumber(library.trashTotal) }}
            </span>
          </RouterLink>
        </div>
      </div>

      <NAlert v-if="library.error" type="error" :bordered="false" class="mb-4">
        <div class="flex items-center justify-between gap-4">
          <span class="text-xs">{{ library.error }}</span>
          <NButton size="tiny" quaternary @click="library.fetchBooks()">重试</NButton>
        </div>
      </NAlert>

      <div v-if="library.loading && library.books.length === 0" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="index in 6"
          :key="index"
          class="h-64 animate-pulse rounded-xl border border-ink-700/70 bg-ink-850/60"
        />
      </div>

      <div
        v-else-if="filteredBooks.length === 0"
        class="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-dashed border-ink-700 bg-ink-900/40 px-8 text-center"
      >
        <div class="grid h-14 w-14 place-items-center rounded-full border border-ink-600 text-brass-400/80">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v18H6.5A1.5 1.5 0 0 1 5 19.5v-15Z" />
            <path d="M8.5 3v18" />
          </svg>
        </div>
        <h2 class="mt-4 font-display text-xl text-fog-200">{{ emptyTitle }}</h2>
        <p class="mt-2 max-w-sm text-xs leading-6 text-fog-500">{{ emptyHint }}</p>
        <div v-if="!search.trim()" class="mt-5 flex gap-2">
          <NButton type="primary" @click="openImport()">导入 TXT</NButton>
        </div>
      </div>

      <div v-else-if="viewMode === 'cards'" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <article
          v-for="book in filteredBooks"
          :key="book.id"
          class="nv-card nv-card-hover group relative flex min-h-[248px] flex-col overflow-hidden rounded-xl p-5 outline-none cursor-pointer focus-visible:border-brass-500/70"
          tabindex="0"
          role="link"
          @click="openBook(book)"
          @keydown="handleRowKey($event, book)"
        >
          <div class="absolute inset-y-0 left-0 w-px bg-gradient-to-b from-transparent via-brass-600/50 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-mono text-[9px] tracking-wider text-fog-600">{{ book.id }}</span>
                <NTag :type="statusTagType(book.status)" size="small" :bordered="false">
                  {{ statusLabel(book.status) }}
                </NTag>
              </div>
              <h3 class="mt-2.5 truncate font-display text-xl text-fog-100">{{ book.title }}</h3>
              <p class="mt-1 truncate text-[11px] text-fog-500">
                {{ book.author || '未署名' }}
                <span class="mx-1 text-fog-600">·</span>
                {{ book.language }}
              </p>
            </div>
            <span class="shrink-0 rounded border border-ink-600/80 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wide text-fog-500">
              {{ book.source_encoding || 'EMPTY' }}
            </span>
          </div>

          <p class="mt-3 line-clamp-2 min-h-10 text-[11px] leading-5 text-fog-400">
            {{ book.description || (book.status === 'empty' ? '空项目，等待导入正文。' : '暂无简介。') }}
          </p>

          <div class="mt-auto grid grid-cols-2 gap-x-4 gap-y-2 border-t border-ink-700/80 pt-3 text-[10px]">
            <div class="flex items-baseline justify-between gap-2">
              <span class="text-fog-500">章节</span>
              <span class="font-mono text-fog-200">{{ formatNumber(book.chapter_count) }}</span>
            </div>
            <div class="flex items-baseline justify-between gap-2">
              <span class="text-fog-500">字数</span>
              <span class="font-mono text-fog-200">{{ formatNumber(book.source_char_count) }}</span>
            </div>
            <div class="col-span-2 flex items-baseline justify-between gap-2">
              <span class="text-fog-500">更新</span>
              <span class="text-fog-400">{{ formatDateTime(book.updated_at) }}</span>
            </div>
          </div>

          <div class="mt-3 flex items-center justify-between gap-2">
            <span class="text-[10px] text-brass-400/90">进入制作台 <span aria-hidden="true">→</span></span>
            <div class="flex items-center gap-1">
              <NButton size="tiny" secondary @click.stop="openImport(book)">更新导入</NButton>
              <NButton size="tiny" quaternary @click.stop="handleDelete(book)">移入回收站</NButton>
            </div>
          </div>
        </article>
      </div>

      <div v-else class="overflow-x-auto rounded-xl border border-ink-700 bg-ink-900/40">
        <table class="w-full min-w-[760px] text-left text-xs">
          <thead class="border-b border-ink-700 bg-ink-850/60 text-[10px] uppercase tracking-wider text-fog-500">
            <tr>
              <th scope="col" class="px-4 py-3 font-medium">书籍</th>
              <th scope="col" class="px-4 py-3 font-medium">作者</th>
              <th scope="col" class="px-4 py-3 text-right font-medium">章节</th>
              <th scope="col" class="px-4 py-3 text-right font-medium">字数</th>
              <th scope="col" class="px-4 py-3 font-medium">更新时间</th>
              <th scope="col" class="px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-ink-700/70">
            <tr
              v-for="book in filteredBooks"
              :key="book.id"
              class="cursor-pointer outline-none transition-colors hover:bg-ink-850/60 focus-visible:bg-ink-850/70"
              tabindex="0"
              @click="openBook(book)"
              @keydown="handleRowKey($event, book)"
            >
              <td class="px-4 py-3">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-[9px] text-fog-600">{{ book.id }}</span>
                  <NTag :type="statusTagType(book.status)" size="small" :bordered="false">
                    {{ statusLabel(book.status) }}
                  </NTag>
                </div>
                <p class="mt-1 max-w-xs truncate font-medium text-fog-100">{{ book.title }}</p>
              </td>
              <td class="px-4 py-3 text-fog-400">{{ book.author || '未署名' }}</td>
              <td class="px-4 py-3 text-right font-mono text-fog-300">{{ formatNumber(book.chapter_count) }}</td>
              <td class="px-4 py-3 text-right font-mono text-fog-300">{{ formatNumber(book.source_char_count) }}</td>
              <td class="whitespace-nowrap px-4 py-3 text-fog-500">{{ formatDateTime(book.updated_at) }}</td>
              <td class="px-4 py-3">
                <div class="flex justify-end gap-1">
                  <NButton size="tiny" secondary @click.stop="openImport(book)">更新导入</NButton>
                  <NButton size="tiny" quaternary @click.stop="handleDelete(book)">移入回收站</NButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ImportBookModal
      v-model:show="showImport"
      :initial-book-id="importTarget"
      @imported="handleImported"
    />
  </div>
</template>
