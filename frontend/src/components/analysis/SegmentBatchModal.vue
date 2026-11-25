<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { NButton, NModal, NSelect, NTag, useMessage } from 'naive-ui'

import type {
  Delivery,
  Emotion,
  SegmentType,
  SegmentUpdatePayload,
  SpeakerType,
} from '@/api/types'
import { useAnalysisStore } from '@/stores/analysis'
import {
  DELIVERY_LABELS,
  EMOTION_LABELS,
  SEGMENT_TYPE_LABELS,
} from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'

const props = defineProps<{
  show: boolean
  segmentIds: string[]
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'saved', count: number): void
}>()

const analysis = useAnalysisStore()
const message = useMessage()
const saving = ref(false)

const form = reactive({
  review_status: null as 'auto' | 'needs_review' | 'approved' | null,
  segment_type: null as SegmentType | null,
  speaker_type: null as SpeakerType | null,
  emotion: null as Emotion | null,
  delivery: null as Delivery | null,
})

const reviewOptions = [
  { label: '保持不变', value: '' },
  { label: '通过审核', value: 'approved' },
  { label: '标记待审核', value: 'needs_review' },
  { label: '恢复自动通过', value: 'auto' },
]

const segmentTypeOptions = [
  { label: '保持不变', value: '' },
  ...Object.entries(SEGMENT_TYPE_LABELS).map(([value, label]) => ({ value, label })),
]

const speakerOptions = [
  { label: '保持不变', value: '' },
  { label: '旁白', value: 'narrator' },
  { label: '未知说话人', value: 'unknown' },
]

const emotionOptions = [
  { label: '保持不变', value: '' },
  ...Object.entries(EMOTION_LABELS).map(([value, label]) => ({ value, label })),
]

const deliveryOptions = [
  { label: '保持不变', value: '' },
  ...Object.entries(DELIVERY_LABELS).map(([value, label]) => ({ value, label })),
]

const targetLabel = computed(() => `${props.segmentIds.length} 个片段`)

function buildPayload(): SegmentUpdatePayload {
  const payload: SegmentUpdatePayload = {}
  if (form.review_status) {
    payload.review_status = form.review_status
  }
  if (form.segment_type) {
    payload.segment_type = form.segment_type
  }
  if (form.speaker_type) {
    payload.speaker_type = form.speaker_type
    if (form.speaker_type !== 'character') {
      payload.speaker_id = null
    }
  }
  if (form.emotion) {
    payload.emotion = form.emotion
  }
  if (form.delivery) {
    payload.delivery = form.delivery
  }
  return payload
}

async function save(): Promise<void> {
  const payload = buildPayload()
  if (Object.keys(payload).length === 0) {
    message.warning('请至少选择一项要批量修改的字段')
    return
  }
  saving.value = true
  try {
    const count = await analysis.batchUpdateSegments(props.segmentIds, payload)
    emit('saved', count)
    emit('update:show', false)
    message.success(`已更新 ${count} 个片段`)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

watch(
  () => props.show,
  (show) => {
    if (show) {
      form.review_status = null
      form.segment_type = null
      form.speaker_type = null
      form.emotion = null
      form.delivery = null
    }
  },
)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="批量修改片段"
    :bordered="false"
    :mask-closable="!saving"
    :style="{ width: '520px', maxWidth: 'calc(100vw - 32px)' }"
    @update:show="emit('update:show', $event)"
  >
    <div class="space-y-4">
      <p class="text-xs leading-6 text-fog-400">
        将同时修改
        <NTag size="small" :bordered="false" type="warning">{{ targetLabel }}</NTag>
        。只应用下方明确选择的字段，其余字段保持不变。
      </p>

      <label class="block">
        <span class="mb-2 block text-[11px] text-fog-300">审核状态</span>
        <NSelect
          :value="form.review_status ?? ''"
          :options="reviewOptions"
          @update:value="form.review_status = ($event as typeof form.review_status) || null"
        />
      </label>

      <label class="block">
        <span class="mb-2 block text-[11px] text-fog-300">片段类型</span>
        <NSelect
          :value="form.segment_type ?? ''"
          :options="segmentTypeOptions"
          @update:value="form.segment_type = ($event as typeof form.segment_type) || null"
        />
      </label>

      <label class="block">
        <span class="mb-2 block text-[11px] text-fog-300">说话人类型</span>
        <NSelect
          :value="form.speaker_type ?? ''"
          :options="speakerOptions"
          @update:value="form.speaker_type = ($event as typeof form.speaker_type) || null"
        />
      </label>

      <label class="block">
        <span class="mb-2 block text-[11px] text-fog-300">情绪</span>
        <NSelect
          :value="form.emotion ?? ''"
          :options="emotionOptions"
          @update:value="form.emotion = ($event as typeof form.emotion) || null"
        />
      </label>

      <label class="block">
        <span class="mb-2 block text-[11px] text-fog-300">表演方式</span>
        <NSelect
          :value="form.delivery ?? ''"
          :options="deliveryOptions"
          @update:value="form.delivery = ($event as typeof form.delivery) || null"
        />
      </label>

      <p class="text-[10px] leading-5 text-fog-500">
        批量修改同样会锁定被修改的字段，重跑分析时不会覆盖。
      </p>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <NButton :disabled="saving" @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :loading="saving" @click="save">应用修改</NButton>
      </div>
    </template>
  </NModal>
</template>