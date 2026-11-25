<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import type { VoiceOwnerOut, VoiceProfileOut } from '@/api/types'
import VoiceDesignModal from '@/components/voice/VoiceDesignModal.vue'
import VoiceOwnerCard from '@/components/voice/VoiceOwnerCard.vue'
import VoicePreviewModal from '@/components/voice/VoicePreviewModal.vue'
import { useLibraryStore } from '@/stores/library'
import { useSystemStore } from '@/stores/system'
import { useVoiceStore } from '@/stores/voices'
import { errorMessage } from '@/utils/errors'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const library = useLibraryStore()
const voices = useVoiceStore()
const system = useSystemStore()

const activeBookId = ref<string | null>(null)
const showDesign = ref(false)
const designOwner = ref<VoiceOwnerOut | null>(null)
const showPreview = ref(false)
const previewVoice = ref<VoiceProfileOut | null>(null)

const activeBooks = computed(() =>
  library.books.filter((book) => book.status !== 'deleted'),
)

const bookOptions = computed(() =>
  activeBooks.value.map((book) => ({
    label: `${book.title} · ${book.chapter_count} 章`,
    value: book.id,
  })),
)

const activeBook = computed(
  () => activeBooks.value.find((book) => book.id === activeBookId.value) ?? null,
)

const totalOwners = computed(() => voices.owners.length)
const allBound = computed(
  () => totalOwners.value > 0 && voices.selectedOwners >= totalOwners.value,
)

function openDesign(owner: VoiceOwnerOut): void {
  designOwner.value = owner
  showDesign.value = true
}

function openPreview(profile: VoiceProfileOut): void {
  previewVoice.value = profile
  showPreview.value = true
}

async function openWorkspace(): Promise<void> {
  if (!activeBookId.value) {
    return
  }
  await router.push({
    name: 'workspace',
    params: { bookId: activeBookId.value },
  })
}

async function loadVoices(bookId: string): Promise<void> {
  try {
    await voices.load(bookId)
  } catch (err) {
    message.error(errorMessage(err))
  }
}

async function bootstrap(): Promise<void> {
  if (!library.books.length) {
    await library.fetchBooks()
  }
  if (library.error) {
    message.error(library.error)
    return
  }
  const requested = typeof route.query.bookId === 'string' ? route.query.bookId : null
  const candidate = activeBooks.value.find((book) => book.id === requested)
  activeBookId.value = candidate?.id ?? activeBooks.value[0]?.id ?? null
}

watch(activeBookId, async (bookId) => {
  designOwner.value = null
  previewVoice.value = null
  showDesign.value = false
  showPreview.value = false
  voices.reset()
  if (!bookId) {
    return
  }
  if (route.query.bookId !== bookId) {
    await router.replace({ query: { ...route.query, bookId } })
  }
  await loadVoices(bookId)
})

