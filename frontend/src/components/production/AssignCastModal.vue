<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  NAlert,
  NButton,
  NCheckbox,
  NInput,
  NModal,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type {
  BookCastsResponse,
  CastCharacterPlanOut,
  CastStagePlanOut,
  VoiceActorOut,
} from '@/api/types'
import { useActorStore } from '@/stores/actors'
import { AGE_LABELS, GENDER_LABELS, labelOf } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'

const show = defineModel<boolean>('show', { required: true })

const props = defineProps<{
  bookId: string
  scope: 'narrator' | 'character' | 'character_stage'
  character?: CastCharacterPlanOut | null
  stage?: CastStagePlanOut | null
  currentActorId?: string | null
}>()

const emit = defineEmits<{
  (event: 'assigned', response: BookCastsResponse): void
}>()

const actors = useActorStore()
const message = useMessage()

const search = ref('')
const favoritesOnly = ref(false)
const selectedId = ref<string | null>(null)
const saving = ref(false)
const error = ref<string | null>(null)

const filtered = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return actors.items.filter((actor) => {
    if (favoritesOnly.value && !actor.is_favorite) {
      return false
    }
    if (!keyword) {
      return true
    }
    const haystack = [
      actor.display_name,
      actor.description ?? '',
      actor.timbre_tags.join(' '),
      actor.personality_tags.join(' '),
    ]
      .join(' ')
      .toLowerCase()
    return haystack.includes(keyword)
  })
})

const title = computed(() => {
  if (props.scope === 'narrator') {
    return '选择旁白配音员'
  }
  const name = props.character?.name ?? '角色'
  if (props.scope === 'character_stage') {
    return `选择 ${name} · ${props.stage?.label ?? props.stage?.age_stage ?? '阶段'} 配音员`
  }
  return `选择 ${name} 的配音员`
})

const hint = computed(() => {
  if (props.scope === 'narrator') {
    return '旁白会朗读所有非对白单元，也是全书声音的底色。'
  }
  if (props.scope === 'character_stage') {
    return '阶段选角只覆盖该年龄阶段；没有阶段绑定时会使用角色的基础配音员。'
  }
  return '基础选角适用于该角色的全部年龄阶段；阶段绑定会优先。'
})

async function loadActors(): Promise<void> {
  error.value = null
  try {
    await actors.load({ q: '', favorite: undefined, status: 'ready', limit: 200 })
  } catch (err) {
    error.value = errorMessage(err)
  }
}

function actorMeta(actor: VoiceActorOut): string {
  return `${labelOf(GENDER_LABELS, actor.gender_presentation)} · ${labelOf(AGE_LABELS, actor.age_impression)} · ${actor.language}`
}

async function confirm(): Promise<void> {
  if (!selectedId.value) {
    message.warning('请先选择一位配音员')
    return
  }
  saving.value = true
  error.value = null
  try {
    const response = await api.assignCast(props.bookId, {
      scope: props.scope,
      voice_actor_id: selectedId.value,
      character_id:
        props.scope === 'narrator' ? null : props.character?.character_id ?? null,
      age_stage_condition:
        props.scope === 'character_stage'
          ? props.stage?.age_stage ?? null
          : null,
      reason: '制作台手动选角',
    })
    emit('assigned', response)
    show.value = false
    message.success('选角已更新')
  } catch (err) {
    error.value = errorMessage(err)
    message.error(errorMessage(err))
  } finally {
    saving.value = false
  }
}

