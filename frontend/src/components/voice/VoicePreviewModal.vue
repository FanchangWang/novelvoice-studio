<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NInput,
  NModal,
  NSelect,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type { VoicePreviewPayload, VoiceProfileOut } from '@/api/types'
import { DELIVERY_LABELS, EMOTION_LABELS } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{ voice: VoiceProfileOut | null }>()

const message = useMessage()

const text = ref('我等你很久了。别怕，把门关上。')
const emotion = ref('neutral')
const intensity = ref(1)
const delivery = ref('normal')
const speed = ref(1)
const previewing = ref(false)
const audioUrl = ref<string | null>(null)
const audioKey = ref(0)

const emotionOptions = Object.entries(EMOTION_LABELS).map(([value, label]) => ({
  label,
  value,
}))
const deliveryOptions = Object.entries(DELIVERY_LABELS).map(([value, label]) => ({
  label,
  value,
}))
const intensityOptions = [
  { label: '轻', value: 1 },
  { label: '中', value: 2 },
  { label: '强', value: 3 },
]

function revoke(): void {
  if (audioUrl.value) {
    URL.revokeObjectURL(audioUrl.value)
    audioUrl.value = null
  }
}

watch(show, (visible) => {
  if (!visible) {
    revoke()
    previewing.value = false
  }
})

watch(
  () => props.voice?.id,
  () => {
    revoke()
  },
)

async function preview(): Promise<void> {
  const voice = props.voice
  if (!voice || previewing.value) {
    return
  }
  if (!text.value.trim()) {
    message.warning('请填写试听文本')
    return
  }
  previewing.value = true
  try {
    const payload: VoicePreviewPayload = {
      text: text.value.trim(),
      emotion: emotion.value as VoicePreviewPayload['emotion'],
      intensity: intensity.value,
      delivery: delivery.value as VoicePreviewPayload['delivery'],
      speed: speed.value,
    }
    const blob = await api.previewVoice(voice.id, payload)
    revoke()
    audioUrl.value = URL.createObjectURL(blob)
    audioKey.value += 1
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    previewing.value = false
  }
}

onBeforeUnmount(revoke)
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    :title="voice ? `试听 · ${voice.name}` : '试听音色'"
    :bordered="false"
    :style="{ width: '620px', maxWidth: 'calc(100vw - 32px)' }"
  >
    <div class="space-y-4">
      <NAlert v-if="voice?.is_mock" type="warning" :bordered="false">
        该音色来自模拟模式，播放的是测试音，不代表真实模型效果。
      </NAlert>

      <label class="block">
        <span class="mb-1.5 block text-[11px] text-fog-300">试听文本</span>
        <NInput
          v-model:value="text"
          type="textarea"
          :rows="3"
          :maxlength="500"
          placeholder="输入一段用于试听的文本"
        />
      </label>

      <div class="grid gap-4 sm:grid-cols-3">
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">情绪</span>
          <NSelect v-model:value="emotion" :options="emotionOptions" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">强度</span>
          <NSelect v-model:value="intensity" :options="intensityOptions" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">表演方式</span>
          <NSelect v-model:value="delivery" :options="deliveryOptions" />
        </label>
      </div>

      <label class="block">
        <span class="mb-1.5 flex items-center justify-between text-[11px] text-fog-300">
          <span>语速</span>
          <span class="font-mono text-[10px] text-fog-500">{{ speed.toFixed(2) }}×</span>
        </span>
        <input
          v-model.number="speed"
          type="range"
          min="0.6"
          max="1.5"
          step="0.05"
          class="w-full accent-brass-400"
        />
      </label>

      <div
        v-if="audioUrl"
        class="rounded-lg border border-ink-700 bg-ink-900/60 p-3"
      >
        <audio
          :key="audioKey"
          :src="audioUrl"
          controls
          autoplay
          class="w-full"
        />
      </div>

      <p class="text-[10px] leading-5 text-fog-500">
        试听只调用当前音色的参考音频，不会写入片段或生成历史。真实模式需要引擎服务已启动。
      </p>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <NButton :disabled="previewing" @click="show = false">关闭</NButton>
        <NButton type="primary" :loading="previewing" @click="preview">
          {{ audioUrl ? '重新试听' : '生成试听' }}
        </NButton>
      </div>
    </template>
  </NModal>
</template>