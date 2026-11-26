<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NInput,
  NSelect,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import type { VoiceActorOut } from '@/api/types'
import ActorEditModal from '@/components/actors/ActorEditModal.vue'
import { useActorStore } from '@/stores/actors'
import { useSystemStore } from '@/stores/system'
import { AGE_LABELS, GENDER_LABELS, labelOf } from '@/utils/analysis'
import { ACTOR_STATUS_LABELS } from '@/utils/production'
import { errorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'

const actors = useActorStore()
const system = useSystemStore()
const dialog = useDialog()
const message = useMessage()

const search = ref('')
const favoritesOnly = ref(false)
const statusFilter = ref('')
const showEditor = ref(false)
const editingActor = ref<VoiceActorOut | null>(null)

let searchTimer: ReturnType<typeof setTimeout> | null = null

const statusOptions = [
  { label: '全部可用', value: '' },
  { label: '已归档', value: 'archived' },
]

const hasActors = computed(() => actors.items.length > 0)
const emptyTitle = computed(() =>
  search.value.trim() ? '没有匹配的配音员' : '配音员库还是空的',
)

function currentFilters() {
  return {
    q: search.value.trim(),
    favorite: favoritesOnly.value ? true : undefined,
    status: statusFilter.value || undefined,
    limit: actors.query.limit ?? 100,
    offset: 0,
  }
}

async function reload(): Promise<void> {
  try {
    await actors.load(currentFilters())
  } catch (err) {
    message.error(errorMessage(err))
  }
}

function scheduleSearch(): void {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    void reload()
  }, 300)
}

function openCreate(): void {
  editingActor.value = null
  showEditor.value = true
}

function openEdit(actor: VoiceActorOut): void {
  editingActor.value = actor
  showEditor.value = true
}

async function onSaved(): Promise<void> {
  await reload()
}

async function toggleFavorite(actor: VoiceActorOut): Promise<void> {
  try {
    await actors.toggleFavorite(actor)
    message.success(actor.is_favorite ? '已取消收藏' : '已加入收藏')
  } catch (err) {
    message.error(errorMessage(err))
  }
}

function archive(actor: VoiceActorOut): void {
  if (actor.status === 'archived') {
    void updateStatus(actor, 'ready')
    return
  }
  dialog.warning({
    title: '归档配音员',
    content: `归档“${actor.display_name}”后，它不会再出现在选角列表。仍被角色使用时无法归档。`,
    positiveText: '归档',
    negativeText: '取消',
    onPositiveClick: () => updateStatus(actor, 'archived'),
  })
}

async function updateStatus(
  actor: VoiceActorOut,
  status: 'ready' | 'archived',
): Promise<void> {
  try {
    await actors.update(actor.id, { status })
    message.success(status === 'archived' ? '已归档' : '已恢复')
    await reload()
  } catch (err) {
    message.error(errorMessage(err))
  }
}

async function loadMore(): Promise<void> {
  try {
    await actors.load({
      ...currentFilters(),
      limit: Math.min(200, (actors.query.limit ?? 100) + 100),
    })
  } catch (err) {
    message.error(errorMessage(err))
  }
}

function actorMeta(actor: VoiceActorOut): string {
  return `${labelOf(GENDER_LABELS, actor.gender_presentation)} · ${labelOf(
    AGE_LABELS,
    actor.age_impression,
  )} · ${actor.language}`
}

onMounted(() => {
  void reload()
  void system.refresh()
})

