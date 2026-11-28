<script setup lang="ts">
import { NButton } from 'naive-ui'

import type { CastCharacterPlanOut, CastStagePlanOut } from '@/api/types'
import { AGE_LABELS, GENDER_LABELS, labelOf, percent } from '@/utils/analysis'

const props = withDefaults(
  defineProps<{ character: CastCharacterPlanOut; editable?: boolean }>(),
  { editable: false },
)
const emit = defineEmits<{
  (event: 'assign-base'): void
  (event: 'assign-stage', stage: CastStagePlanOut): void
}>()

function castMeta(stage: CastStagePlanOut): string {
  const actor = stage.cast?.actor
  if (!actor) return ''
  return `${labelOf(GENDER_LABELS, actor.gender_presentation)} · ${labelOf(AGE_LABELS, actor.age_impression)}`
}
</script>

<template>
  <div class="mt-2 space-y-1.5 border-t border-ink-700/70 pt-2.5">
    <div class="flex flex-wrap items-center gap-2 text-[10px]">
      <span class="w-16 shrink-0 text-fog-500">基础声线</span>
      <template v-if="character.base_cast?.actor">
        <span class="text-fog-200">{{ character.base_cast.actor.display_name }}</span>
        <span class="text-fog-500">{{ castMeta({ age_stage: '', label: '', cast: character.base_cast }) }}</span>
        <span class="font-mono text-[9px] text-fog-600">{{ percent(character.base_cast.confidence) }}</span>
        <audio
          v-if="character.base_cast.actor.audio_url"
          :src="character.base_cast.actor.audio_url"
          controls
          preload="none"
          class="h-8 w-full max-w-md sm:ml-auto sm:w-40"
          :aria-label="`${character.name} 基础声线试听`"
        />
      </template>
      <span v-else class="text-fog-500">
        {{ character.cast_required ? '尚未绑定基础配音员' : '该角色不需要单独配音' }}
      </span>
      <NButton
        v-if="editable && (character.cast_required || character.base_cast)"
        size="tiny"
        quaternary
        class="ml-auto"
        @click="emit('assign-base')"
      >
        {{ character.base_cast ? '更换基础' : '选择基础' }}
      </NButton>
    </div>

    <template v-if="character.stages.length">
      <div
        v-for="stage in character.stages"
        :key="stage.age_stage"
        class="rounded-lg bg-ink-900/60 px-2.5 py-2"
      >
        <div class="flex flex-wrap items-center gap-2 text-[10px]">
          <span class="w-16 shrink-0 text-fog-300">{{ stage.label }}</span>
          <template v-if="stage.cast?.actor">
            <span class="text-fog-200">{{ stage.cast.actor.display_name }}</span>
            <span class="text-fog-500">{{ castMeta(stage) }}</span>
            <span class="font-mono text-[9px] text-fog-600">{{ percent(stage.cast.confidence) }}</span>
            <audio
              v-if="stage.cast.actor.audio_url"
              :src="stage.cast.actor.audio_url"
              controls
              preload="none"
              class="h-8 w-full max-w-md sm:ml-auto sm:w-40"
              :aria-label="`${character.name} ${stage.label}声线试听`"
            />
          </template>
          <span v-else class="text-fog-500">
            {{ character.base_cast ? `继承基础 · ${character.base_cast.actor?.display_name ?? '已设基础声线'}` : '尚未指定声线' }}
          </span>
          <NButton v-if="editable" size="tiny" quaternary class="ml-auto" @click="emit('assign-stage', stage)">
            {{ stage.cast ? '更换阶段' : '阶段指定' }}
          </NButton>
        </div>
      </div>
    </template>
  </div>
</template>
