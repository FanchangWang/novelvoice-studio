<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import type { SegmentOut } from '@/api/types'
import SegmentBatchModal from '@/components/analysis/SegmentBatchModal.vue'
import SegmentEditModal from '@/components/analysis/SegmentEditModal.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useWorkspaceStore } from '@/stores/workspace'
import {
  DELIVERY_LABELS,
  EMOTION_LABELS,
  INTENSITY_LABELS,
  REVIEW_STATUS_LABELS,
  SEGMENT_TYPE_LABELS,
  SPEAKER_TYPE_LABELS,
  percent,
} from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{ chapterId: string | null }>()
const emit = defineEmits<{ (event: 'analyze'): void }>()

const analysis = useAnalysisStore()
const workspace = useWorkspaceStore()
const message = useMessage()

type QuickFilter = 'all' | 'needs_review' | 'approved' | 'low' | 'speaker_unknown'

const quickFilter = ref<QuickFilter>('all')
const typeFilter = ref<string>('')
const speakerTypeFilter = ref<string>('')
const selectedIds = ref<string[]>([])
const editingSegment = ref<SegmentOut | null>(null)
const showEdit = ref(false)
const showBatch = ref(false)
const approvingIds = ref<string[]>([])

const segmentTypeOptions = [
  { label: '全部类型', value: '' },
  ...Object.entries(SEGMENT_TYPE_LABELS).map(([value, label]) => ({ value, label })),
]

const speakerTypeOptions = [
  { label: '全部说话人', value: '' },
  ...Object.entries(SPEAKER_TYPE_LABELS).map(([value, label]) => ({ value, label })),
]

const quickFilters: Array<{ key: QuickFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'needs_review', label: '待审核' },
  { key: 'low', label: '低置信度' },
  { key: 'speaker_unknown', label: '说话人不明' },
  { key: 'approved', label: '已审核' },
]

const chapterInfo = computed(() => {
  if (!props.chapterId) {
    return null
  }
  return analysis.chapterStatus.get(props.chapterId) ?? null
})

const filteredSegments = computed(() => {
  return analysis.segments.filter((segment) => {
    if (quickFilter.value === 'needs_review' && segment.review_status !== 'needs_review') {
      return false
    }
    if (quickFilter.value === 'approved' && segment.review_status !== 'approved') {
      return false
    }
    if (
      quickFilter.value === 'low' &&
      !segment.low_speaker_confidence &&
      !segment.low_emotion_confidence
    ) {
      return false
    }
    if (
      quickFilter.value === 'speaker_unknown' &&
      segment.speaker_type !== 'unknown' &&
      !segment.low_speaker_confidence
    ) {
      return false
    }
    if (typeFilter.value && segment.segment_type !== typeFilter.value) {
      return false
    }
    if (speakerTypeFilter.value && segment.speaker_type !== speakerTypeFilter.value) {
      return false
    }
    return true
  })
})

const allVisibleSelected = computed(
  () =>
    filteredSegments.value.length > 0 &&
    filteredSegments.value.every((item) => selectedIds.value.includes(item.id)),
)

const reviewCount = computed(
  () => analysis.segments.filter((item) => item.review_status === 'needs_review').length,
)

const analyzed = computed(() => {
  const chapterStatus = chapterInfo.value?.analysis_status
  return (
    chapterStatus === 'completed' ||
    chapterStatus === 'needs_review' ||
    analysis.segments.length > 0
  )
})

function speakerLabel(segment: SegmentOut): string {
  if (segment.speaker_type === 'narrator') {
    return '旁白'
  }
  if (segment.speaker_type === 'character') {
    return (
      analysis.characterName(segment.speaker_id) ??
      segment.speaker_name_snapshot ??
      '未命名角色'
    )
  }
  return '未知说话人'
}

function speakerTone(segment: SegmentOut): 'default' | 'warning' | 'error' | 'success' {
  if (segment.speaker_type === 'unknown' || segment.low_speaker_confidence) {
    return 'error'
  }
  if (segment.speaker_type === 'narrator') {
    return 'default'
  }
  return 'success'
}

function reviewTone(status: string): 'default' | 'warning' | 'success' {
  if (status === 'needs_review') {
    return 'warning'
  }
  if (status === 'approved') {
    return 'success'
  }
  return 'default'
}

function openEdit(segment: SegmentOut): void {
  editingSegment.value = segment
  showEdit.value = true
}

function handleSaved(updated: SegmentOut): void {
  if (editingSegment.value?.id === updated.id) {
    editingSegment.value = updated
  }
}

function toggleSelected(segmentId: string): void {
  selectedIds.value = selectedIds.value.includes(segmentId)
    ? selectedIds.value.filter((id) => id !== segmentId)
    : [...selectedIds.value, segmentId]
}

function toggleSelectAll(): void {
  if (allVisibleSelected.value) {
    const visible = new Set(filteredSegments.value.map((item) => item.id))
    selectedIds.value = selectedIds.value.filter((id) => !visible.has(id))
  } else {
    const next = new Set(selectedIds.value)
    for (const item of filteredSegments.value) {
      next.add(item.id)
    }
    selectedIds.value = [...next]
  }
}

