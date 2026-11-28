<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NInput,
  NModal,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import type { ProductionChapterOut, ReviewItemOut } from '@/api/types'
import { useReviewStore, type ReviewQuery } from '@/stores/reviews'
import { errorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'
import {
  ENTITY_TYPE_LABELS,
  REVIEW_SEVERITY_LABELS,
  REVIEW_SEVERITY_OPTIONS,
  REVIEW_STATUS_LABELS,
  REVIEW_STATUS_OPTIONS,
  severityTagType,
  statusTagType,
} from '@/utils/production'

const props = withDefaults(
  defineProps<{
    bookId?: string | null
    chapters?: ProductionChapterOut[]
    heading?: string
    hint?: string
  }>(),
  {
    bookId: null,
    chapters: () => [],
    heading: '异常收件箱',
    hint: '只处理需要你判断的节点；接受会保留当前结果并关闭该项。',
  },
)

const chapterFilter = defineModel<string | null>('chapterFilter', { default: null })
const emit = defineEmits<{
  (event: 'changed'): void
  (event: 'open-book', bookId: string): void
  (event: 'open-location', item: ReviewItemOut): void
}>()

const reviews = useReviewStore()
const message = useMessage()

const selectedIds = ref<string[]>([])
const showIgnore = ref(false)
const ignoreNote = ref('')
const pendingIds = ref<string[]>([])
const actionLoading = ref(false)
const chapterOptions = computed(() =>
  props.chapters.map((chapter) => ({
    value: chapter.id,
    label: `${chapter.volume_title || '默认卷'} · ${chapter.title}`,
  })),
)

const openItems = computed(() =>
  reviews.items.filter((item) => item.status === 'open'),
)
const actionableOpenItems = computed(() =>
  openItems.value.filter((item) => item.category === 'content' && item.actionable),
)
const allSelected = computed(
  () =>
    actionableOpenItems.value.length > 0 &&
    actionableOpenItems.value.every((item) => selectedIds.value.includes(item.id)),
)
const selectedOpenIds = computed(() =>
  selectedIds.value.filter((id) =>
    actionableOpenItems.value.some((item) => item.id === id),
  ),
)

function pruneSelection(): void {
  const openIds = new Set(openItems.value.map((item) => item.id))
  selectedIds.value = selectedIds.value.filter((id) => openIds.has(id))
}

function toggleAll(value: boolean): void {
  selectedIds.value = value ? actionableOpenItems.value.map((item) => item.id) : []
}

function toggleOne(item: ReviewItemOut, value: boolean): void {
  if (item.category !== 'content' || !item.actionable) {
    return
  }
  const set = new Set(selectedIds.value)
  if (value) {
    set.add(item.id)
  } else {
    set.delete(item.id)
  }
  selectedIds.value = [...set]
}

async function refresh(overrides: ReviewQuery = {}): Promise<void> {
  try {
    await reviews.load({
      bookId: props.bookId,
      chapterId: chapterFilter.value,
      offset: 0,
      ...overrides,
    })
    pruneSelection()
  } catch (err) {
    message.error(errorMessage(err))
  }
}

async function accept(item: ReviewItemOut): Promise<void> {
  actionLoading.value = true
  try {
    await reviews.resolve(item.id, 'accept')
    message.success('已接受该结果并关闭异常')
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

async function acceptSelected(): Promise<void> {
  if (!selectedOpenIds.value.length) {
    return
  }
  actionLoading.value = true
  try {
    const count = await reviews.batchResolve(selectedOpenIds.value, 'accept')
    selectedIds.value = []
    message.success(`已接受 ${count} 项异常`)
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function openIgnore(ids: string[]): void {
  if (!ids.length) {
    return
  }
  pendingIds.value = ids
  ignoreNote.value = ''
  showIgnore.value = true
}

async function confirmIgnore(): Promise<void> {
  const note = ignoreNote.value.trim()
  if (!note) {
    message.warning('忽略异常必须填写原因')
    return
  }
  actionLoading.value = true
  try {
    const count = await reviews.batchResolve(pendingIds.value, 'ignore', note)
    showIgnore.value = false
    pendingIds.value = []
    selectedIds.value = []
    message.success(`已忽略 ${count} 项异常`)
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function actionLabel(item: ReviewItemOut): string {
  if (item.action_type === 'script_unit') return '打开剧本问题行'
  if (item.action_type === 'character') return '查看角色与原文'
  if (item.action_type === 'chapter_classification') return '查看章节并分类'
  if (item.action_type === 'chapter_retry') return '查看原文并重试'
  return '查看原文证据'
}

function locationLabel(item: ReviewItemOut): string {
  const unit = item.location.unit_index
  const scene = item.location.scene_index
  if (typeof unit === 'number') return `剧本第 ${unit + 1} 行`
  if (typeof scene === 'number') return `场景 ${scene + 1}`
  if (item.location.character_name) return `角色：${item.location.character_name}`
  return item.chapter_title ? `章节：${item.chapter_title}` : ''
}

async function loadMore(): Promise<void> {
  const next = Math.min(250, (reviews.query.limit ?? 100) + 100)
  await refresh({ limit: next })
}

watch(
  () => chapterFilter.value,
  () => {
    selectedIds.value = []
    void refresh()
  },
)

watch(
  () => props.bookId,
  () => {
    selectedIds.value = []
    void refresh()
  },
)

watch(
  () => reviews.items,
  pruneSelection,
)

onMounted(() => {
  void refresh()
})
</script>

<template>
  <section class="flex min-h-0 flex-col">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="eyebrow">REVIEW INBOX / 异常</p>
        <h2 class="mt-2 font-display text-xl text-fog-100">{{ heading }}</h2>
        <p class="mt-1 max-w-2xl text-[11px] leading-5 text-fog-500">{{ hint }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <div class="rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-1.5 text-[10px]">
          <span class="text-fog-500">{{ reviews.query.category === 'system' ? '系统故障' : '待处理内容问题' }}</span>
          <span class="ml-2 font-mono text-fog-100">{{ reviews.query.category === 'system' ? reviews.counts.open : actionableOpenItems.length }}</span>
        </div>
        <NButton size="small" quaternary :loading="reviews.loading" @click="refresh()">
          刷新
        </NButton>
      </div>
    </header>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <NSelect
        v-if="chapterOptions.length"
        :value="chapterFilter ?? ''"
        :options="[{ value: '', label: '全部章节' }, ...chapterOptions]"
        filterable
        clearable
        size="small"
        class="w-56"
        @update:value="chapterFilter = $event || null"
      />
      <NSelect
        :value="reviews.query.category ?? 'content'"
        :options="[{ value: 'content', label: '内容异常' }, { value: 'system', label: '系统故障' }]"
        size="small"
        class="w-32"
        @update:value="selectedIds = []; refresh({ category: $event })"
      />
      <NSelect
        :value="reviews.query.status ?? 'open'"
        :options="REVIEW_STATUS_OPTIONS"
        size="small"
        class="w-32"
        @update:value="refresh({ status: $event || null })"
      />
      <NSelect
        :value="reviews.query.severity ?? ''"
        :options="REVIEW_SEVERITY_OPTIONS"
        size="small"
        class="w-32"
        @update:value="refresh({ severity: $event || null })"
      />
      <NCheckbox
        v-if="reviews.query.category !== 'system'"
        :checked="allSelected"
        :disabled="!actionableOpenItems.length"
        @update:checked="toggleAll"
      >
        <span class="text-[11px] text-fog-400">全选待处理项</span>
      </NCheckbox>
      <div v-if="reviews.query.category !== 'system'" class="ml-auto flex items-center gap-2">
        <span class="text-[10px] text-fog-500">已选 {{ selectedOpenIds.length }} 项</span>
        <NButton
          size="small"
          secondary
          :disabled="!selectedOpenIds.length || actionLoading"
          @click="acceptSelected"
        >
          批量接受
        </NButton>
        <NButton
          size="small"
          quaternary
          :disabled="!selectedOpenIds.length || actionLoading"
          @click="openIgnore(selectedOpenIds)"
        >
          批量忽略
        </NButton>
      </div>
      <p v-else class="ml-auto text-[10px] text-fog-500">系统故障由后台任务自动记录；请到总览查看任务并重试。</p>
    </div>

    <NAlert v-if="reviews.error" type="error" :bordered="false" class="mt-4">
      {{ reviews.error }}
    </NAlert>

    <div v-if="reviews.loading && !reviews.items.length" class="flex items-center justify-center py-20 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取异常…
    </div>

    <div
      v-else-if="reviews.isEmpty"
      class="mt-5 flex min-h-[220px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-6 text-center"
    >
      <p class="font-display text-lg text-fog-200">{{ reviews.query.category === 'system' ? '这个范围内没有系统故障' : '这个范围内没有待处理的内容异常' }}</p>
      <p class="mt-2 text-[11px] leading-6 text-fog-500">
        {{ reviews.query.category === 'system' ? '模型、任务和音频执行故障显示在总览的后台任务中，不会混入内容异常。' : 'AI 已自动采用的结果不会出现在这里；需要你判断的问题会附上原文或剧本位置、影响和处理建议。' }}
      </p>
    </div>

    <div v-else class="mt-4 space-y-2.5 pb-4">
      <article
        v-for="item in reviews.items"
        :key="item.id"
        class="nv-card rounded-xl px-4 py-3"
      >
        <div class="flex items-start gap-3">
          <NCheckbox
            v-if="item.status === 'open' && item.category === 'content' && item.actionable"
            :checked="selectedIds.includes(item.id)"
            class="mt-0.5"
            @update:checked="toggleOne(item, $event)"
          />
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <NTag size="tiny" :bordered="false" :type="severityTagType(item.severity)">
                {{ REVIEW_SEVERITY_LABELS[item.severity] ?? item.severity }}
              </NTag>
              <NTag
                v-if="item.category === 'system'"
                size="tiny"
                :bordered="false"
                type="error"
              >
                系统故障
              </NTag>
              <NTag
                v-else-if="item.status !== 'open'"
                size="tiny"
                :bordered="false"
                :type="statusTagType(item.status === 'resolved' ? 'approved' : 'info')"
              >
                {{ REVIEW_STATUS_LABELS[item.status] ?? item.status }}
              </NTag>
              <p class="min-w-0 flex-1 truncate text-[12px] font-medium text-fog-200">
                {{ item.title }}
              </p>
            </div>
            <p v-if="item.message" class="mt-1.5 text-[11px] leading-5 text-fog-400">
              {{ item.message }}
            </p>
            <div v-if="locationLabel(item) || item.location.quote" class="mt-2 rounded-lg border border-ink-700 bg-ink-900/50 px-3 py-2">
              <p class="text-[10px] text-brass-300">{{ locationLabel(item) }}</p>
              <p v-if="item.location.quote" class="mt-1 line-clamp-3 whitespace-pre-wrap border-l-2 border-brass-600/60 pl-2 text-[11px] leading-5 text-fog-300">{{ item.location.quote }}</p>
            </div>
            <div v-if="item.impact || item.recommended_action" class="mt-2 grid gap-2 text-[10px] leading-5 md:grid-cols-2">
              <p v-if="item.impact" class="rounded bg-rust-500/[0.06] px-2 py-1.5 text-rust-300"><span class="text-fog-500">影响：</span>{{ item.impact }}</p>
              <p v-if="item.recommended_action" class="rounded bg-brass-500/[0.06] px-2 py-1.5 text-brass-200"><span class="text-fog-500">推荐：</span>{{ item.recommended_action }}</p>
            </div>
            <div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-fog-500">
              <button
                v-if="item.book_title && !bookId"
                type="button"
                class="text-brass-300 transition-colors hover:text-brass-200"
                @click="emit('open-book', item.book_id)"
              >
                {{ item.book_title }}
              </button>
              <span v-else-if="item.book_title">{{ item.book_title }}</span>
              <span v-if="item.chapter_title" class="text-brass-300">
                章节：{{ item.chapter_title }}
              </span>
              <span :title="item.code">
                {{ ENTITY_TYPE_LABELS[item.entity_type] ?? item.entity_type }}
                <template v-if="item.entity_id"> · {{ item.entity_id.slice(-6).toUpperCase() }}</template>
              </span>
              <span>{{ formatDateTime(item.created_at) }}</span>
              <span v-if="item.resolution_note" class="text-fog-400">
                处理说明：{{ item.resolution_note }}
              </span>
            </div>
          </div>
          <div v-if="item.status === 'open'" class="flex shrink-0 flex-wrap items-center justify-end gap-2">
            <NButton
              v-if="item.category === 'content' && item.action_type !== 'task' && (item.location.chapter_id || item.location.unit_id || item.location.character_id)"
              size="tiny"
              secondary
              @click="emit('open-location', item)"
            >
              {{ actionLabel(item) }}
            </NButton>
            <template v-if="item.category === 'content' && item.actionable">
              <NButton size="tiny" type="primary" secondary :loading="actionLoading" @click="accept(item)">接受</NButton>
              <NButton size="tiny" quaternary @click="openIgnore([item.id])">忽略</NButton>
            </template>
          </div>
        </div>
      </article>

      <div v-if="reviews.items.length < reviews.total" class="flex justify-center pt-2">
        <NButton size="small" quaternary :loading="reviews.loading" @click="loadMore">
          加载更多（{{ reviews.items.length }} / {{ reviews.total }}）
        </NButton>
      </div>
    </div>

    <NModal
      v-model:show="showIgnore"
      preset="card"
      title="忽略异常"
      :bordered="false"
      :style="{ width: '520px', maxWidth: 'calc(100vw - 32px)' }"
    >
      <p class="text-[11px] leading-5 text-fog-400">
        忽略代表你确认该问题可以暂时不处理。请写下原因，后续可以在已忽略列表中查到。
      </p>
      <NInput
        v-model:value="ignoreNote"
        type="textarea"
        :rows="3"
        :maxlength="1000"
        show-count
        class="mt-3"
        placeholder="例如：该角色是龙套，暂不单独配音"
      />
      <template #footer>
        <div class="flex justify-end gap-2">
          <NButton :disabled="actionLoading" @click="showIgnore = false">取消</NButton>
          <NButton type="primary" :loading="actionLoading" @click="confirmIgnore">
            确认忽略 {{ pendingIds.length }} 项
          </NButton>
        </div>
      </template>
    </NModal>
  </section>
</template>