<script setup lang="ts">
import { computed } from 'vue'
import {
  NButton,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type { VoiceOwnerOut, VoiceProfileOut } from '@/api/types'
import { useVoiceStore } from '@/stores/voices'
import { AGE_LABELS, GENDER_LABELS, labelOf } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'

const props = defineProps<{
  bookId: string
  owner: VoiceOwnerOut
}>()

const emit = defineEmits<{
  (event: 'design'): void
  (event: 'preview', voice: VoiceProfileOut): void
}>()

const voices = useVoiceStore()
const dialog = useDialog()
const message = useMessage()

const selected = computed<VoiceProfileOut | null>(() => {
  const id = props.owner.selected_profile_id
  return props.owner.profiles.find((item) => item.id === id) ?? null
})

const candidates = computed(() =>
  [...props.owner.profiles].sort((a, b) => {
    const left = Number(a.generation_params.candidate_index ?? 0)
    const right = Number(b.generation_params.candidate_index ?? 0)
    return left - right
  }),
)

const subtitle = computed(() => {
  if (props.owner.owner_type === 'narrator') {
    return '全书旁白'
  }
  const gender = labelOf(GENDER_LABELS, props.owner.gender)
  const age = labelOf(AGE_LABELS, props.owner.age_range)
  return `${gender} · ${age}`
})

function candidateLabel(profile: VoiceProfileOut): string {
  const index = Number(profile.generation_params.candidate_index ?? -1)
  return index >= 0 ? `候选 ${String(index + 1).padStart(2, '0')}` : '候选'
}

function processedLabel(profile: VoiceProfileOut): string {
  return profile.generation_params.reference_processed ? '参考已去齿' : '参考原始'
}

function durationLabel(profile: VoiceProfileOut): string {
  if (!profile.duration_ms) {
    return '—'
  }
  return `${(profile.duration_ms / 1000).toFixed(1)}s`
}

async function select(profile: VoiceProfileOut): Promise<void> {
  try {
    await voices.select(profile.id)
    message.success(`已绑定“${props.owner.name}”的音色`)
  } catch (err) {
    message.error(errorMessage(err))
  }
}

function remove(profile: VoiceProfileOut): void {
  dialog.warning({
    title: '删除音色候选',
    content: `将删除“${profile.name}”及其参考音频文件。已用于片段的音色不能删除。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await voices.remove(profile.id)
        message.success('候选已删除')
      } catch (err) {
        message.error(errorMessage(err))
      }
    },
  })
}
</script>

<template>
  <article class="nv-card rounded-xl p-4">
    <header class="flex items-start gap-3">
      <div
        class="grid h-10 w-10 shrink-0 place-items-center rounded-full border font-display text-base"
        :class="
          owner.owner_type === 'narrator'
            ? 'border-brass-600/50 bg-brass-500/10 text-brass-300'
            : 'border-ink-600 bg-ink-800 text-fog-200'
        "
      >
        {{ owner.name.slice(0, 1) }}
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="truncate font-display text-base text-fog-100">{{ owner.name }}</h3>
          <NTag size="small" :bordered="false" :type="owner.owner_type === 'narrator' ? 'warning' : 'default'">
            {{ owner.owner_type === 'narrator' ? '旁白' : '角色' }}
          </NTag>
          <NTag v-if="owner.has_selected" size="small" :bordered="false" type="success">
            已绑定
          </NTag>
        </div>
        <p class="mt-0.5 text-[10px] text-fog-500">
          {{ subtitle }} · {{ owner.profile_count }} 个候选
        </p>
      </div>
      <NButton size="tiny" secondary @click="emit('design')">
        {{ owner.profile_count ? '新增候选' : '设计音色' }}
      </NButton>
    </header>

    <p class="mt-3 line-clamp-2 text-[11px] leading-5 text-fog-400" :title="owner.description">
      {{ owner.description || '暂无音色描述，设计时可直接填写。' }}
    </p>

    <div v-if="selected" class="mt-3 rounded-lg border border-brass-600/25 bg-brass-500/[0.05] p-3">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-[11px] font-medium text-brass-200">当前绑定</span>
        <span class="truncate text-[11px] text-fog-200">{{ selected.name }}</span>
        <span v-if="selected.is_mock" class="rounded border border-brass-600/30 px-1 text-[9px] text-brass-300">
          MOCK
        </span>
        <span class="ml-auto font-mono text-[9px] text-fog-500">
          seed {{ selected.seed ?? '—' }} · {{ durationLabel(selected) }} · {{ processedLabel(selected) }}
        </span>
      </div>
      <audio
        :src="api.voiceAudioUrl(selected.id, selected.updated_at)"
        controls
        preload="none"
        class="mt-2 h-8 w-full"
      />
      <div class="mt-2 flex justify-end">
        <NButton size="tiny" @click="emit('preview', selected)">按情绪试听</NButton>
      </div>
    </div>

    <div v-else class="mt-3 rounded-lg border border-dashed border-ink-600 bg-ink-900/40 px-3 py-2.5">
      <p class="text-[10px] leading-5 text-fog-500">
        尚未绑定音色。绑定后，旁白或该角色的片段才能生成语音。
      </p>
    </div>

    <div v-if="candidates.length" class="mt-3 space-y-2">
      <p class="eyebrow">候选列表</p>
      <div
        v-for="profile in candidates"
        :key="profile.id"
        class="rounded-lg border px-3 py-2 transition-colors"
        :class="
          profile.is_selected
            ? 'border-brass-600/35 bg-brass-500/[0.06]'
            : 'border-ink-700/80 bg-ink-850/40'
        "
      >
        <div class="flex items-center gap-2">
          <span class="font-mono text-[9px] text-fog-500">{{ candidateLabel(profile) }}</span>
          <span class="min-w-0 flex-1 truncate text-[11px] text-fog-300">{{ profile.name }}</span>
          <NTag v-if="profile.is_mock" size="tiny" :bordered="false" type="warning">MOCK</NTag>
          <NTag v-if="profile.is_selected" size="tiny" :bordered="false" type="success">使用中</NTag>
          <NTooltip trigger="hover">
            <template #trigger>
              <span class="font-mono text-[9px] text-fog-600">{{ durationLabel(profile) }}</span>
            </template>
            {{ processedLabel(profile) }} · seed {{ profile.seed ?? '—' }}
          </NTooltip>
        </div>
        <div class="mt-1.5 flex items-center gap-2">
          <audio
            :src="api.voiceAudioUrl(profile.id, profile.updated_at)"
            controls
            preload="none"
            class="h-8 min-w-0 flex-1"
          />
          <NButton
            size="tiny"
            :secondary="!profile.is_selected"
            :disabled="profile.is_selected"
            @click="select(profile)"
          >
            {{ profile.is_selected ? '已绑定' : '绑定' }}
          </NButton>
          <NButton
            size="tiny"
            quaternary
            :disabled="profile.is_selected"
            @click="remove(profile)"
          >
            删除
          </NButton>
        </div>
      </div>
    </div>

    <p v-if="voices.error" class="mt-3 text-[10px] text-rust-400">{{ voices.error }}</p>
  </article>
</template>