async function approveSegments(ids: string[]): Promise<void> {
  if (!ids.length) {
    return
  }
  approvingIds.value = ids
  try {
    const count = await analysis.batchUpdateSegments(ids, { review_status: 'approved' })
    selectedIds.value = selectedIds.value.filter((id) => !ids.includes(id))
    const editing = editingSegment.value
    if (editing && ids.includes(editing.id)) {
      editingSegment.value =
        analysis.segments.find((item) => item.id === editing.id) ?? editing
    }
    message.success(`已通过 ${count} 个片段`)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    approvingIds.value = []
  }
}

function onBatchSaved(): void {
  selectedIds.value = []
}

async function retryLoad(): Promise<void> {
  if (!props.chapterId) {
    return
  }
  try {
    await analysis.loadSegments(props.chapterId)
  } catch (err) {
    message.error(errorMessage(err))
  }
}

function reloadChapter(): void {
  if (props.chapterId) {
    void workspace.select(props.chapterId)
  }
}

watch(
  () => props.chapterId,
  (chapterId) => {
    selectedIds.value = []
    quickFilter.value = 'all'
    typeFilter.value = ''
    speakerTypeFilter.value = ''
    if (chapterId) {
      void analysis.loadSegments(chapterId).catch((err) => {
        message.error(errorMessage(err))
      })
    }
  },
  { immediate: true },
)

