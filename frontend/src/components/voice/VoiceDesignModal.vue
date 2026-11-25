<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  useMessage,
} from 'naive-ui'

import type { VoiceDesignResponse, VoiceOwnerOut } from '@/api/types'
import { useVoiceStore } from '@/stores/voices'
import { errorMessage } from '@/utils/errors'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{
  bookId: string
  owner: VoiceOwnerOut | null
}>()

const emit = defineEmits<{ (event: 'designed', value: VoiceDesignResponse): void }>()

const voices = useVoiceStore()
const message = useMessage()

const description = ref('')
const referenceText = ref('')
const candidates = ref(2)
const seed = ref<number | null>(null)
const submitting = ref(false)

const candidateOptions = [1, 2, 3, 4, 5].map((value) => ({
  label: `${value} 个候选`,
  value,
}))

watch(
  () => show.value,
  (visible) => {
    if (!visible) {
      return
    }
    description.value = props.owner?.description ?? ''
    referenceText.value = ''
    candidates.value = Math.min(Math.max(props.owner?.profile_count ? 1 : 2, 1), 5)
    seed.value = null
  },
)

async function submit(): Promise<void> {
  const owner = props.owner
  if (!owner || submitting.value) {
    return
  }
  if (!description.value.trim() && !owner.description.trim()) {
    message.warning('请先填写音色描述')
    return
  }
  submitting.value = true
  try {
    const response = await voices.design(props.bookId, {
      owner_type: owner.owner_type,
      owner_id: owner.owner_id,
      description: description.value.trim() || null,
      reference_text: referenceText.value.trim() || null,
      candidates: candidates.value,
      seed: seed.value,
    })
    if (response.errors.length) {
      message.warning(
        `已生成 ${response.items.length} 个候选，另有 ${response.errors.length} 个失败`,
      )
    } else {
      message.success(`已生成 ${response.items.length} 个音色候选`)
    }
    emit('designed', response)
    show.value = false
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    :title="owner ? `设计音色 · ${owner.name}` : '设计音色'"
    :bordered="false"
    :mask-closable="!submitting"
    :style="{ width: '620px', maxWidth: 'calc(100vw - 32px)' }"
  >
    <div class="space-y-4">
      <NAlert type="info" :bordered="false">
        <p class="text-[11px] leading-5">
          真实模式会先由 Qwen3-TTS 生成情绪参考音，再用 CosyVoice3 克隆固定音色。
          候选越多，占用 GPU 时间越长。模拟模式会直接生成测试音。
        </p>
      </NAlert>

      <label class="block">
        <span class="mb-1.5 flex items-center justify-between text-[11px] text-fog-300">
          <span>音色描述</span>
          <span class="text-[9px] text-fog-500">会作为声音设计提示词</span>
        </span>
        <NInput
          v-model:value="description"
          type="textarea"
          :rows="3"
          :maxlength="2000"
          placeholder="例如：青年男性，声音清亮，咬字干净，语速偏快"
        />
      </label>

      <label class="block">
        <span class="mb-1.5 flex items-center justify-between text-[11px] text-fog-300">
          <span>参考文本</span>
          <span class="text-[9px] text-fog-500">留空使用全局中性参考段落</span>
        </span>
        <NInput
          v-model:value="referenceText"
          type="textarea"
          :rows="3"
          :maxlength="500"
          placeholder="用于生成参考音的固定文本，建议包含平翘舌与常见齿音字"
        />
      </label>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">候选数量</span>
          <NSelect v-model:value="candidates" :options="candidateOptions" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">
            随机种子 <span class="text-[9px] text-fog-500">留空自动</span>
          </span>
          <NInputNumber
            v-model:value="seed"
            :min="0"
            :max="2147483647"
            :show-button="false"
            placeholder="例如 42"
            class="w-full"
          />
        </label>
      </div>

      <p class="text-[10px] leading-5 text-fog-500">
        候选会保存在该书的声音库中，可逐个试听后再绑定。绑定后才会用于片段生成。
      </p>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <NButton :disabled="submitting" @click="show = false">取消</NButton>
        <NButton type="primary" :loading="submitting" @click="submit">
          开始设计
        </NButton>
      </div>
    </template>
  </NModal>
</template>