watch(show, (visible) => {
  if (!visible) {
    return
  }
  selectedId.value = props.currentActorId ?? null
  search.value = ''
  favoritesOnly.value = false
  error.value = null
  void loadActors()
})
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    :title="title"
    :bordered="false"
    :style="{ width: '760px', maxWidth: 'calc(100vw - 32px)' }"
  >
    <p class="text-[11px] leading-5 text-fog-500">{{ hint }}</p>

    <div class="mt-3 flex flex-wrap items-center gap-3">
      <NInput
        v-model:value="search"
        clearable
        size="small"
        placeholder="搜索名字 / 描述 / 标签"
        class="min-w-[180px] flex-1"
      />
      <NCheckbox v-model:checked="favoritesOnly">
        <span class="text-[11px] text-fog-400">只看收藏</span>
      </NCheckbox>
      <NButton size="small" quaternary :loading="actors.loading" @click="loadActors">
        刷新
      </NButton>
    </div>

    <NAlert v-if="error" type="error" :bordered="false" class="mt-3">{{ error }}</NAlert>

    <div v-if="actors.loading && !actors.items.length" class="flex items-center justify-center py-16 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取配音员库…
    </div>

    <div
      v-else-if="!filtered.length"
      class="mt-3 rounded-lg border border-dashed border-ink-600 px-5 py-10 text-center"
    >
      <p class="text-[11px] text-fog-300">没有可用的配音员。</p>
      <p class="mt-1 text-[10px] text-fog-500">
        配音员需要在声音库中生成参考音并处于 ready 状态。
      </p>
      <RouterLink
        to="/voices"
        class="mt-3 inline-block rounded-lg border border-brass-600/40 bg-brass-500/10 px-3 py-1.5 text-[11px] text-brass-300"
      >
        去声音库新建
      </RouterLink>
    </div>

    <div v-else class="mt-3 max-h-[420px] space-y-1.5 overflow-y-auto pr-1">
      <div
        v-for="actor in filtered"
        :key="actor.id"
        role="button"
        tabindex="0"
        class="flex w-full cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors focus-visible:border-brass-500/70 focus-visible:outline-none"
        :class="
          selectedId === actor.id
            ? 'border-brass-500/60 bg-brass-500/[0.07]'
            : 'border-ink-700 bg-ink-900/50 hover:border-ink-600'
        "
        @click="selectedId = actor.id"
        @keydown.enter.prevent="selectedId = actor.id"
        @keydown.space.prevent="selectedId = actor.id"
      >
        <span
          class="mt-1 grid h-5 w-5 shrink-0 place-items-center rounded-full border text-[9px]"
          :class="
            selectedId === actor.id
              ? 'border-brass-400 text-brass-300'
              : 'border-ink-600 text-fog-600'
          "
        >
          {{ selectedId === actor.id ? '✓' : '' }}
        </span>
        <span class="min-w-0 flex-1">
          <span class="flex flex-wrap items-center gap-1.5">
            <span class="text-[12px] font-medium text-fog-100">{{ actor.display_name }}</span>
            <NTag v-if="actor.is_mock" size="tiny" :bordered="false" type="warning">MOCK</NTag>
            <NTag v-if="actor.is_favorite" size="tiny" :bordered="false" type="success">收藏</NTag>
            <span class="ml-auto font-mono text-[9px] text-fog-600">
              用于 {{ actor.usage.active_cast_count }} 个角色
            </span>
          </span>
          <span class="mt-0.5 block text-[10px] text-fog-500">{{ actorMeta(actor) }}</span>
          <span class="mt-0.5 line-clamp-1 block text-[10px] text-fog-400">
            {{ actor.description || '暂无描述' }}
          </span>
          <span v-if="actor.timbre_tags.length" class="mt-1 flex flex-wrap gap-1">
            <span
              v-for="tag in actor.timbre_tags.slice(0, 6)"
              :key="tag"
              class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-fog-400"
            >
              {{ tag }}
            </span>
          </span>
          <audio
            v-if="actor.audio_url"
            :src="actor.audio_url"
            controls
            preload="none"
            class="mt-1.5 h-8 w-full max-w-md"
            @click.stop
          />
        </span>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between gap-3">
        <span class="text-[10px] text-fog-500">
          {{ filtered.length }} 位可用配音员
        </span>
        <div class="flex gap-2">
          <NButton :disabled="saving" @click="show = false">取消</NButton>
          <NButton type="primary" :loading="saving" :disabled="!selectedId" @click="confirm">
            绑定到{{ scope === 'narrator' ? '旁白' : '角色' }}
          </NButton>
        </div>
      </div>
    </template>
  </NModal>
</template>