onBeforeUnmount(() => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
})
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <header class="shrink-0 border-b border-ink-700/80 bg-ink-950/40 px-5 pb-5 pt-7 backdrop-blur md:px-8">
      <div class="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p class="eyebrow">VOICE ACTORS / 声音库</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <h1 class="font-display text-3xl text-fog-100">全局配音员库</h1>
            <NTag v-if="system.mockMode" size="small" :bordered="false" type="warning">
              模拟模式 · 测试音
            </NTag>
          </div>
          <p class="mt-2 max-w-2xl text-[12px] leading-6 text-fog-400">
            配音员属于整个应用，不绑定单本书。每本书的旁白与角色从同一个声音库中挑选，
            这样同一角色可以跨章节保持声音一致。
          </p>
        </div>
        <div class="flex items-center gap-2">
          <NButton secondary @click="reload">刷新</NButton>
          <NButton type="primary" @click="openCreate">新建配音员</NButton>
        </div>
      </div>

      <div class="mt-6 flex flex-wrap items-center gap-3 border-y border-ink-700/70 py-3">
        <NInput
          v-model:value="search"
          clearable
          placeholder="搜索名字 / 描述 / 标签"
          class="w-64"
          @update:value="scheduleSearch"
          @keyup.enter="reload"
        />
        <NSelect
          v-model:value="statusFilter"
          :options="statusOptions"
          class="w-36"
          size="small"
          @update:value="reload"
        />
        <NCheckbox v-model:checked="favoritesOnly" @update:checked="reload">
          <span class="text-[11px] text-fog-400">只看收藏</span>
        </NCheckbox>
        <span class="text-[10px] text-fog-500">
          {{ actors.total }} 位配音员
        </span>
        <span class="ml-auto text-[10px] text-fog-500">
          参考音保存在本机 data/voice_actors/
        </span>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-6xl px-5 py-6 md:px-8">
        <NAlert v-if="system.mockMode" type="warning" :bordered="false" class="mb-4">
          <p class="text-[11px] leading-5">
            当前为模拟模式：新建配音员会生成测试音，用来验证流程。切换到真实模式后，
            参考音由本机 Qwen3-TTS / CosyVoice3 引擎生成。
          </p>
        </NAlert>
        <NAlert v-if="actors.error" type="error" :bordered="false" class="mb-4">
          {{ actors.error }}
        </NAlert>

        <div v-if="actors.loading && !hasActors" class="grid gap-4 sm:grid-cols-2">
          <div
            v-for="index in 4"
            :key="index"
            class="h-56 animate-pulse rounded-xl border border-ink-700/70 bg-ink-850/60"
          />
        </div>

        <div
          v-else-if="!hasActors"
          class="flex min-h-[360px] flex-col items-center justify-center rounded-2xl border border-dashed border-ink-700 bg-ink-900/40 px-8 text-center"
        >
          <div class="grid h-14 w-14 place-items-center rounded-full border border-ink-600 text-brass-400/80">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4 12h2l1.5-5L10 17l2-9 1.5 5H20" />
            </svg>
          </div>
          <h2 class="mt-4 font-display text-xl text-fog-200">{{ emptyTitle }}</h2>
          <p class="mt-2 max-w-sm text-[11px] leading-6 text-fog-500">
            写下声音描述，让本机声音设计生成参考音。之后可以在每本书的选角中反复使用。
          </p>
          <NButton v-if="!search.trim()" class="mt-5" type="primary" @click="openCreate">
            新建第一位配音员
          </NButton>
        </div>

        <div v-else class="grid items-start gap-4 xl:grid-cols-2">
          <article
            v-for="actor in actors.items"
            :key="actor.id"
            class="nv-card rounded-xl px-4 py-4"
          >
            <div class="flex items-start gap-3">
              <div
                class="grid h-11 w-11 shrink-0 place-items-center rounded-full border font-display text-base"
                :class="
                  actor.status === 'archived'
                    ? 'border-ink-600 bg-ink-800 text-fog-500'
                    : 'border-brass-600/40 bg-brass-500/[0.08] text-brass-300'
                "
              >
                {{ actor.display_name.slice(0, 1) }}
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <h3 class="truncate font-display text-base text-fog-100">
                    {{ actor.display_name }}
                  </h3>
                  <NTag v-if="actor.is_favorite" size="tiny" :bordered="false" type="success">
                    收藏
                  </NTag>
                  <NTag v-if="actor.is_mock" size="tiny" :bordered="false" type="warning">
                    MOCK
                  </NTag>
                  <NTag v-if="actor.status === 'archived'" size="tiny" :bordered="false">
                    已归档
                  </NTag>
                  <NTag
                    v-else-if="actor.status !== 'ready'"
                    size="tiny"
                    :bordered="false"
                    type="error"
                  >
                    {{ ACTOR_STATUS_LABELS[actor.status] ?? actor.status }}
                  </NTag>
                </div>
                <p class="mt-1 text-[10px] text-fog-500">{{ actorMeta(actor) }}</p>
                <p class="mt-1.5 line-clamp-2 text-[11px] leading-5 text-fog-400">
                  {{ actor.description || '暂无描述' }}
                </p>
              </div>
              <div class="flex shrink-0 flex-col items-end gap-1">
                <NButton size="tiny" quaternary @click="toggleFavorite(actor)">
                  {{ actor.is_favorite ? '取消收藏' : '收藏' }}
                </NButton>
                <NButton size="tiny" quaternary @click="openEdit(actor)">编辑</NButton>
              </div>
            </div>

            <div class="mt-2 flex flex-wrap gap-1">
              <span
                v-for="tag in [...actor.timbre_tags, ...actor.personality_tags].slice(0, 10)"
                :key="tag"
                class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-fog-400"
              >
                {{ tag }}
              </span>
            </div>

            <div class="mt-2.5">
              <audio
                v-if="actor.audio_url"
                :src="actor.audio_url"
                controls
                preload="none"
                class="h-8 w-full"
              />
              <p v-else class="rounded border border-rust-500/30 bg-rust-500/[0.06] px-2 py-1.5 text-[9px] text-rust-400">
                参考音缺失，不能用于选角。
              </p>
            </div>

            <div class="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-ink-700/70 pt-2.5 text-[9px] text-fog-500">
              <span>用于 {{ actor.usage.active_cast_count }} 个角色</span>
              <span>{{ actor.usage.book_count }} 本书</span>
              <span v-if="actor.usage.last_used_at">最近 {{ formatDateTime(actor.usage.last_used_at) }}</span>
              <span class="ml-auto font-mono">
                {{ actor.duration_ms ? `${(actor.duration_ms / 1000).toFixed(1)}s` : '—' }}
                <template v-if="actor.sample_rate"> · {{ actor.sample_rate }}Hz</template>
              </span>
            </div>

            <div class="mt-2 flex justify-end">
              <NButton size="tiny" quaternary @click="archive(actor)">
                {{ actor.status === 'archived' ? '恢复' : '归档' }}
              </NButton>
            </div>
          </article>
        </div>

        <div v-if="actors.items.length < actors.total" class="mt-5 flex justify-center">
          <NButton quaternary :loading="actors.loading" @click="loadMore">
            加载更多（{{ actors.items.length }} / {{ actors.total }}）
          </NButton>
        </div>

        <p class="mt-6 text-[10px] leading-5 text-fog-600">
          配音员参考音只在生成与试听时使用；音频二进制保存在本地磁盘，不写入 SQLite。
        </p>
      </div>
    </div>

    <ActorEditModal
      v-model:show="showEditor"
      :actor="editingActor"
      @saved="onSaved"
    />
  </div>
</template>