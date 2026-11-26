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

import type { ReviewItemOut } from '@/api/types'
import { useReviewStore } from '@/stores/reviews'
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
    heading?: string
    hint?: string
  }>(),
  {
    bookId: null,
    heading: '异常收件箱',
    hint: '只处理需要你判断的节点；接受会保留当前结果并关闭该项。',
  },
)

const emit = defineEmits<{
  (event: 'changed'): void
  (event: 'open-book', bookId: string): void
}>()

const reviews = useReviewStore()
const message = useMessage()

const selectedIds = ref<string[]>([])
const showIgnore = ref(false)
const ignoreNote = ref('')
const pendingIds = ref<string[]>([])
const actionLoading = ref(false)

const openItems = computed(() =>
  reviews.items.filter((item) => item.status === 'open'),
)
const allSelected = computed(
  () =>
    openItems.value.length > 0 &&
    openItems.value.every((item) => selectedIds.value.includes(item.id)),
)
const selectedOpenIds = computed(() =>
  selectedIds.value.filter((id) =>
    openItems.value.some((item) => item.id === id),
  ),
)

function pruneSelection(): void {
  const openIds = new Set(openItems.value.map((item) => item.id))
  selectedIds.value = selectedIds.value.filter((id) => openIds.has(id))
}

function toggleAll(value: boolean): void {
  selectedIds.value = value ? openItems.value.map((item) => item.id) : []
}

function toggleOne(item: ReviewItemOut, value: boolean): void {
  const set = new Set(selectedIds.value)
  if (value) {
    set.add(item.id)
  } else {
    set.delete(item.id)
  }
  selectedIds.value = [...set]
}

async function refresh(overrides = {}): Promise<void> {
  try {
    await reviews.load({ bookId: props.bookId, offset: 0, ...overrides })
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

async function loadMore(): Promise<void> {
  const next = Math.min(250, (reviews.query.limit ?? 100) + 100)
  await refresh({ limit: next })
}

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
          <span class="text-fog-500">待处理</span>
          <span class="ml-2 font-mono text-fog-100">{{ reviews.counts.open }}</span>
          <span class="mx-2 text-fog-600">/</span>
          <span class="text-rust-400">阻断 {{ reviews.counts.blocking }}</span>
          <span class="ml-2 text-brass-300">警告 {{ reviews.counts.warning }}</span>
          <span class="ml-2 text-lake-400">提示 {{ reviews.counts.info }}</span>
        </div>
        <NButton size="small" quaternary :loading="reviews.loading" @click="refresh()">
          刷新
        </NButton>
      </div>
    </header>

    <div class="mt-4 flex flex-wrap items-center gap-2">
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
        :checked="allSelected"
        :disabled="!openItems.length"
        @update:checked="toggleAll"
      >
        <span class="text-[11px] text-fog-400">全选待处理</span>
      </NCheckbox>
      <div class="ml-auto flex items-center gap-2">
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
      <p class="font-display text-lg text-fog-200">这个范围内没有未处理的异常</p>
      <p class="mt-2 text-[11px] text-fog-500">
        生成、分场与选角过程中需要你判断的节点会集中出现在这里。
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
            v-if="item.status === 'open'"
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
                v-if="item.status !== 'open'"
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
          <div v-if="item.status === 'open'" class="flex shrink-0 items-center gap-2">
            <NButton size="tiny" type="primary" secondary :loading="actionLoading" @click="accept(item)">
              接受
            </NButton>
            <NButton size="tiny" quaternary @click="openIgnore([item.id])">忽略</NButton>
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