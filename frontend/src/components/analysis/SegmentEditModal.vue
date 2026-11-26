<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'

import type {
  Delivery,
  Emotion,
  SegmentOut,
  SegmentType,
  SegmentUpdatePayload,
  SpeakerType,
} from '@/api/types'
import { useAnalysisStore } from '@/stores/analysis'
import {
  DELIVERY_LABELS,
  EMOTION_LABELS,
  INTENSITY_LABELS,
  LOCKABLE_FIELDS,
  SEGMENT_TYPE_LABELS,
  SPEAKER_TYPE_LABELS,
} from '@/utils/analysis'
import { AUDIO_STATUS_LABELS } from '@/utils/production'
import { errorMessage } from '@/utils/errors'

const props = defineProps<{
  show: boolean
  segment: SegmentOut | null
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'saved', value: SegmentOut): void
}>()

const analysis = useAnalysisStore()
const message = useMessage()

const form = reactive({
  speech_text: '',
  segment_type: 'narration' as SegmentType,
  speaker_type: 'narrator' as SpeakerType,
  speaker_id: null as string | null,
  emotion: 'neutral' as Emotion,
  intensity: 1 as number | null,
  delivery: 'normal' as Delivery,
  tts_instruction: '',
  speed: 1 as number | null,
  pause_before_ms: 0 as number | null,
  pause_after_ms: 0 as number | null,
})

const unlockFields = ref<string[]>([])
const approveAfter = ref(false)
const saving = ref(false)

const characterOptions = computed(() =>
  analysis.activeCharacters.map((item) => ({
    label: item.identity_description
      ? `${item.canonical_name} · ${item.identity_description}`
      : item.canonical_name,
    value: item.id,
  })),
)

const speakerOptions = Object.entries(SPEAKER_TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}))

const segmentTypeOptions = Object.entries(SEGMENT_TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}))

const emotionOptions = Object.entries(EMOTION_LABELS).map(([value, label]) => ({
  value,
  label,
}))

const deliveryOptions = Object.entries(DELIVERY_LABELS).map(([value, label]) => ({
  value,
  label,
}))

const intensityOptions = [1, 2, 3].map((value) => ({
  value,
  label: `${value} · ${INTENSITY_LABELS[value]}`,
}))

const lockedFields = computed(() =>
  props.segment ? new Set(props.segment.user_locked_fields) : new Set<string>(),
)

const isCharacterSpeaker = computed(() => form.speaker_type === 'character')

const evidenceItems = computed(() => {
  const raw = props.segment?.evidence ?? []
  return raw
    .map((item) => ({
      quote: typeof item.quote === 'string' ? item.quote : '',
      reason: typeof item.reason === 'string' ? item.reason : '',
    }))
    .filter((item) => item.quote || item.reason)
})

function resetForm(): void {
  const segment = props.segment
  if (!segment) {
    return
  }
  form.speech_text = segment.speech_text
  form.segment_type = segment.segment_type as SegmentType
  form.speaker_type = segment.speaker_type as SpeakerType
  form.speaker_id = segment.speaker_id
  form.emotion = segment.emotion as Emotion
  form.intensity = segment.intensity
  form.delivery = segment.delivery as Delivery
  form.tts_instruction = segment.tts_instruction ?? ''
  form.speed = segment.speed
  form.pause_before_ms = segment.pause_before_ms
  form.pause_after_ms = segment.pause_after_ms
  unlockFields.value = []
  approveAfter.value = false
}

function close(): void {
  emit('update:show', false)
}

function toggleUnlock(field: string): void {
  if (!lockedFields.value.has(field)) {
    return
  }
  unlockFields.value = unlockFields.value.includes(field)
    ? unlockFields.value.filter((item) => item !== field)
    : [...unlockFields.value, field]
}