watch(
  () => analysis.jobFinishedTick,
  () => {
    if (props.chapterId) {
      void analysis.loadSegments(props.chapterId).catch(() => undefined)
    }
  },
)
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="shrink-0 border-b border-ink-700/70 bg-ink-900/30 px-4 py-3 md:px-5">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div class="flex items-center gap-2">
          <p class="text-[12px] font-medium text-fog-200">片段审核</p>
          <NTag size="small" :bordered="false">
            {{ analysis.segments.length }} 片段
          </NTag>
          <NTag v-if="reviewCount" size="small" :bordered="false" type="warning">
            {{ reviewCount }} 待审核
          </NTag>
        </div>
        <div class="ml-auto flex flex-wrap items-center gap-2">
          <NSelect
            v-model:value="typeFilter"
            size="small"
            :options="segmentTypeOptions"
            class="w-32"
          />
          <NSelect
            v-model:value="speakerTypeFilter"
            size="small"
            :options="speakerTypeOptions"
            class="w-32"
          />
          <NButton size="small" quaternary :loading="analysis.loadingSegments" @click="retryLoad">
            刷新
          </NButton>
        </div>
      </div>

      <div class="mt-2.5 flex flex-wrap items-center gap-1.5">
        <button
          v-for="item in quickFilters"
          :key="item.key"
          type="button"
          class="nv-focus rounded-full border px-2.5 py-1 text-[10px] transition-colors"
          :class="
            quickFilter === item.key
              ? 'border-brass-500/60 bg-brass-500/10 text-brass-200'
              : 'border-ink-700 text-fog-400 hover:border-ink-600 hover:text-fog-200'
          "
          @click="quickFilter = item.key"
        >
          {{ item.label }}
        </button>
        <span class="mx-1 h-3 w-px bg-ink-700" />
        <NCheckbox
          :checked="allVisibleSelected"
          :disabled="!filteredSegments.length"
          @update:checked="toggleSelectAll"
        >
          <span class="text-[10px] text-fog-400">全选当前</span>
        </NCheckbox>
        <span v-if="selectedIds.length" class="text-[10px] text-brass-300">
          已选 {{ selectedIds.length }}
        </span>
        <div v-if="selectedIds.length" class="ml-auto flex items-center gap-1.5">
          <NButton size="tiny" :loading="!!approvingIds.length" @click="approveSegments(selectedIds)">
            通过所选
          </NButton>
          <NButton size="tiny" @click="showBatch = true">批量修改</NButton>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-4xl px-4 py-4 md:px-6">
        <NAlert v-if="analysis.error" type="error" :bordered="false" class="mb-3">
          <div class="flex items-center justify-between gap-3">
            <span class="text-[11px]">{{ analysis.error }}</span>
            <NButton size="tiny" quaternary @click="retryLoad">重试</NButton>
          </div>
        </NAlert>

        <div
          v-if="!chapterId"
          class="flex min-h-[280px] flex-col items-center justify-center text-center"
        >
          <p class="text-xs text-fog-400">请先选择一个章节</p>
        </div>

        <div
          v-else-if="analysis.loadingSegments && !analysis.segments.length"
          class="flex min-h-[280px] items-center justify-center gap-3 text-xs text-fog-400"
        >
          <NSpin size="small" />
          正在加载片段…
        </div>

        <div
          v-else-if="!analyzed"
          class="flex min-h-[320px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 px-6 text-center"
        >
          <p class="font-display text-lg text-fog-200">本章尚未分析</p>
          <p class="mt-2 max-w-md text-[11px] leading-5 text-fog-500">
            运行章节分析后，这里会按说话人、情绪和表演方式展示可审核的片段。
          </p>
          <div class="mt-4 flex gap-2">
            <NButton
              type="primary"
              size="small"
              :loading="analysis.analyzingChapter"
              @click="emit('analyze')"
            >
              分析本章
            </NButton>
            <NButton size="small" @click="reloadChapter">重新加载章节</NButton>
          </div>
        </div>

        <div
          v-else-if="!filteredSegments.length"
          class="flex min-h-[260px] flex-col items-center justify-center text-center"
        >
          <p class="text-xs text-fog-300">当前筛选条件下没有片段</p>
          <p class="mt-1 text-[10px] text-fog-500">切换筛选条件或清除类型过滤。</p>
        </div>

        <div v-else class="space-y-2">
          <article
            v-for="segment in filteredSegments"
            :key="segment.id"
            class="group cursor-pointer rounded-xl border bg-ink-900/45 px-3 py-2.5 transition-colors hover:bg-ink-850/70"
            :class="
              selectedIds.includes(segment.id)
                ? 'border-brass-500/50'
                : segment.review_status === 'needs_review'
                  ? 'border-brass-600/25'
                  : 'border-ink-700/80'
            "
            @click="openEdit(segment)"
          >
            <div class="flex items-start gap-2.5">
              <div class="pt-0.5" @click.stop>
                <NCheckbox
                  :checked="selectedIds.includes(segment.id)"
                  @update:checked="toggleSelected(segment.id)"
                />
              </div>

              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="font-mono text-[9px] text-fog-600">
                    #{{ segment.segment_index + 1 }}
                  </span>
                  <NTag size="small" :bordered="false">
                    {{ SEGMENT_TYPE_LABELS[segment.segment_type] ?? segment.segment_type }}
                  </NTag>
                  <NTag size="small" :bordered="false" :type="speakerTone(segment)">
                    {{ speakerLabel(segment) }}
                  </NTag>
                  <span class="text-[10px] text-fog-500">
                    {{ EMOTION_LABELS[segment.emotion] ?? segment.emotion }}
                    · {{ INTENSITY_LABELS[segment.intensity] ?? segment.intensity }}
                    · {{ DELIVERY_LABELS[segment.delivery] ?? segment.delivery }}
                  </span>
                  <NTag
                    v-if="segment.review_status === 'needs_review'"
                    size="small"
                    :bordered="false"
                    :type="reviewTone(segment.review_status)"
                  >
                    待审核
                  </NTag>
                  <NTag
                    v-else-if="segment.review_status === 'approved'"
                    size="small"
                    :bordered="false"
                    type="success"
                  >
                    已审核
                  </NTag>
                  <span
                    v-if="segment.user_locked_fields.length"
                    class="rounded border border-brass-600/30 bg-brass-500/[0.06] px-1 py-0.5 text-[8px] text-brass-300"
                  >
                    {{ segment.user_locked_fields.length }} 项锁定
                  </span>
                </div>

                <p class="mt-1.5 whitespace-pre-wrap break-words font-display text-[13px] leading-6 text-fog-200">
                  {{ segment.source_text }}
                </p>
                <p
                  v-if="segment.speech_text !== segment.source_text"
                  class="mt-1 whitespace-pre-wrap break-words text-[10px] leading-5 text-lake-400"
                >
                  朗读：{{ segment.speech_text }}
                </p>

                <div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-fog-600">
                  <span>说话人置信度 {{ percent(segment.speaker_confidence) }}</span>
                  <span>情绪置信度 {{ percent(segment.emotion_confidence) }}</span>
                  <span>语速 {{ segment.speed }}x</span>
                  <span>停顿 {{ segment.pause_before_ms }}/{{ segment.pause_after_ms }}ms</span>
                  <span>{{ REVIEW_STATUS_LABELS[segment.review_status] ?? segment.review_status }}</span>
                  <span class="ml-auto">{{ formatDateTime(segment.updated_at) }}</span>
                </div>

                <p v-if="segment.low_speaker_confidence || segment.low_emotion_confidence" class="mt-1 text-[9px] text-rust-400">
                  <span v-if="segment.low_speaker_confidence">说话人需要确认</span>
                  <span v-if="segment.low_speaker_confidence && segment.low_emotion_confidence"> · </span>
                  <span v-if="segment.low_emotion_confidence">情绪置信度偏低</span>
                </p>
              </div>

              <div class="flex shrink-0 flex-col items-end gap-1.5" @click.stop>
                <NButton
                  v-if="segment.review_status !== 'approved'"
                  size="tiny"
                  :secondary="true"
                  :loading="approvingIds.includes(segment.id)"
                  @click="approveSegments([segment.id])"
                >
                  通过
                </NButton>
                <NButton size="tiny" quaternary @click="openEdit(segment)">编辑</NButton>
              </div>
            </div>
          </article>
        </div>
      </div>
    </div>

    <SegmentEditModal
      v-model:show="showEdit"
      :segment="editingSegment"
      @saved="handleSaved"
    />
    <SegmentBatchModal
      v-model:show="showBatch"
      :segment-ids="selectedIds"
      @saved="onBatchSaved"
    />
  </div>
</template>