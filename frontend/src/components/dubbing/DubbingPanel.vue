<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NModal,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type {
  AudioVersionListResponse,
  SegmentAudioOut,
  SegmentOut,
} from '@/api/types'
import { useVoiceStore } from '@/stores/voices'
import {
  DELIVERY_LABELS,
  EMOTION_LABELS,
  INTENSITY_LABELS,
  labelOf,
} from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { formatDateTime, formatNumber, truncate } from '@/utils/format'

const props = defineProps<{
  bookId: string
  chapterId: string | null
}>()

const emit = defineEmits<{ (event: 'analyze'): void }>()

const router = useRouter()
const message = useMessage()
const voices = useVoiceStore()

const segments = ref<SegmentOut[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const generatingId = ref<string | null>(null)
const filter = ref<'all' | 'ready' | 'pending' | 'failed'>('all')

const showVersions = ref(false)
const versionSegment = ref<SegmentOut | null>(null)
const versionData = ref<AudioVersionListResponse | null>(null)
const versionsLoading = ref(false)
const restoringId = ref<string | null>(null)

let segmentsToken = 0

const readyCount = computed(
  () => segments.value.filter((item) => item.audio_status === 'ready').length,
)
const failedCount = computed(
  () => segments.value.filter((item) => item.audio_status === 'failed').length,
)
const missingVoiceSegments = computed(() =>
  segments.value.filter((item) => {
    if (item.speaker_type === 'unknown') {
      return true
    }
    return !selectedVoice(item)
  }),
)

const filterOptions = [
  { label: '全部片段', value: 'all' },
  { label: '已生成', value: 'ready' },
  { label: '未生成 / 缺音色', value: 'pending' },
  { label: '生成失败', value: 'failed' },
]

const filteredSegments = computed(() => {
  if (filter.value === 'ready') {
    return segments.value.filter((item) => item.audio_status === 'ready')
  }
  if (filter.value === 'failed') {
    return segments.value.filter((item) => item.audio_status === 'failed')
  }
  if (filter.value === 'pending') {
    return segments.value.filter(
      (item) => item.audio_status !== 'ready' || !selectedVoice(item),
    )
  }
  return segments.value
})

const progressPercent = computed(() =>
  segments.value.length ? Math.round((readyCount.value / segments.value.length) * 100) : 0,
)

function selectedVoice(segment: SegmentOut) {
  if (segment.speaker_type === 'narrator') {
    return voices.selectedProfile('narrator', props.bookId)
  }
  if (segment.speaker_type === 'character') {
    return voices.selectedProfile('character', segment.speaker_id)
  }
  return null
}

function speakerLabel(segment: SegmentOut): string {
  if (segment.speaker_type === 'narrator') {
    return '旁白'
  }
  if (segment.speaker_type === 'character') {
    const owner = voices.ownerFor('character', segment.speaker_id)
    return owner?.name ?? segment.speaker_name_snapshot ?? '未命名角色'
  }
  return '未知说话人'
}

function audioStatusLabel(status: string): string {
  if (status === 'ready') {
    return '已生成'
  }
  if (status === 'failed') {
    return '失败'
  }
  if (status === 'stale') {
    return '待重生成'
  }
  if (status === 'generating') {
    return '生成中'
  }
  return '未生成'
}

function audioStatusTone(status: string): 'success' | 'error' | 'warning' | 'default' {
  if (status === 'ready') {
    return 'success'
  }
  if (status === 'failed') {
    return 'error'
  }
  if (status === 'stale' || status === 'generating') {
    return 'warning'
  }
  return 'default'
}

function segmentAudioSrc(segment: SegmentOut): string {
  return api.segmentAudioUrl(
    segment.id,
    segment.current_audio_asset_id ?? segment.updated_at,
  )
}

function emotionLabel(segment: SegmentOut): string {
  const emotion = labelOf(EMOTION_LABELS, segment.emotion)
  const intensity = INTENSITY_LABELS[segment.intensity]
  const delivery = labelOf(DELIVERY_LABELS, segment.delivery)
  return `${emotion}${intensity ? ` · ${intensity}` : ''}${delivery !== '—' && delivery !== '正常' ? ` · ${delivery}` : ''}`
}

function durationLabel(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

async function loadSegments(chapterId: string | null): Promise<void> {
  const token = ++segmentsToken
  segments.value = []
  error.value = null
  if (!chapterId) {
    return
  }
  loading.value = true
  try {
    const response = await api.listSegments(chapterId)
    if (token === segmentsToken) {
      segments.value = response.items
    }
  } catch (err) {
    if (token === segmentsToken) {
      error.value = errorMessage(err)
    }
  } finally {
    if (token === segmentsToken) {
      loading.value = false
    }
  }
}

async function ensureVoices(): Promise<void> {
  if (voices.library?.book_id === props.bookId) {
    return
  }
  try {
    await voices.load(props.bookId)
  } catch {
    // 音色库失败时仍可查看片段，生成按钮会提示未绑定
  }
}

function applySegmentAudio(result: SegmentAudioOut): void {
  const index = segments.value.findIndex((item) => item.id === result.segment_id)
  if (index < 0) {
    return
  }
  const current = segments.value[index] as SegmentOut
  segments.value[index] = {
    ...current,
    audio_status: result.audio_status,
    voice_profile_id: result.voice_profile_id,
    current_audio_asset_id: result.asset?.id ?? current.current_audio_asset_id,
    updated_at: result.asset?.created_at ?? current.updated_at,
  }
  if (versionSegment.value?.id === result.segment_id) {
    versionSegment.value = segments.value[index] ?? versionSegment.value
  }
}

async function generate(segment: SegmentOut): Promise<void> {
  if (generatingId.value) {
    return
  }
  if (segment.speaker_type === 'unknown') {
    message.warning('该片段说话人不明确，请先在“片段审核”中确认')
    return
  }
  if (!selectedVoice(segment)) {
    message.warning('该说话人尚未绑定音色，请先到声音库绑定')
    return
  }
  generatingId.value = segment.id
  error.value = null
  try {
    const result = await api.generateSegmentAudio(segment.id)
    applySegmentAudio(result)
    message.success(
      result.asset
        ? `已生成 v${String(result.asset.version).padStart(3, '0')} · ${durationLabel(result.asset.duration_ms)}`
        : '已生成音频',
    )
  } catch (err) {
    error.value = errorMessage(err)
    message.error(error.value)
    const index = segments.value.findIndex((item) => item.id === segment.id)
    if (index >= 0) {
      segments.value[index] = {
        ...(segments.value[index] as SegmentOut),
        audio_status: 'failed',
      }
    }
  } finally {
    generatingId.value = null
  }
}

async function openVersions(segment: SegmentOut): Promise<void> {
  versionSegment.value = segment
  versionData.value = null
  showVersions.value = true
  versionsLoading.value = true
  try {
    versionData.value = await api.listAudioVersions(segment.id)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    versionsLoading.value = false
  }
}

async function restore(assetId: string): Promise<void> {
  if (restoringId.value) {
    return
  }
  restoringId.value = assetId
  try {
    const result = await api.restoreAudioAsset(assetId)
    applySegmentAudio(result)
    if (versionSegment.value) {
      versionData.value = await api.listAudioVersions(versionSegment.value.id)
    }
    message.success('已切换到该版本')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    restoringId.value = null
  }
}

function gotoVoices(): void {
  void router.push({ path: '/voices', query: { bookId: props.bookId } })
}

watch(
  () => props.chapterId,
  (chapterId) => {
    void loadSegments(chapterId)
  },
  { immediate: true },
)

watch(
  () => props.bookId,
  () => {
    void ensureVoices()
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="shrink-0 border-b border-ink-700/70 bg-ink-900/30 px-4 py-3 md:px-6">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div class="min-w-0">
          <p class="eyebrow">DUBBING / 配音</p>
          <p class="mt-0.5 text-[11px] text-fog-400">
            每个片段可单独生成、试听并回退历史版本
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-[10px] text-fog-500">
          <NButton size="tiny" quaternary @click="gotoVoices">声音库</NButton>
          <NButton size="tiny" quaternary :disabled="!segments.length" @click="emit('analyze')">
            重跑本章分析
          </NButton>
        </div>
        <div class="ml-auto flex min-w-[200px] flex-1 items-center gap-3 sm:max-w-sm">
          <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700">
            <div
              class="h-full rounded-full bg-brass-400 transition-all"
              :style="{ width: `${progressPercent}%` }"
            />
          </div>
          <span class="font-mono text-[9px] text-fog-400">
            {{ readyCount }} / {{ segments.length }}
          </span>
          <NSelect
            v-model:value="filter"
            size="tiny"
            :options="filterOptions"
            class="w-36"
          />
        </div>
      </div>

      <div class="mt-2 flex flex-wrap items-center gap-2 text-[9px]">
        <NTag v-if="missingVoiceSegments.length" size="tiny" :bordered="false" type="warning">
          {{ missingVoiceSegments.length }} 段缺音色 / 待确认说话人
        </NTag>
        <NTag v-if="failedCount" size="tiny" :bordered="false" type="error">
          {{ failedCount }} 段失败
        </NTag>
        <NTag v-if="!voices.library && !voices.loading" size="tiny" :bordered="false" type="error">
          音色库未加载
        </NTag>
        <span v-else-if="voices.loading" class="flex items-center gap-1.5 text-fog-500">
          <NSpin size="small" />
          读取音色库
        </span>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-5xl px-4 py-4 md:px-6">
        <NAlert v-if="error" type="error" :bordered="false" class="mb-3">
          <div class="flex items-center justify-between gap-3">
            <span class="text-[11px]">{{ error }}</span>
            <NButton v-if="chapterId" size="tiny" quaternary @click="loadSegments(chapterId)">
              重试
            </NButton>
          </div>
        </NAlert>

        <div v-if="loading" class="flex items-center justify-center py-24 text-xs text-fog-400">
          <NSpin size="small" class="mr-3" />
          正在读取片段…
        </div>

        <div
          v-else-if="!segments.length"
          class="flex flex-col items-center justify-center rounded-xl border border-dashed border-ink-600 px-6 py-20 text-center"
        >
          <div class="grid h-11 w-11 place-items-center rounded-full border border-ink-600 text-brass-400/80">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4 13a8 8 0 0 1 16 0" />
              <path d="M4 13v4a2 2 0 0 0 2 2h1v-6H6a2 2 0 0 0-2 2Z" />
              <path d="M20 13v3a3 3 0 0 1-3 3h-3" />
            </svg>
          </div>
          <h3 class="mt-4 font-display text-lg text-fog-200">本章还没有可配音的片段</h3>
          <p class="mt-2 max-w-md text-[11px] leading-5 text-fog-500">
            先运行 AI 分析，生成旁白、角色对白和情绪标注，再回到这里逐段生成语音。
          </p>
          <NButton class="mt-4" size="small" type="primary" @click="emit('analyze')">
            分析本章
          </NButton>
        </div>

        <div v-else class="space-y-2">
          <article
            v-for="segment in filteredSegments"
            :key="segment.id"
            class="rounded-xl border border-ink-700/80 bg-ink-850/45 px-3.5 py-3 transition-colors hover:border-ink-600"
          >
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span class="font-mono text-[9px] text-fog-600">
                #{{ String(segment.segment_index + 1).padStart(2, '0') }}
              </span>
              <span class="text-[11px] font-medium text-fog-200">{{ speakerLabel(segment) }}</span>
              <NTag size="tiny" :bordered="false">
                {{ emotionLabel(segment) }}
              </NTag>
              <NTag size="tiny" :bordered="false" :type="audioStatusTone(segment.audio_status)">
                {{ audioStatusLabel(segment.audio_status) }}
              </NTag>
              <span v-if="!selectedVoice(segment)" class="text-[9px] text-rust-400">
                未绑定音色
              </span>
              <span v-else class="text-[9px] text-fog-600">
                {{ selectedVoice(segment)?.name }}
              </span>
              <span class="ml-auto font-mono text-[9px] text-fog-600">
                {{ segment.review_status === 'needs_review' ? '待审核' : '' }}
              </span>
            </div>

            <p class="mt-1.5 text-[12px] leading-5 text-fog-300" :title="segment.speech_text">
              {{ truncate(segment.speech_text, 160) }}
            </p>

            <div class="mt-2 flex flex-wrap items-center gap-2">
              <audio
                v-if="segment.current_audio_asset_id"
                :src="segmentAudioSrc(segment)"
                controls
                preload="none"
                class="h-8 min-w-[240px] flex-1"
              />
              <span v-else class="flex-1 text-[10px] text-fog-600">
                生成后可在这里直接播放
              </span>

              <NButton
                size="tiny"
                secondary
                :type="segment.audio_status === 'ready' ? 'default' : 'primary'"
                :loading="generatingId === segment.id"
                :disabled="Boolean(generatingId) || !selectedVoice(segment) || segment.speaker_type === 'unknown'"
                @click="generate(segment)"
              >
                {{ generatingId === segment.id ? '生成中' : segment.audio_status === 'ready' ? '重新生成' : '生成语音' }}
              </NButton>
              <NButton
                v-if="segment.current_audio_asset_id"
                size="tiny"
                quaternary
                @click="openVersions(segment)"
              >
                版本
              </NButton>
            </div>
          </article>

          <p v-if="!filteredSegments.length" class="py-10 text-center text-[11px] text-fog-500">
            当前筛选下没有片段
          </p>
        </div>
      </div>
    </div>

    <NModal
      v-model:show="showVersions"
      preset="card"
      :title="versionSegment ? `音频版本 · #${versionSegment.segment_index + 1}` : '音频版本'"
      :bordered="false"
      :style="{ width: '640px', maxWidth: 'calc(100vw - 32px)' }"
    >
      <div v-if="versionsLoading" class="flex items-center justify-center py-12 text-xs text-fog-400">
        <NSpin size="small" class="mr-3" />
        正在读取版本…
      </div>
      <div v-else-if="versionData" class="space-y-2">
        <p v-if="!versionData.items.length" class="py-8 text-center text-[11px] text-fog-500">
          还没有生成记录
        </p>
        <div
          v-for="asset in versionData.items"
          :key="asset.id"
          class="rounded-lg border px-3 py-2.5"
          :class="
            asset.id === versionData.current_asset_id
              ? 'border-brass-600/35 bg-brass-500/[0.06]'
              : 'border-ink-700 bg-ink-900/50'
          "
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-mono text-[11px] text-fog-200">
              v{{ String(asset.version).padStart(3, '0') }}
            </span>
            <NTag v-if="asset.id === versionData.current_asset_id" size="tiny" :bordered="false" type="success">
              当前使用
            </NTag>
            <span class="font-mono text-[9px] text-fog-500">
              {{ durationLabel(asset.duration_ms) }} · {{ asset.sample_rate }} Hz ·
              {{ formatNumber(asset.file_size_bytes) }} B
            </span>
            <span class="ml-auto text-[9px] text-fog-600">
              {{ formatDateTime(asset.created_at) }}
            </span>
          </div>
          <div class="mt-1.5 flex items-center gap-2">
            <audio :src="api.assetAudioUrl(asset)" controls preload="none" class="h-8 min-w-0 flex-1" />
            <NButton
              size="tiny"
              :disabled="asset.id === versionData.current_asset_id"
              :loading="restoringId === asset.id"
              @click="restore(asset.id)"
            >
              {{ asset.id === versionData.current_asset_id ? '使用中' : '切回此版' }}
            </NButton>
          </div>
          <p class="mt-1 font-mono text-[9px] text-fog-600">
            {{ asset.tts_provider || 'unknown' }} · {{ asset.tts_model || '—' }}
          </p>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end">
          <NButton @click="showVersions = false">关闭</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>