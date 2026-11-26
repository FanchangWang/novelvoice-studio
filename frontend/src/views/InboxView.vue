<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NSelect } from 'naive-ui'

import ReviewInbox from '@/components/production/ReviewInbox.vue'
import { useLibraryStore } from '@/stores/library'

const library = useLibraryStore()
const router = useRouter()

const bookId = ref<string | null>(null)

const activeBooks = computed(() =>
  library.books.filter((book) => book.status !== 'deleted'),
)

const bookOptions = computed(() => [
  { label: '全部书籍', value: '' },
  ...activeBooks.value.map((book) => ({
    label: `${book.title} · ${book.chapter_count} 章`,
    value: book.id,
  })),
])

function openBook(target: string): void {
  void router.push({ name: 'production', params: { bookId: target } })
}

onMounted(() => {
  if (!library.books.length) {
    void library.fetchBooks()
  }
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header class="shrink-0 border-b border-ink-700/80 bg-ink-950/40 px-5 pb-5 pt-7 backdrop-blur md:px-8">
      <div class="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p class="eyebrow">INBOX / 异常收件箱</p>
          <h1 class="mt-2 font-display text-3xl text-fog-100">需要你判断的节点</h1>
          <p class="mt-2 max-w-2xl text-[12px] leading-6 text-fog-400">
            设定集、分场、剧本与选角的异常会集中在这里。接受表示保留当前结果并关闭该项，
            忽略需要写下原因。阻断项会挡住后续制作。
          </p>
        </div>
        <div class="flex items-center gap-3">
          <NSelect
            :value="bookId ?? ''"
            :options="bookOptions"
            class="w-60"
            size="small"
            placeholder="全部书籍"
            @update:value="bookId = $event || null"
          />
          <NButton size="small" secondary @click="router.push('/library')">返回书库</NButton>
        </div>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-5xl px-5 py-6 md:px-8">
        <ReviewInbox
          :book-id="bookId"
          heading="全部异常"
          hint="不选书籍时显示整个应用的待办。点击书名可以进入对应制作台。"
          @open-book="openBook"
        />
      </div>
    </div>
  </div>
</template>