function buildPayload(): SegmentUpdatePayload {
  const segment = props.segment
  if (!segment) {
    return {}
  }
  const payload: SegmentUpdatePayload = {}
  const text = form.speech_text.trim()
  if (text && text !== segment.speech_text) {
    payload.speech_text = text
  }
  if (form.segment_type !== segment.segment_type) {
    payload.segment_type = form.segment_type
  }
  if (form.speaker_type !== segment.speaker_type) {
    payload.speaker_type = form.speaker_type
  }
  if (form.speaker_type === 'character') {
    if (form.speaker_id && form.speaker_id !== segment.speaker_id) {
      payload.speaker_id = form.speaker_id
    }
  } else if (segment.speaker_type === 'character' || segment.speaker_id) {
    payload.speaker_id = null
  }
  if (form.emotion !== segment.emotion) {
    payload.emotion = form.emotion
  }
  if (form.intensity !== null && form.intensity !== segment.intensity) {
    payload.intensity = form.intensity
  }
  if (form.delivery !== segment.delivery) {
    payload.delivery = form.delivery
  }
  const instruction = form.tts_instruction.trim()
  if (instruction !== (segment.tts_instruction ?? '')) {
    payload.tts_instruction = instruction || null
  }
  if (form.speed !== null && form.speed !== segment.speed) {
    payload.speed = form.speed
  }
  if (form.pause_before_ms !== null && form.pause_before_ms !== segment.pause_before_ms) {
    payload.pause_before_ms = form.pause_before_ms
  }
  if (form.pause_after_ms !== null && form.pause_after_ms !== segment.pause_after_ms) {
    payload.pause_after_ms = form.pause_after_ms
  }
  if (approveAfter.value) {
    payload.review_status = 'approved'
  }
  if (unlockFields.value.length) {
    payload.unlock_fields = [...unlockFields.value]
  }
  return payload
}

async function save(): Promise<void> {
  const segment = props.segment
  if (!segment) {
    return
  }
  if (!form.speech_text.trim()) {
    message.warning('朗读文本不能为空')
    return
  }
  const payload = buildPayload()
  if (Object.keys(payload).length === 0) {
    close()
    return
  }
  saving.value = true
  try {
    const updated = await analysis.saveSegment(segment.id, payload)
    emit('saved', updated)
    close()
    message.success(approveAfter.value ? '片段已保存并通过审核' : '片段已保存')
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
      resetForm()
    }
  },
)

