<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  NAlert,
  NButton,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import type { BookSummary } from '@/api/types'
import { useLibraryStore } from '@/stores/library'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, formatNumber } from '@/utils/format'

const library = useLibraryStore()
const dialog = useDialog()
const message = useMessage()
const busyId = ref<string | null>(null)

const books = computed(() => library.deletedBooks)

async function handleRestore(book: BookSummary): Promise<void> {
  busyId.value = book.id
  try {
    await library.restoreBook(book.id)
    message.success(`《${book.title}》已恢复到书库`)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    busyId.value = null
  }
}

function handlePermanentDelete(book: BookSummary): void {
  dialog.error({
    title: '彻底删除书籍',
    content: `将永久删除《${book.title}》的数据库记录、章节、分析结果、音频和项目目录。此操作无法恢复。`,
    positiveText: '永久删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      busyId.value = book.id
      try {
        await library.permanentDeleteBook(book.id)
        message.success(`《${book.title}》已彻底删除`)
      } catch (err) {
        message.error(errorMessage(err))
      } finally {
        busyId.value = null
      }
    },
  })
}

onMounted(() => {
  void library.fetchTrash()
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header class="shrink-0 px-8 pb-5 pt-8">
      <div class="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p class="eyebrow">TRASH / 回收站</p>
          <h1 class="mt-3 font-display text-3xl leading-tight text-fog-100">回收站</h1>
          <p class="mt-2 max-w-xl text-sm leading-6 text-fog-400">
            回收站中的书籍不会出现在书库，也不会继续参与制作。恢复后沿用原书籍 ID 和全部项目数据。
          </p>
        </div>
        <RouterLink
          to="/library"
          class="nv-focus inline-flex items-center rounded-lg border border-ink-700 px-3 py-2 text-xs text-fog-300 transition-colors hover:border-ink-600 hover:text-fog-100"
        >
          返回书库
        </RouterLink>
      </div>
      <div class="mt-7 border-y border-ink-700/70 py-3 text-xs text-fog-500">
        共 {{ formatNumber(books.length) }} 本已删除书籍
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto px-8 pb-12">
      <NAlert v-if="library.error" type="error" :bordered="false" class="mb-4">
        <div class="flex items-center justify-between gap-4">
          <span class="text-xs">{{ library.error }}</span>
          <NButton size="tiny" quaternary @click="library.fetchTrash()">重试</NButton>
        </div>
      </NAlert>

      <div v-if="library.trashLoading && books.length === 0" class="space-y-3">
        <div v-for="index in 4" :key="index" class="h-24 animate-pulse rounded-xl border border-ink-700/70 bg-ink-850/60" />
      </div>

      <div
        v-else-if="books.length === 0"
        class="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-dashed border-ink-700 bg-ink-900/40 px-8 text-center"
      >
        <div class="grid h-14 w-14 place-items-center rounded-full border border-ink-600 text-fog-500">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M4 7h16" /><path d="M9 7V4h6v3" /><path d="m6 7 1 13h10l1-13" /><path d="M10 11v5M14 11v5" />
          </svg>
        </div>
        <h2 class="mt-4 font-display text-xl text-fog-200">回收站是空的</h2>
        <p class="mt-2 text-xs leading-6 text-fog-500">从书库移入回收站的书籍会显示在这里。</p>
        <RouterLink to="/library" class="mt-5 text-xs text-brass-400 hover:text-brass-300">返回书库</RouterLink>
      </div>

      <ul v-else class="space-y-3">
        <li
          v-for="book in books"
          :key="book.id"
          class="flex flex-wrap items-center gap-4 rounded-xl border border-ink-700 bg-ink-900/50 p-4"
        >
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-mono text-[10px] text-fog-600">{{ book.id }}</span>
              <NTag type="error" size="small" :bordered="false">回收站</NTag>
            </div>
            <h2 class="mt-1.5 truncate font-display text-lg text-fog-100">{{ book.title }}</h2>
            <p class="mt-1 text-[11px] text-fog-500">
              {{ book.author || '未署名' }} · {{ formatNumber(book.chapter_count) }} 章 ·
              {{ formatNumber(book.source_char_count) }} 字 ·
              {{ book.deleted_at ? `删除于 ${formatDateTime(book.deleted_at)}` : '已删除' }}
            </p>
          </div>
          <div class="flex items-center gap-2">
            <NButton
              size="small"
              type="primary"
              secondary
              :loading="busyId === book.id"
              @click="handleRestore(book)"
            >
              恢复
            </NButton>
            <NButton
              size="small"
              tertiary
              :disabled="busyId !== null"
              @click="handlePermanentDelete(book)"
            >
              彻底删除
            </NButton>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
