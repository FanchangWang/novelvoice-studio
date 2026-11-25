<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NDynamicTags,
  NInput,
  NModal,
  NSelect,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import type {
  AgeRange,
  CharacterGender,
  CharacterOut,
  CharacterType,
  CharacterUpdatePayload,
} from '@/api/types'
import { useAnalysisStore } from '@/stores/analysis'
import {
  AGE_LABELS,
  BASIS_LABELS,
  CHARACTER_TYPE_LABELS,
  GENDER_LABELS,
  percent,
} from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'

const props = defineProps<{
  show: boolean
  character: CharacterOut | null
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'saved', value: CharacterOut): void
}>()

const analysis = useAnalysisStore()
const message = useMessage()
const dialog = useDialog()

const form = reactive({
  canonical_name: '',
  aliases: [] as string[],
  gender: 'unknown' as CharacterGender,
  age_range: 'unknown' as AgeRange,
  character_type: 'unknown' as CharacterType,
  identity_description: '',
  personality: [] as string[],
  personality_desc: '',
  appearance_desc: '',
  voice_description: '',
  is_provisional: false,
  user_locked: false,
})

const saving = ref(false)
const acceptingAlias = ref<string | null>(null)
const splittingId = ref<string | null>(null)
const savedCharacter = ref<CharacterOut | null>(null)

const current = computed(() => savedCharacter.value ?? props.character)

const genderOptions = Object.entries(GENDER_LABELS).map(([value, label]) => ({ value, label }))
const ageOptions = Object.entries(AGE_LABELS).map(([value, label]) => ({ value, label }))
const typeOptions = Object.entries(CHARACTER_TYPE_LABELS).map(([value, label]) => ({
  value,
  label,
}))

const mergedSources = computed(() => {
  const character = current.value
  if (!character) {
    return []
  }
  return character.merged_source_ids
    .map((id) => analysis.characters.find((item) => item.id === id))
    .filter((item): item is CharacterOut => Boolean(item))
})

const evidenceItems = computed(() => {
  const raw = current.value?.evidence ?? []
  return raw
    .map((item) => ({
      quote: typeof item.quote === 'string' ? item.quote : '',
      reason: typeof item.reason === 'string' ? item.reason : '',
    }))
    .filter((item) => item.quote || item.reason)
})

function resetForm(): void {
  const character = props.character
  savedCharacter.value = null
  if (!character) {
    return
  }
  form.canonical_name = character.canonical_name
  form.aliases = character.aliases.map((item) => item.name)
  form.gender = character.gender as CharacterGender
  form.age_range = character.age_range as AgeRange
  form.character_type = character.character_type as CharacterType
  form.identity_description = character.identity_description ?? ''
  form.personality = [...character.personality]
  form.personality_desc = character.personality_desc ?? ''
  form.appearance_desc = character.appearance_desc ?? ''
  form.voice_description = character.voice_description ?? ''
  form.is_provisional = character.is_provisional
  form.user_locked = character.user_locked
}

function close(): void {
  emit('update:show', false)
}