watch(
  () => props.segment,
  () => {
    if (props.show) {
      resetForm()
    }
  },
)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="审核片段"
    :bordered="false"
    :mask-closable="!saving"
    :style="{ width: '720px', maxWidth: 'calc(100vw - 32px)' }"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="segment" class="space-y-5">
      <div class="flex flex-wrap items-center gap-2 text-[10px] text-fog-500">
        <span class="font-mono">#{{ segment.segment_index + 1 }}</span>
        <NTag size="small" :bordered="false">
          {{ SEGMENT_TYPE_LABELS[segment.segment_type] ?? segment.segment_type }}
        </NTag>
        <span v-if="segment.review_status === 'needs_review'" class="text-brass-300">
          待审核
        </span>
        <span v-else-if="segment.review_status === 'approved'" class="text-moss-400">
          已审核
        </span>
        <span>文本版本 v{{ segment.text_version }}</span>
        <span v-if="segment.audio_status !== 'none'" class="text-rust-400">
          音频状态：{{ AUDIO_STATUS_LABELS[segment.audio_status] ?? segment.audio_status }}
        </span>
      </div>

      <div>
        <p class="eyebrow mb-2">原文 · 只读</p>
        <div class="max-h-36 overflow-y-auto rounded-lg border border-ink-700 bg-ink-900/70 px-3 py-2.5">
          <p class="whitespace-pre-wrap font-display text-[13px] leading-6 text-fog-300">
            {{ segment.source_text }}
          </p>
        </div>
      </div>

      <label class="block">
        <span class="mb-2 flex items-center justify-between text-[11px] text-fog-300">
          <span>朗读文本（可修正错字与断句，不改变原文）</span>
          <span
            v-if="lockedFields.has('speech_text')"
            class="text-[9px] text-brass-400"
          >
            已人工锁定
          </span>
        </span>
        <NInput
          v-model:value="form.speech_text"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          placeholder="朗读时使用的文本"
        />
      </label>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">片段类型</span>
          <NSelect v-model:value="form.segment_type" :options="segmentTypeOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">说话人类型</span>
          <NSelect v-model:value="form.speaker_type" :options="speakerOptions" />
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">
            说话角色
            <span v-if="!isCharacterSpeaker" class="text-fog-500">
              （旁白 / 未知时不绑定角色）
            </span>
          </span>
          <NSelect
            v-model:value="form.speaker_id"
            :options="characterOptions"
            :disabled="!isCharacterSpeaker"
            filterable
            clearable
            placeholder="选择角色"
          />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">情绪</span>
          <NSelect v-model:value="form.emotion" :options="emotionOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">强度</span>
          <NSelect v-model:value="form.intensity" :options="intensityOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">表演方式</span>
          <NSelect v-model:value="form.delivery" :options="deliveryOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">语速</span>
          <NInputNumber
            v-model:value="form.speed"
            :min="0.5"
            :max="2"
            :step="0.05"
            class="w-full"
          />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">段前停顿 ms</span>
          <NInputNumber
            v-model:value="form.pause_before_ms"
            :min="0"
            :max="10000"
            :step="100"
            class="w-full"
          />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">段后停顿 ms</span>
          <NInputNumber
            v-model:value="form.pause_after_ms"
            :min="0"
            :max="10000"
            :step="100"
            class="w-full"
          />
        </label>
      </div>

      <label class="block">
        <span class="mb-2 block text-[11px] text-fog-300">TTS 表演提示</span>
        <NInput
          v-model:value="form.tts_instruction"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 4 }"
          placeholder="自然语言表演提示，不改变原文"
        />
      </label>

      <div v-if="segment.user_locked_fields.length" class="rounded-lg border border-brass-600/25 bg-brass-500/[0.05] p-3">
        <p class="text-[10px] font-semibold tracking-wide text-brass-300">人工锁定字段</p>
        <p class="mt-1 text-[10px] leading-5 text-fog-500">
          重跑分析时这些字段保持当前值。点击可标记为“保存后解锁”，交还给 AI 更新。
        </p>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="field in LOCKABLE_FIELDS.filter((item) => lockedFields.has(item.key))"
            :key="field.key"
            type="button"
            class="nv-focus rounded border px-1.5 py-0.5 text-[9px] transition-colors"
            :class="
              unlockFields.includes(field.key)
                ? 'border-rust-500/50 text-rust-400 line-through'
                : 'border-brass-600/40 text-brass-300 hover:border-brass-500'
            "
            @click="toggleUnlock(field.key)"
          >
            {{ field.label }}{{ unlockFields.includes(field.key) ? ' · 待解锁' : '' }}
          </button>
        </div>
      </div>

      <div v-if="evidenceItems.length" class="rounded-lg border border-ink-700 bg-ink-900/40 p-3">
        <p class="text-[10px] font-semibold tracking-wide text-fog-400">AI 判断依据</p>
        <ul class="mt-2 space-y-1.5">
          <li
            v-for="(item, index) in evidenceItems"
            :key="index"
            class="text-[10px] leading-5 text-fog-500"
          >
            <span class="text-fog-300">“{{ item.quote }}”</span>
            <span v-if="item.reason"> — {{ item.reason }}</span>
          </li>
        </ul>
      </div>

      <div class="flex items-center justify-between gap-3 rounded-lg border border-ink-700 bg-ink-850/40 px-3 py-2.5">
        <div>
          <p class="text-[11px] text-fog-200">保存并通过审核</p>
          <p class="text-[9px] text-fog-500">
            通过后当前表演字段全部锁定，后续重跑不会覆盖。
          </p>
        </div>
        <NSwitch v-model:value="approveAfter" />
      </div>

      <NAlert v-if="analysis.error" type="error" :bordered="false">
        <span class="text-[11px]">{{ analysis.error }}</span>
      </NAlert>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <NButton :disabled="saving" @click="close">取消</NButton>
        <NButton type="primary" :loading="saving" @click="save">
          {{ approveAfter ? '保存并通过' : '保存' }}
        </NButton>
      </div>
    </template>
  </NModal>
</template>