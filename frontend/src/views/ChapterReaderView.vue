<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ChapterSourceReader from '@/components/production/ChapterSourceReader.vue'

const props = defineProps<{ bookId: string; chapterId: string }>()
const route = useRoute()
const router = useRouter()
const currentChapterId = ref(props.chapterId)
const target = ref<{ quote?: string; start?: number; end?: number }>({})

function syncTarget(): void {
  const quote = typeof route.query.quote === 'string' ? route.query.quote.slice(0, 4000) : undefined
  const rawStart = Number(route.query.start)
  const rawEnd = Number(route.query.end)
  target.value = {
    ...(quote ? { quote } : {}),
    ...(Number.isInteger(rawStart) && rawStart >= 0 ? { start: rawStart } : {}),
    ...(Number.isInteger(rawEnd) && rawEnd > rawStart ? { end: rawEnd } : {}),
  }
}

async function openChapter(chapterId: string): Promise<void> {
  currentChapterId.value = chapterId
  target.value = {}
  await router.push({
    name: 'chapter-reader',
    params: { bookId: props.bookId, chapterId },
    query: {},
  })
}

function back(): void {
  void router.push({
    name: 'production',
    params: { bookId: props.bookId },
    query: { tab: route.query.from === 'reviews' ? 'reviews' : 'chapters' },
  })
}

const readerProps = computed(() => ({ ...target.value }))
syncTarget()
watch(() => route.query, syncTarget)
watch(() => props.chapterId, (chapterId) => {
  if (chapterId !== currentChapterId.value) currentChapterId.value = chapterId
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col overflow-hidden bg-ink-950/30 p-4 md:p-6">
    <ChapterSourceReader
      :book-id="bookId"
      :chapter-id="currentChapterId"
      v-bind="readerProps"
      @update:chapter-id="openChapter"
      @back="back"
    />
  </div>
</template>