onMounted(() => {
  void bootstrap()
  void system.refresh()
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header
      class="flex h-16 shrink-0 items-center gap-3 border-b border-ink-700/80 bg-ink-950/55 px-4 backdrop-blur md:px-5"
    >
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="font-display text-lg text-fog-100">声音库</h1>
          <span
            v-if="system.mockMode !== null"
            class="rounded border px-1.5 py-0.5 text-[9px] tracking-wide"
            :class="
              system.mockMode
                ? 'border-brass-600/25 bg-brass-500/[0.08] text-brass-300'
                : 'border-moss-500/25 bg-moss-400/[0.08] text-moss-400'
            "
          >
            {{ system.mockMode ? '模拟模式' : 'Ollama + 本地语音' }}
          </span>
        </div>
        <p class="mt-0.5 text-[10px] text-fog-500">
          为旁白和角色设计固定音色，试听不同情绪后再绑定
        </p>
      </div>

      <NSelect
        :value="activeBookId"
        :options="bookOptions"
        placeholder="选择书籍"
        size="small"
        class="w-52 shrink-0"
        :disabled="!bookOptions.length"
        @update:value="activeBookId = $event"
      />
      <NButton size="small" quaternary :loading="voices.loading" @click="activeBookId && loadVoices(activeBookId)">
        刷新
      </NButton>
      <NButton
        size="small"
        type="primary"
        :secondary="true"
        :disabled="!activeBookId"
        @click="openWorkspace"
      >
        打开工作区
      </NButton>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-6xl px-5 py-6 md:px-8">
        <NAlert v-if="system.mockMode" type="warning" :bordered="false" class="mb-4">
          <p class="text-[11px] leading-5">
            当前为模拟模式：音色候选是测试音，不会加载模型。切换到真实模式后，请先启动
            Qwen3-TTS 与 CosyVoice3 引擎服务。
          </p>
        </NAlert>
        <NAlert v-else-if="system.mockMode === false" type="info" :bordered="false" class="mb-4">
          <p class="text-[11px] leading-5">
            真实模式：设计音色会调用本机引擎服务（127.0.0.1:8766 / 8767）。
            首次加载模型需要等待，单 GPU 下任务会串行执行。
          </p>
        </NAlert>

        <div v-if="library.loading" class="flex items-center justify-center py-24 text-xs text-fog-400">
          <NSpin size="small" class="mr-3" />
          正在读取书库…
        </div>

        <div v-else-if="!activeBooks.length" class="nv-card mt-8 rounded-xl px-6 py-16 text-center">
          <h2 class="font-display text-xl text-fog-200">还没有可用的书籍</h2>
          <p class="mt-2 text-xs text-fog-500">先从书库导入 TXT 小说，再回来设计音色。</p>
          <NButton class="mt-5" @click="router.push('/library')">返回书库</NButton>
        </div>

        <template v-else-if="activeBook">
          <NAlert v-if="voices.error" type="error" :bordered="false" class="mb-4">
            {{ voices.error }}
          </NAlert>

          <section class="nv-card mb-5 rounded-xl px-4 py-3.5">
            <div class="flex flex-wrap items-center gap-x-5 gap-y-2">
              <div class="min-w-0">
                <p class="eyebrow">CURRENT BOOK</p>
                <p class="mt-1 truncate font-display text-base text-fog-100">
                  {{ activeBook.title }}
                </p>
              </div>
              <div class="flex flex-wrap items-center gap-2 text-[10px] text-fog-500">
                <NTag size="small" :bordered="false">
                  {{ totalOwners }} 个声音位
                </NTag>
                <NTag size="small" :bordered="false" :type="allBound ? 'success' : 'warning'">
                  已绑定 {{ voices.selectedOwners }} / {{ totalOwners }}
                </NTag>
                <NTag size="small" :bordered="false">
                  {{ voices.library?.total_profiles ?? 0 }} 个候选
                </NTag>
              </div>
              <div class="ml-auto flex min-w-[180px] flex-1 items-center gap-3 sm:max-w-xs">
                <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700">
                  <div
                    class="h-full rounded-full bg-brass-400 transition-all"
                    :style="{ width: `${totalOwners ? (voices.selectedOwners / totalOwners) * 100 : 0}%` }"
                  />
                </div>
                <span class="font-mono text-[9px] text-fog-500">
                  {{ totalOwners ? Math.round((voices.selectedOwners / totalOwners) * 100) : 0 }}%
                </span>
              </div>
            </div>
            <p
              v-if="voices.library?.characters_without_voice.length"
              class="mt-2.5 border-t border-ink-700/70 pt-2.5 text-[10px] leading-5 text-brass-300/90"
            >
              尚未绑定：{{ voices.library.characters_without_voice.join('、') }}
            </p>
          </section>

          <div v-if="voices.loading && !voices.library" class="flex items-center justify-center py-20 text-xs text-fog-400">
            <NSpin size="small" class="mr-3" />
            正在读取音色库…
          </div>

          <div v-else-if="voices.library" class="grid items-start gap-4 xl:grid-cols-2">
            <VoiceOwnerCard
              v-if="voices.narrator"
              :book-id="activeBook.id"
              :owner="voices.narrator"
              @design="openDesign(voices.narrator)"
              @preview="openPreview"
            />
            <VoiceOwnerCard
              v-for="owner in voices.characters"
              :key="owner.owner_id"
              :book-id="activeBook.id"
              :owner="owner"
              @design="openDesign(owner)"
              @preview="openPreview"
            />
          </div>

          <div
            v-if="voices.library && !voices.characters.length"
            class="mt-4 rounded-xl border border-dashed border-ink-600 px-5 py-8 text-center"
          >
            <p class="text-xs text-fog-300">这本书还没有识别出角色。</p>
            <p class="mt-1 text-[10px] text-fog-500">
              先在工作区运行「分析本章」或「全书分析」，角色会自动出现在这里。
            </p>
          </div>

          <p class="mt-6 text-[10px] leading-5 text-fog-600">
            声音位来自旁白与当前有效角色；已合并角色不会单独绑定。绑定音色时会按阶段 3
            定稿参数对参考音做一次轻量去齿，不改变每次生成的后处理。
          </p>
        </template>
      </div>
    </div>

    <VoiceDesignModal
      v-if="activeBook"
      v-model:show="showDesign"
      :book-id="activeBook.id"
      :owner="designOwner"
    />
    <VoicePreviewModal v-model:show="showPreview" :voice="previewVoice" />
  </div>
</template>