<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NModal, NSpin, NTag, useMessage } from 'naive-ui'

import { api } from '@/api/client'
import type {
  ScriptUnitOut,
  UnitAudioAssetOut,
  UnitAudioVersionListResponse,
} from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime } from '@/utils/format'
import { AUDIO_STATUS_LABELS, UNIT_TYPE_LABELS } from '@/utils/production'

const props = defineProps<{
  show: boolean
  unit: ScriptUnitOut | null
}>()

const emit = defineEmits<{
  (event: 'update:show', value: boolean): void
  (event: 'changed'): void
}>()

const message = useMessage()
const data = ref<UnitAudioVersionListResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const restoringId = ref<string | null>(null)
let loadToken = 0

const title = computed(() => {
  if (!props.unit) {
    return '单元音频版本'
  }
  const type = UNIT_TYPE_LABELS[props.unit.unit_type] ?? props.unit.unit_type
  return `单元音频版本 · ${type} #${String(props.unit.unit_index + 1).padStart(3, '0')}`
})

const subtitle = computed(() => {
  if (!props.unit) {
    return ''
  }
  return props.unit.speaker_name || (props.unit.speaker_type === 'narrator' ? '旁白' : '')
})

function close(): void {
  emit('update:show', false)
}

function durationLabel(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

async function load(): Promise<void> {
  const unit = props.unit
  if (!unit) {
    data.value = null
    return
  }
  const token = ++loadToken
  loading.value = true
  error.value = null
  try {
    const response = await api.listScriptUnitAudioVersions(unit.id)
    if (token === loadToken) {
      data.value = response
    }
  } catch (err) {
    if (token === loadToken) {
      error.value = errorMessage(err)
    }
  } finally {
    if (token === loadToken) {
      loading.value = false
    }
  }
}

async function restore(asset: UnitAudioAssetOut): Promise<void> {
  const unit = props.unit
  if (!unit || restoringId.value || asset.is_current) {
    return
  }
  restoringId.value = asset.id
  try {
    const result = await api.restoreScriptUnitAudioVersion(unit.id, asset.id)
    if (result.audio_status === 'stale') {
      message.warning('已切回该版本；当前文本或声线与它不一致，已标记为待重新生成')
    } else {
      message.success(`已切回 v${asset.version}`)
    }
    await load()
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    restoringId.value = null
  }
}

watch(
  () => [props.show, props.unit?.id] as const,
  ([show]) => {
    if (show) {
      void load()
    } else {
      loadToken += 1
      data.value = null
      error.value = null
      restoringId.value = null
    }
  },
  { immediate: true },
)
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="title"
    :bordered="false"
    :style="{ width: '700px', maxWidth: 'calc(100vw - 32px)' }"
    @update:show="emit('update:show', $event)"
  >
    <div class="mb-3 flex flex-wrap items-center gap-2 text-[10px] text-fog-500">
      <span v-if="subtitle">{{ subtitle }}</span>
      <NTag
        v-if="unit"
        size="tiny"
        :bordered="false"
        :type="unit.audio_status === 'ready' ? 'success' : 'warning'"
      >
        {{ AUDIO_STATUS_LABELS[unit.audio_status] ?? unit.audio_status }}
      </NTag>
      <span v-if="unit?.voice_actor_name">声线：{{ unit.voice_actor_name }}</span>
      <span class="ml-auto">切回旧版本不会删除新版本；输入不一致时会标记为待重新生成。</span>
    </div>

    <NAlert v-if="error" type="error" :bordered="false" class="mb-3">
      {{ error }}
    </NAlert>

    <div v-if="loading" class="flex items-center justify-center py-12 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取音频版本…
    </div>

    <div v-else-if="data" class="space-y-2">
      <p v-if="!data.items.length" class="py-8 text-center text-[11px] text-fog-500">
        还没有生成记录。关闭后点击单元上的“生成音频”。
      </p>
      <div
        v-for="asset in data.items"
        :key="asset.id"
        class="rounded-lg border px-3 py-2.5"
        :class="
          asset.id === data.current_asset_id
            ? 'border-brass-600/35 bg-brass-500/[0.06]'
            : 'border-ink-700 bg-ink-900/50'
        "
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-mono text-[11px] text-fog-200">
            v{{ String(asset.version).padStart(3, '0') }}
          </span>
          <NTag
            v-if="asset.id === data.current_asset_id"
            size="tiny"
            :bordered="false"
            type="success"
          >
            当前使用
          </NTag>
          <span class="font-mono text-[9px] text-fog-500">
            {{ durationLabel(asset.duration_ms) }} · {{ asset.sample_rate }} Hz ·
            {{ formatBytes(asset.file_size_bytes) }}
          </span>
          <span v-if="asset.voice_actor_name" class="text-[9px] text-fog-500">
            {{ asset.voice_actor_name }}
          </span>
          <span class="ml-auto text-[9px] text-fog-600">
            {{ formatDateTime(asset.created_at) }}
          </span>
        </div>
        <div class="mt-1.5 flex items-center gap-2">
          <audio
            :src="api.assetAudioUrl(asset)"
            controls
            preload="none"
            class="h-8 min-w-0 flex-1"
          />
          <NButton
            size="tiny"
            :disabled="asset.id === data.current_asset_id"
            :loading="restoringId === asset.id"
            @click="restore(asset)"
          >
            {{ asset.id === data.current_asset_id ? '使用中' : '切回此版' }}
          </NButton>
        </div>
        <p class="mt-1 font-mono text-[9px] text-fog-600">
          {{ asset.tts_provider || 'unknown' }} · {{ asset.tts_model || '—' }}
        </p>
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end">
        <NButton @click="close">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>