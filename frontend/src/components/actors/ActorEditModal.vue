<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NInput,
  NModal,
  NSelect,
  useMessage,
} from 'naive-ui'

import type { VoiceActorCreatePayload, VoiceActorOut } from '@/api/types'
import { useActorStore } from '@/stores/actors'
import { errorMessage } from '@/utils/errors'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{ actor?: VoiceActorOut | null; sourceBookId?: string | null }>()

const emit = defineEmits<{
  (event: 'saved', actor: VoiceActorOut): void
}>()

const actors = useActorStore()
const message = useMessage()

const name = ref('')
const description = ref('')
const gender = ref('unknown')
const age = ref('adult')
const language = ref('zh-CN')
const timbreTags = ref('')
const personalityTags = ref('')
const referenceText = ref('')
const favorite = ref(false)
const error = ref<string | null>(null)

const isEdit = computed(() => Boolean(props.actor))
const isBookScoped = computed(() => Boolean(props.sourceBookId) && !isEdit.value)
const saving = computed(() => actors.creating || actors.saving)

const genderOptions = [
  { label: '男声', value: 'male' },
  { label: '女声', value: 'female' },
  { label: '中性', value: 'neutral' },
  { label: '未知', value: 'unknown' },
]
const ageOptions = [
  { label: '儿童', value: 'child' },
  { label: '少年', value: 'teenager' },
  { label: '青年', value: 'young_adult' },
  { label: '成年', value: 'adult' },
  { label: '中年', value: 'middle_aged' },
  { label: '老年', value: 'elderly' },
  { label: '未知', value: 'unknown' },
]

function reset(): void {
  const actor = props.actor
  name.value = actor?.display_name ?? ''
  description.value = actor?.description ?? ''
  gender.value = actor?.gender_presentation ?? 'unknown'
  age.value = actor?.age_impression ?? 'adult'
  language.value = actor?.language ?? 'zh-CN'
  timbreTags.value = actor?.timbre_tags.join('、') ?? ''
  personalityTags.value = actor?.personality_tags.join('、') ?? ''
  referenceText.value = actor?.reference_text ?? ''
  favorite.value = actor?.is_favorite ?? false
  error.value = null
}

function splitTags(value: string): string[] {
  return value
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function submit(): Promise<void> {
  const cleanName = name.value.trim()
  const cleanDescription = description.value.trim()
  if (!cleanName) {
    error.value = '请填写配音员名字'
    return
  }
  if (!cleanDescription) {
    error.value = '请填写声音描述，声音设计会用它生成参考音'
    return
  }
  error.value = null
  try {
    if (props.actor) {
      const updated = await actors.update(props.actor.id, {
        display_name: cleanName,
        description: cleanDescription,
        gender_presentation: gender.value as 'male' | 'female' | 'neutral' | 'unknown',
        age_impression: age.value as
          | 'child'
          | 'teenager'
          | 'young_adult'
          | 'adult'
          | 'middle_aged'
          | 'elderly'
          | 'unknown',
        timbre_tags: splitTags(timbreTags.value),
        personality_tags: splitTags(personalityTags.value),
        is_favorite: favorite.value,
      })
      emit('saved', updated)
      show.value = false
      message.success('配音员资料已更新')
      return
    }
    const payload: VoiceActorCreatePayload = {
      display_name: cleanName,
      description: cleanDescription,
      gender_presentation: gender.value as VoiceActorCreatePayload['gender_presentation'],
      age_impression: age.value as VoiceActorCreatePayload['age_impression'],
      language: language.value.trim() || 'zh-CN',
      timbre_tags: splitTags(timbreTags.value),
      personality_tags: splitTags(personalityTags.value),
      reference_text: referenceText.value.trim() || null,
      is_favorite: favorite.value,
      source_book_id: isBookScoped.value ? props.sourceBookId : null,
    }
    const created = await actors.create(payload)
    emit('saved', created)
    show.value = false
    message.success(`配音员“${created.display_name}”已生成参考音`)
  } catch (err) {
    error.value = errorMessage(err)
  }
}

watch(show, (visible) => {
  if (visible) {
    reset()
  }
})
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    :title="isEdit ? '编辑配音员' : isBookScoped ? '新建本书专属配音员' : '新建通用配音员'"
    :bordered="false"
    :style="{ width: '640px', maxWidth: 'calc(100vw - 32px)' }"
  >
    <div class="space-y-4">
      <NAlert v-if="error" type="error" :bordered="false">{{ error }}</NAlert>

      <NAlert v-if="!isEdit" type="info" :bordered="false">
        <p class="text-[11px] leading-5">
          保存时会调用本机声音设计服务生成参考音。模拟模式下返回测试音；真实模式首次加载模型可能需要等待。
          {{ isBookScoped ? '这是书籍专属配音员，仅能分配给当前书。' : '这是通用配音员，可供所有书籍使用。' }}
        </p>
      </NAlert>
      <NAlert v-else type="warning" :bordered="false">
        <p class="text-[11px] leading-5">
          修改名字、描述或标签不会重新生成参考音。要换声音，请新建一位配音员。
        </p>
      </NAlert>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">名字 *</span>
          <NInput v-model:value="name" :maxlength="120" placeholder="例如：温润青年 / 冷峻旁白" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">语言</span>
          <NInput v-model:value="language" :maxlength="32" :disabled="isEdit" />
        </label>
      </div>

      <label class="block">
        <span class="mb-1.5 flex items-center justify-between text-[11px] text-fog-300">
          <span>声音描述 *</span>
          <span class="text-[9px] text-fog-500">用于声音设计，不是角色简介</span>
        </span>
        <NInput
          v-model:value="description"
          type="textarea"
          :rows="3"
          :maxlength="2000"
          show-count
          placeholder="例如：三十岁男性，偏低沉温润的中音，语速稍慢，尾音柔和，适合古风叙述"
        />
      </label>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">性别呈现</span>
          <NSelect v-model:value="gender" :options="genderOptions" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">年龄印象</span>
          <NSelect v-model:value="age" :options="ageOptions" />
        </label>
      </div>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">音色标签</span>
          <NInput v-model:value="timbreTags" placeholder="低沉、温润、清亮（逗号或顿号分隔）" />
        </label>
        <label class="block">
          <span class="mb-1.5 block text-[11px] text-fog-300">性格标签</span>
          <NInput v-model:value="personalityTags" placeholder="沉稳、克制、温柔" />
        </label>
      </div>

      <label v-if="!isEdit" class="block">
        <span class="mb-1.5 flex items-center justify-between text-[11px] text-fog-300">
          <span>参考文本</span>
          <span class="text-[9px] text-fog-500">留空使用设置里的默认参考文本</span>
        </span>
        <NInput
          v-model:value="referenceText"
          type="textarea"
          :rows="2"
          :maxlength="500"
          placeholder="用于生成参考音的朗读文本"
        />
      </label>

      <NCheckbox v-model:checked="favorite">
        <span class="text-[11px] text-fog-400">创建后加入收藏，选角时优先显示</span>
      </NCheckbox>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <NButton :disabled="saving" @click="show = false">取消</NButton>
        <NButton type="primary" :loading="saving" @click="submit">
          {{ isEdit ? '保存修改' : '生成参考音并保存' }}
        </NButton>
      </div>
    </template>
  </NModal>
</template>