async function save(): Promise<void> {
  const character = current.value
  if (!character) {
    return
  }
  const name = form.canonical_name.trim()
  if (!name) {
    message.warning('角色名称不能为空')
    return
  }
  const payload: CharacterUpdatePayload = {
    canonical_name: name,
    aliases: form.aliases,
    gender: form.gender,
    age_range: form.age_range,
    character_type: form.character_type,
    identity_description: form.identity_description.trim() || null,
    personality: form.personality,
    personality_desc: form.personality_desc.trim() || null,
    appearance_desc: form.appearance_desc.trim() || null,
    voice_description: form.voice_description.trim() || null,
  }
  if (form.is_provisional !== character.is_provisional) {
    payload.is_provisional = form.is_provisional
  }
  if (form.user_locked !== character.user_locked) {
    payload.user_locked = form.user_locked
  }

  saving.value = true
  try {
    const updated = await analysis.saveCharacter(character.id, payload)
    savedCharacter.value = updated
    emit('saved', updated)
    close()
    message.success('角色资料已保存并锁定')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

async function acceptCandidate(name: string): Promise<void> {
  const character = current.value
  if (!character) {
    return
  }
  acceptingAlias.value = name
  try {
    const updated = await analysis.acceptAlias(character, name)
    form.aliases = updated.aliases.map((item) => item.name)
    savedCharacter.value = updated
    emit('saved', updated)
    message.success(`已接受别名「${name}」`)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    acceptingAlias.value = null
  }
}

function confirmSplit(source: CharacterOut): void {
  const character = current.value
  if (!character) {
    return
  }
  dialog.warning({
    title: '拆分角色',
    content: `将从「${character.canonical_name}」中拆出「${source.canonical_name}」，并恢复合并时记录的片段说话人与关系。`,
    positiveText: '拆分',
    negativeText: '取消',
    onPositiveClick: async () => {
      splittingId.value = source.id
      try {
        const count = await analysis.splitCharacter(character.id, source.id)
        const refreshed = analysis.characters.find((item) => item.id === character.id)
        if (refreshed) {
          savedCharacter.value = refreshed
          emit('saved', refreshed)
        }
        message.success(`已拆分「${source.canonical_name}」，恢复 ${count} 个片段`)
      } catch (err) {
        message.error(errorMessage(err))
      } finally {
        splittingId.value = null
      }
    },
  })
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
  () => props.character,
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
    title="角色资料"
    :bordered="false"
    :mask-closable="!saving"
    :style="{ width: '680px', maxWidth: 'calc(100vw - 32px)' }"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="current" class="space-y-4">
      <div class="flex flex-wrap items-center gap-2 text-[10px] text-fog-500">
        <NTag size="small" :bordered="false">
          置信度 {{ percent(current.confidence) }}
        </NTag>
        <NTag v-if="current.is_provisional" size="small" :bordered="false" type="warning">
          暂定角色
        </NTag>
        <NTag v-if="current.user_locked" size="small" :bordered="false" type="success">
          已人工锁定
        </NTag>
        <span v-if="current.gender_basis !== 'unknown'">
          性别依据：{{ BASIS_LABELS[current.gender_basis] ?? current.gender_basis }}
        </span>
      </div>

      <div class="grid gap-4 sm:grid-cols-2">
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">标准姓名</span>
          <NInput v-model:value="form.canonical_name" :maxlength="120" />
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">别名 / 称谓</span>
          <NDynamicTags v-model:value="form.aliases" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">性别</span>
          <NSelect v-model:value="form.gender" :options="genderOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">年龄段</span>
          <NSelect v-model:value="form.age_range" :options="ageOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">角色类型</span>
          <NSelect v-model:value="form.character_type" :options="typeOptions" />
        </label>
        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">资料锁定</span>
          <div class="flex h-8 items-center gap-2">
            <NSwitch v-model:value="form.user_locked" />
            <span class="text-[10px] text-fog-500">锁定后重跑分析不覆盖本角色</span>
          </div>
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">身份 / 叙事作用</span>
          <NInput
            v-model:value="form.identity_description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">性格标签</span>
          <NDynamicTags v-model:value="form.personality" />
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">性格简述</span>
          <NInput
            v-model:value="form.personality_desc"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 3 }"
          />
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">外貌简述</span>
          <NInput
            v-model:value="form.appearance_desc"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 3 }"
          />
        </label>
        <label class="block sm:col-span-2">
          <span class="mb-2 block text-[11px] text-fog-300">声音描述（阶段 3 音色设计参考）</span>
          <NInput
            v-model:value="form.voice_description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="例如：中年男声，音色低沉，语速偏慢"
          />
        </label>
        <div class="flex items-center justify-between gap-3 rounded-lg border border-ink-700 bg-ink-850/40 px-3 py-2 sm:col-span-2">
          <div>
            <p class="text-[11px] text-fog-200">暂定角色</p>
            <p class="text-[9px] text-fog-500">标记后仍会进入角色待确认列表。</p>
          </div>
          <NSwitch v-model:value="form.is_provisional" />
        </div>
      </div>

      <div v-if="current.alias_candidates.length" class="rounded-lg border border-brass-600/25 bg-brass-500/[0.05] p-3">
        <p class="text-[10px] font-semibold tracking-wide text-brass-300">待确认别名</p>
        <ul class="mt-2 space-y-2">
          <li
            v-for="candidate in current.alias_candidates"
            :key="candidate.name"
            class="flex items-center justify-between gap-3"
          >
            <div class="min-w-0">
              <p class="text-[11px] text-fog-200">
                {{ candidate.name }}
                <span class="ml-1 text-[9px] text-fog-500">
                  置信度 {{ percent(candidate.confidence) }}
                  · {{ BASIS_LABELS[candidate.basis] ?? candidate.basis }}
                </span>
              </p>
              <p v-if="candidate.reason" class="text-[9px] text-fog-500">{{ candidate.reason }}</p>
            </div>
            <NButton
              size="tiny"
              :loading="acceptingAlias === candidate.name"
              @click="acceptCandidate(candidate.name)"
            >
              接受
            </NButton>
          </li>
        </ul>
      </div>

      <div v-if="mergedSources.length" class="rounded-lg border border-ink-700 bg-ink-900/40 p-3">
        <p class="text-[10px] font-semibold tracking-wide text-fog-400">已合并角色</p>
        <ul class="mt-2 space-y-2">
          <li
            v-for="source in mergedSources"
            :key="source.id"
            class="flex items-center justify-between gap-3"
          >
            <div>
              <p class="text-[11px] text-fog-200">{{ source.canonical_name }}</p>
              <p class="text-[9px] text-fog-500">
                {{ source.identity_description || '无身份描述' }}
              </p>
            </div>
            <NButton
              size="tiny"
              :loading="splittingId === source.id"
              @click="confirmSplit(source)"
            >
              拆分恢复
            </NButton>
          </li>
        </ul>
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

      <NAlert v-if="analysis.error" type="error" :bordered="false">
        <span class="text-[11px]">{{ analysis.error }}</span>
      </NAlert>
    </div>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <NButton :disabled="saving" @click="close">取消</NButton>
        <NButton type="primary" :loading="saving" @click="save">保存资料</NButton>
      </div>
    </template>
  </NModal>
</template>