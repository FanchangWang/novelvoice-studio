<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NSelect,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type {
  CharacterOut,
  ProductionChapterOut,
  SceneStateResponse,
  ScriptReviewItemOut,
  ScriptStateResponse,
  ScriptUnitOut,
  ScriptVersionOut,
} from '@/api/types'
import ChapterPlaylist from '@/components/production/ChapterPlaylist.vue'
import UnitAudioVersionsModal from '@/components/production/UnitAudioVersionsModal.vue'
import { useProductionStore } from '@/stores/production'
import {
  DELIVERY_LABELS,
  EMOTION_LABELS,
  INTENSITY_LABELS,
  SPEAKER_TYPE_LABELS,
  isJobActive,
  labelOf,
  percent,
} from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { formatDateTime } from '@/utils/format'
import {
  ADAPTATION_LEVEL_LABELS,
  AUDIO_STATUS_LABELS,
  REVIEW_SEVERITY_LABELS,
  SCRIPT_VERSION_STATUS_LABELS,
  STAGE_STATUS_LABELS,
  UNIT_TYPE_LABELS,
  audioStatusTagType,
  chapterDisplayTitle,
  jobStatusLabel,
  jobTypeLabel,
  severityTagType,
  statusTagType,
} from '@/utils/production'

const props = defineProps<{
  bookId: string
  chapters: ProductionChapterOut[]
  chapterId: string | null
  focusUnitId?: string | null
  autoScriptRunning?: boolean
  autoProductionActive?: boolean
  autoScriptStage?: string
}>()

const emit = defineEmits<{
  (event: 'update:chapterId', value: string | null): void
  (event: 'changed'): void
  (event: 'open-reviews', chapterId: string): void
}>()

const production = useProductionStore()
const message = useMessage()

const state = ref<ScriptStateResponse | null>(null)
const scenes = ref<SceneStateResponse | null>(null)
const versions = ref<ScriptVersionOut[]>([])
const characters = ref<CharacterOut[]>([])
const charactersBookId = ref<string | null>(null)
const loading = ref(false)
const actionLoading = ref(false)
const error = ref<string | null>(null)
type ScriptFilter =
  | 'all'
  | 'needs_review'
  | 'dialogue'
  | 'narration'
  | 'audio_pending'
const filter = ref<ScriptFilter>('all')
const showVersions = ref(false)
const audioActionUnitId = ref<string | null>(null)
const showAudioVersions = ref(false)
const versionUnit = ref<ScriptUnitOut | null>(null)
const speakerActionUnitId = ref<string | null>(null)

const TTS_TYPES = new Set(['narration', 'dialogue', 'inner_monologue'])

let pollTimer: ReturnType<typeof setTimeout> | null = null
let lastJobActive = false
let loadToken = 0

const includedChapters = computed(() =>
  props.chapters.filter((chapter) => chapter.analysis_included),
)
const chapterOptions = computed(() =>
  includedChapters.value.map((chapter) => ({
    label: `${chapterDisplayTitle(chapter.sequence, chapter.title)} · ${
      STAGE_STATUS_LABELS[chapter.script_status] ?? chapter.script_status
    }`,
    value: chapter.id,
  })),
)
const selectedChapter = computed(
  () => includedChapters.value.find((chapter) => chapter.id === props.chapterId) ?? null,
)
const job = computed(() => state.value?.job ?? null)
const jobActive = computed(() => isJobActive(job.value?.status))
const autoProductionActive = computed(() => Boolean(props.autoProductionActive || props.autoScriptRunning))
const waitingForAuto = computed(() => jobActive.value || (autoProductionActive.value && !units.value.length))
const needsSceneRetry = computed(() => !selectedChapter.value?.scene_count)
const generateLabel = computed(() => {
  if (jobActive.value) return '正在生成剧本'
  if (autoProductionActive.value) return units.value.length ? '自动制作进行中' : '等待自动生成'
  if (units.value.length) return '重新生成剧本'
  return needsSceneRetry.value ? '重新分析并生成剧本' : '生成本章剧本'
})
const units = computed(() => state.value?.units ?? [])
const activeVersion = computed(() => state.value?.version ?? null)
const reviewItems = computed(() => state.value?.review_items ?? [])
const actionableReviewCount = computed(() => reviewItems.value.length)
const needsReviewUnitIds = computed(() => new Set(
  reviewItems.value.flatMap((item) => {
    if (item.entity_type === 'script_unit' && item.entity_id) return [item.entity_id]
    const rows = Array.isArray(item.payload.units) ? item.payload.units : []
    return rows.flatMap((row) => {
      if (!row || typeof row !== 'object') return []
      const index = (row as Record<string, unknown>).unit_index
      const unit = units.value.find((candidate) => candidate.unit_index === index)
      return unit ? [unit.id] : []
    })
  }),
))
const needsReview = computed(() => state.value?.script_status === 'needs_review')
const speakerOptions = computed(() => [
  { label: '旁白', value: 'narrator' },
  ...characters.value
    .filter((character) => !character.merged_into_id)
    .map((character) => ({ label: character.canonical_name, value: character.id })),
  { label: '仍未知', value: 'unknown' },
])

function selectedSpeakerValue(unit: ScriptUnitOut): string {
  if (unit.speaker_type === 'character' && unit.speaker_id) return unit.speaker_id
  if (unit.speaker_type === 'narrator') return 'narrator'
  return 'unknown'
}

function openChapterReviews(): void {
  emit('open-reviews', props.chapterId ?? '')
}

const filteredUnits = computed(() => {
  if (filter.value === 'all') {
    return units.value
  }
  if (filter.value === 'needs_review') {
    return units.value.filter((unit) => needsReviewUnitIds.value.has(unit.id))
  }
  if (filter.value === 'audio_pending') {
    return units.value.filter(
      (unit) => isTtsUnit(unit) && unit.audio_status !== 'ready',
    )
  }
  return units.value.filter((unit) => unit.unit_type === filter.value)
})

const ttsCount = computed(
  () => units.value.filter((unit) => isTtsUnit(unit)).length,
)
const cueCount = computed(() => units.value.length - ttsCount.value)
const readyAudioCount = computed(
  () =>
    units.value.filter(
      (unit) => isTtsUnit(unit) && unit.audio_status === 'ready',
    ).length,
)
const staleAudioCount = computed(
  () =>
    units.value.filter(
      (unit) => isTtsUnit(unit) && unit.audio_status === 'stale',
    ).length,
)
const missingVoiceCount = computed(
  () => units.value.filter((unit) => isTtsUnit(unit) && !unit.voice_actor_id).length,
)
const pendingAudioCount = computed(
  () =>
    units.value.filter(
      (unit) =>
        isTtsUnit(unit) &&
        !['ready', 'stale'].includes(unit.audio_status),
    ).length,
)

const filterOptions: Array<{ label: string; value: ScriptFilter }> = [
  { label: '全部单元', value: 'all' },
  { label: '内容问题行', value: 'needs_review' },
  { label: '待生成音频', value: 'audio_pending' },
  { label: '对白', value: 'dialogue' },
  { label: '旁白', value: 'narration' },
]

function stopPolling(): void {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll(): void {
  if (!jobActive.value || pollTimer) {
    return
  }
  pollTimer = setTimeout(async () => {
    pollTimer = null
    await load(true).catch(() => undefined)
    schedulePoll()
  }, 1800)
}

async function load(silent = false): Promise<void> {
  const chapterId = props.chapterId
  if (!chapterId || !selectedChapter.value) {
    loadToken += 1
    stopPolling()
    lastJobActive = false
    state.value = null
    scenes.value = null
    versions.value = []
    return
  }
  const token = ++loadToken
  if (!silent) {
    loading.value = true
  }
  error.value = null
  try {
    const characterRequest =
      charactersBookId.value === props.bookId
        ? Promise.resolve(null)
        : api.listCharacters(props.bookId)
    const [response, versionResponse, sceneResponse, characterResponse] = await Promise.all([
      api.getScript(chapterId),
      api.listScriptVersions(chapterId),
      api.getScenes(chapterId),
      characterRequest,
    ])
    if (token !== loadToken) {
      return
    }
    state.value = response
    scenes.value = sceneResponse
    versions.value = versionResponse.items
    if (characterResponse) {
      characters.value = characterResponse.items
      charactersBookId.value = props.bookId
    }
    const active = isJobActive(response.job?.status)
    if (active) {
      schedulePoll()
    } else {
      stopPolling()
      if (lastJobActive) {
        emit('changed')
        await production.refresh()
      } else if (response.job?.status === 'failed' && response.job.error_message) {
        error.value = response.job.error_message
      }
    }
    lastJobActive = active
  } catch (err) {
    if (token === loadToken) {
      error.value = errorMessage(err)
    }
    throw err
  } finally {
    if (token === loadToken) {
      loading.value = false
    }
  }
}

async function start(): Promise<void> {
  const chapterId = props.chapterId
  if (!chapterId || !selectedChapter.value) {
    return
  }
  actionLoading.value = true
  error.value = null
  try {
    await api.startScript(chapterId, needsSceneRetry.value)
    message.success(needsSceneRetry.value ? '已开始重新分析本章并生成剧本' : '已开始生成有声剧本')
    await load(true)
    await production.refresh()
  } catch (err) {
    error.value = errorMessage(err)
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

async function cancel(): Promise<void> {
  const chapterId = props.chapterId
  if (!chapterId) {
    return
  }
  actionLoading.value = true
  try {
    await api.cancelScript(chapterId)
    message.success('已请求取消剧本任务')
    await load(true)
    await production.refresh()
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

async function activate(version: ScriptVersionOut, approve: boolean): Promise<void> {
  actionLoading.value = true
  try {
    if (approve) {
      await api.approveScriptVersion(version.id)
      message.success(`剧本 v${version.version} 已审核通过并设为当前版本`)
    } else {
      await api.rollbackScriptVersion(version.id)
      message.success(`已切换到剧本 v${version.version}`)
    }
    await load(true)
    await production.refresh()
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function unitText(unit: ScriptUnitOut): string {
  return unit.display_text || unit.text
}

function unitTone(unit: ScriptUnitOut): string {
  if (needsReviewUnitIds.value.has(unit.id)) {
    return 'border-brass-600/35 bg-brass-500/[0.04]'
  }
  if (unit.unit_type === 'dialogue') {
    return 'border-ink-700 bg-ink-850/60'
  }
  return 'border-ink-700/70 bg-ink-900/40'
}

function isTtsUnit(unit: ScriptUnitOut): boolean {
  return TTS_TYPES.has(unit.unit_type)
}

function unitTypeLabel(unit: ScriptUnitOut): string {
  return UNIT_TYPE_LABELS[unit.unit_type] ?? unit.unit_type
}

function speakerLabel(unit: ScriptUnitOut): string {
  if (unit.speaker_name) {
    return unit.speaker_name
  }
  if (unit.speaker_type) {
    return SPEAKER_TYPE_LABELS[unit.speaker_type] ?? unit.speaker_type
  }
  return ''
}

function showSpeaker(unit: ScriptUnitOut): boolean {
  const label = speakerLabel(unit)
  return Boolean(label) && label !== unitTypeLabel(unit)
}

function audioLabel(unit: ScriptUnitOut): string {
  return AUDIO_STATUS_LABELS[unit.audio_status] ?? unit.audio_status
}

function unitAudioSrc(unit: ScriptUnitOut): string {
  return api.scriptUnitAudioUrl(
    unit.id,
    unit.current_audio_asset_id ?? unit.updated_at,
  )
}

function audioActionLabel(unit: ScriptUnitOut): string {
  if (audioActionUnitId.value === unit.id) {
    return '生成中'
  }
  if (unit.audio_status === 'failed') {
    return '重试'
  }
  if (unit.audio_status === 'ready' || unit.audio_status === 'stale') {
    return '重新生成'
  }
  return '生成音频'
}

async function generateAudio(unit: ScriptUnitOut): Promise<void> {
  if (audioActionUnitId.value || !unit.voice_actor_id) {
    return
  }
  audioActionUnitId.value = unit.id
  try {
    const result = await api.generateScriptUnitAudio(unit.id)
    message.success(
      result.audio_status === 'ready'
        ? '音频已生成，可直接试听'
        : '音频已生成，但当前输入已变化，已标记为待重新生成',
    )
    await load(true)
    await production.refresh()
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    audioActionUnitId.value = null
  }
}

function openAudioVersions(unit: ScriptUnitOut): void {
  versionUnit.value = unit
  showAudioVersions.value = true
}

async function handleAudioVersionsChanged(): Promise<void> {
  await load(true)
  await production.refresh()
  emit('changed')
}

async function updateSpeaker(unit: ScriptUnitOut, value: string): Promise<void> {
  speakerActionUnitId.value = unit.id
  try {
    const character = value !== 'narrator' && value !== 'unknown'
    await api.updateScriptUnitSpeaker(
      unit.id,
      character ? 'character' : (value as 'narrator' | 'unknown'),
      character ? value : null,
    )
    message.success('说话人已确认，相关音频已标记为需要重新生成')
    await load(true)
    await production.refresh()
    emit('changed')
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    speakerActionUnitId.value = null
  }
}

async function focusUnit(unitId: string): Promise<void> {
  filter.value = 'all'
  await nextTick()
  document.getElementById(`script-unit-${unitId}`)?.scrollIntoView({
    behavior: 'smooth',
    block: 'center',
  })
}

function focusReview(item: ScriptReviewItemOut): void {
  const first = (item.payload.units as Array<Record<string, unknown>> | undefined)?.[0]
  const index = first?.unit_index
  const unit = units.value.find((candidate) => candidate.unit_index === index)
  if (unit) {
    void focusUnit(unit.id)
  } else {
    message.info('该问题没有对应到具体剧本行，请从异常收件箱查看原文证据。')
  }
}

watch(
  () => [props.focusUnitId, units.value],
  () => {
    if (props.focusUnitId) void focusUnit(props.focusUnitId)
  },
)

watch(
  () => [props.chapterId, includedChapters.value.map((chapter) => chapter.id)] as const,
  ([chapterId, includedIds]) => {
    if (chapterId !== null && includedIds.includes(chapterId)) {
      return
    }
    const nextChapterId = includedIds[0] ?? null
    if (nextChapterId !== chapterId) {
      emit('update:chapterId', nextChapterId)
    }
  },
  { immediate: true },
)

watch(
  () => props.chapterId,
  () => {
    stopPolling()
    lastJobActive = false
    state.value = null
    scenes.value = null
    versions.value = []
    if (props.chapterId) {
      void load().catch(() => undefined)
    }
  },
)

onMounted(() => {
  if (props.chapterId) {
    void load().catch(() => undefined)
  }
})

onBeforeUnmount(() => {
  stopPolling()
  loadToken += 1
})
</script>

<template>
  <div class="space-y-4">
    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="eyebrow">SCRIPT / 有声剧本</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <h2 class="font-display text-xl text-fog-100">剧本标记与表演提示</h2>
            <button
              v-if="state && needsReview"
              type="button"
              class="nv-focus rounded"
              aria-label="审核本章剧本"
              title="查看本章内容异常"
              @click="openChapterReviews"
            >
              <NTag size="small" :bordered="false" :type="statusTagType(state.script_status)">
                {{ STAGE_STATUS_LABELS[state.script_status] ?? state.script_status }} · 查看异常
              </NTag>
            </button>
            <NTag
              v-else-if="state"
              size="small"
              :bordered="false"
              :type="statusTagType(state.script_status)"
            >
              {{ STAGE_STATUS_LABELS[state.script_status] ?? state.script_status }}
            </NTag>
            <NTag v-if="activeVersion" size="small" :bordered="false">
              v{{ activeVersion.version }} ·
              {{ ADAPTATION_LEVEL_LABELS[activeVersion.adaptation_level] ?? activeVersion.adaptation_level }}
            </NTag>
            <button
              v-if="state?.open_reviews"
              type="button"
              class="nv-focus rounded"
              :aria-label="needsReview ? '查看本章剧本阻断异常' : '查看本章待处理内容问题'"
              @click="openChapterReviews"
            >
              <NTag size="small" :bordered="false" :type="needsReview ? 'error' : 'warning'">
                {{ needsReview ? `待处理内容问题 ${actionableReviewCount}` : `内容问题 ${actionableReviewCount}` }}
              </NTag>
            </button>
          </div>
          <p class="mt-2 max-w-2xl text-[11px] leading-5 text-fog-500">
            剧本把原文转成旁白、对白、内心独白与音效 cue，并为每个单元标注说话人、情绪与表演方式。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <NSelect
            :value="chapterId"
            :options="chapterOptions"
            size="small"
            class="w-64"
            placeholder="选择章节"
            :disabled="!chapterOptions.length"
            @update:value="emit('update:chapterId', $event)"
          />
          <NButton size="small" quaternary :loading="loading" @click="load()">刷新</NButton>
          <template v-if="chapterOptions.length">
            <NButton
              v-if="jobActive"
              size="small"
              secondary
              :loading="actionLoading"
              @click="cancel"
            >
              取消
            </NButton>
            <NButton
              v-else-if="!autoProductionActive"
              size="small"
              type="primary"
              :loading="actionLoading"
              @click="start"
            >
              {{ generateLabel }}
            </NButton>
            <span v-else class="px-2 text-[10px] text-brass-300">自动制作进行中</span>
          </template>
        </div>
      </div>

      <div
        v-if="state && actionableReviewCount"
        class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2"
        :class="needsReview
          ? 'border-rust-500/35 bg-rust-500/[0.06]'
          : 'border-lake-500/25 bg-lake-500/[0.05]'"
      >
        <p class="text-[11px] leading-5" :class="needsReview ? 'text-rust-400' : 'text-fog-400'">
          <template v-if="needsReview">
            本章有 {{ actionableReviewCount }} 个待处理内容问题，请先查看定位与处理建议。
          </template>
          <template v-else>
            本章有 {{ actionableReviewCount }} 个需要你判断的内容问题；点击条目可定位到原文或剧本具体行。
          </template>
        </p>
        <NButton
          size="tiny"
          :type="needsReview ? 'primary' : 'default'"
          :quaternary="!needsReview"
          @click="openChapterReviews"
        >
          {{ needsReview ? '去审核本章异常' : '查看内容问题' }}
        </NButton>
      </div>

      <div v-if="job" class="mt-3 rounded-lg border border-ink-700 bg-ink-900/50 px-3.5 py-2.5">
        <div class="flex flex-wrap items-center gap-3">
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="jobActive ? 'animate-pulse bg-brass-400' : job.status === 'failed' ? 'bg-rust-500' : 'bg-moss-400'"
          />
          <span class="text-[11px] text-fog-300">
            {{ jobTypeLabel(job.job_type) }} · {{ jobStatusLabel(job.status) }} ·
            {{ Math.round((job.progress || 0) * 100) }}%
          </span>
          <span class="text-[10px] text-fog-500">{{ job.completed_items }} / {{ job.total_items }}</span>
          <span v-if="job.error_message" class="text-[10px] text-rust-400">{{ job.error_message }}</span>
        </div>
        <div v-if="jobActive" class="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
          <div
            class="h-full rounded-full bg-brass-400 transition-all"
            :style="{ width: `${Math.round((job.progress || 0) * 100)}%` }"
          />
        </div>
      </div>

      <NAlert v-if="error" type="error" :bordered="false" class="mt-3">{{ error }}</NAlert>
    </section>

    <section v-if="scenes?.scenes.length" class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p class="eyebrow">CHAPTER ANALYSIS / 本章分析</p>
          <h3 class="mt-1 font-display text-base text-fog-100">本章场景与剧情概要</h3>
        </div>
        <NTag size="small" :bordered="false">{{ scenes.scenes.length }} 个场景</NTag>
      </div>
      <div class="mt-3 space-y-2">
        <article
          v-for="scene in scenes.scenes"
          :key="scene.id"
          class="rounded-lg border border-ink-700/70 bg-ink-900/40 px-3 py-2.5"
        >
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-fog-500">
            <span class="font-mono text-brass-300">{{ String(scene.scene_index + 1).padStart(2, '0') }}</span>
            <span v-if="scene.location">地点：{{ scene.location }}</span>
            <span v-if="scene.story_time_label">时间：{{ scene.story_time_label }}</span>
            <span v-if="scene.pov_character_name">视角：{{ scene.pov_character_name }}</span>
            <span v-if="scene.mood">情绪：{{ scene.mood }}</span>
          </div>
          <p v-if="scene.title" class="mt-1 text-[12px] font-medium text-fog-200">{{ scene.title }}</p>
          <p v-if="scene.summary" class="mt-1 text-[11px] leading-5 text-fog-400">{{ scene.summary }}</p>
        </article>
      </div>
    </section>

    <ChapterPlaylist v-if="units.length" :units="units" />

    <div v-if="!chapterOptions.length" class="rounded-xl border border-dashed border-ink-700 px-6 py-16 text-center text-[11px] text-fog-500">
      没有纳入制作的章节，请先在章节页确认正文
    </div>

    <div v-else-if="!chapterId" class="rounded-xl border border-dashed border-ink-700 px-6 py-16 text-center text-[11px] text-fog-500">
      请选择纳入制作的章节。
    </div>

    <div v-else-if="loading && !state" class="flex items-center justify-center py-24 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取剧本…
    </div>

    <div
      v-else-if="!units.length"
      class="flex min-h-[280px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
    >
      <NSpin v-if="waitingForAuto" size="small" class="mb-4" />
      <h3 class="font-display text-lg text-fog-200">
        {{ waitingForAuto ? '本章正在等待自动生成' : '本章还没有有声剧本' }}
      </h3>
      <p class="mt-2 max-w-md text-[11px] leading-6 text-fog-500">
        <template v-if="waitingForAuto">
          自动制作正在逐章处理，本章完成后会直接显示剧本；已完成的章节可以随时切换查看。
        </template>
        <template v-else>
          生成时会保留原文，并按轻改编原则只做朗读所需的最小调整。完成后可在这里检查每个单元的说话人与表演提示。
        </template>
      </p>
      <NButton
        v-if="!autoProductionActive && !jobActive"
        class="mt-4"
        type="primary"
        :loading="actionLoading"
        @click="start"
      >
        {{ generateLabel }}
      </NButton>
    </div>

    <template v-else>
      <div class="flex flex-wrap items-center gap-2">
        <NSelect
          :value="filter"
          :options="filterOptions"
          size="small"
          class="w-36"
          @update:value="filter = $event"
        />
        <span class="text-sm text-fog-500">
          {{ filteredUnits.length }} 个单元 · 可配音 {{ ttsCount }} · cue {{ cueCount }}
        </span>
        <span aria-hidden="true" class="text-ink-600">·</span>
        <span class="text-sm text-moss-400">
          已生成 {{ readyAudioCount }} / {{ ttsCount }}
        </span>
        <template v-if="pendingAudioCount">
          <span aria-hidden="true" class="text-ink-600">·</span>
          <span class="text-sm text-fog-500">待生成 {{ pendingAudioCount }}</span>
        </template>
        <template v-if="staleAudioCount">
          <span aria-hidden="true" class="text-ink-600">·</span>
          <span class="text-sm text-brass-300">待重生成 {{ staleAudioCount }}</span>
        </template>
        <template v-if="missingVoiceCount">
          <span aria-hidden="true" class="text-ink-600">·</span>
          <span class="text-sm text-rust-400">未选角 {{ missingVoiceCount }}</span>
        </template>
        <div class="ml-auto flex items-center gap-2">
          <NButton v-if="reviewItems.length" size="tiny" quaternary @click="openChapterReviews">
            {{ needsReview ? '审核本章异常' : '查看本章内容问题' }}
          </NButton>
          <NButton size="tiny" quaternary @click="showVersions = !showVersions">
            {{ showVersions ? '收起版本' : `版本历史（${versions.length}）` }}
          </NButton>
        </div>
      </div>

      <section v-if="showVersions" class="nv-card rounded-xl px-4 py-3">
        <div v-if="!versions.length" class="py-4 text-center text-[10px] text-fog-500">
          暂无历史版本。
        </div>
        <div v-else class="space-y-1.5">
          <div
            v-for="version in versions"
            :key="version.id"
            class="flex flex-wrap items-center gap-2 rounded-lg border px-2.5 py-2 text-[10px]"
            :class="version.is_active ? 'border-brass-600/35 bg-brass-500/[0.05]' : 'border-ink-700 bg-ink-900/50'"
          >
            <span class="font-mono text-fog-300">v{{ version.version }}</span>
            <NTag size="tiny" :bordered="false" :type="statusTagType(version.status)">
              {{ SCRIPT_VERSION_STATUS_LABELS[version.status] ?? version.status }}
            </NTag>
            <NTag v-if="version.is_active" size="tiny" :bordered="false" type="success">
              当前版本
            </NTag>
            <span class="text-fog-500">{{ version.unit_count }} 个单元 · {{ ADAPTATION_LEVEL_LABELS[version.adaptation_level] ?? version.adaptation_level }}</span>
            <span class="text-fog-600">{{ formatDateTime(version.created_at) }}</span>
            <div class="ml-auto flex gap-1">
              <NButton
                v-if="!version.is_active"
                size="tiny"
                quaternary
                :loading="actionLoading"
                @click="activate(version, false)"
              >
                设为当前
              </NButton>
              <NButton
                v-if="version.status !== 'approved'"
                size="tiny"
                secondary
                :loading="actionLoading"
                @click="activate(version, true)"
              >
                审核通过
              </NButton>
            </div>
          </div>
        </div>
      </section>

      <div v-if="activeVersion && activeVersion.source_scene_version !== selectedChapter?.scene_version" class="rounded-lg border border-brass-600/30 bg-brass-500/[0.06] px-3.5 py-2 text-[10px] leading-5 text-brass-200">
        当前剧本基于分场 v{{ activeVersion.source_scene_version ?? '无' }}，而本章现在使用分场
        v{{ selectedChapter?.scene_version }}。建议重新生成剧本，避免说话人与状态不一致。
      </div>

      <div class="space-y-2.5">
        <article
          v-for="unit in filteredUnits"
          :key="unit.id"
          :id="`script-unit-${unit.id}`"
          class="scroll-mt-24 rounded-xl border px-4 py-3.5 transition-shadow"
          :class="[
            unitTone(unit),
            focusUnitId === unit.id ? 'ring-2 ring-brass-400/70' : '',
          ]"
        >
          <div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
              <span class="font-mono text-fog-500">{{ String(unit.unit_index + 1).padStart(3, '0') }}</span>
              <NTag size="tiny" :bordered="false" type="info">
                {{ unitTypeLabel(unit) }}
              </NTag>
              <span v-if="showSpeaker(unit)" class="font-medium text-fog-100">
                {{ speakerLabel(unit) }}
              </span>
              <span v-if="isTtsUnit(unit)" class="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1">
                <span class="text-fog-500">声线</span>
                <span v-if="unit.voice_actor_name" class="text-fog-300">
                  {{ unit.voice_actor_name }}
                </span>
                <span v-else class="text-rust-400">未分配</span>
              </span>
              <NTag
                v-if="isTtsUnit(unit) && unit.audio_status !== 'none'"
                size="tiny"
                :bordered="false"
                :type="audioStatusTagType(unit.audio_status)"
              >
                音频 {{ audioLabel(unit) }}
              </NTag>
            </div>
            <span class="shrink-0 text-sm text-fog-500">
              置信度 <span class="font-mono">{{ percent(unit.confidence) }}</span>
            </span>
          </div>

          <div
            v-if="unit.emotion !== 'neutral' || unit.delivery !== 'normal' || needsReviewUnitIds.has(unit.id)"
            class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-fog-500"
          >
            <span v-if="unit.emotion !== 'neutral'" class="inline-flex items-center gap-1.5">
              <span>情绪</span>
              <NTag size="tiny" :bordered="false">
                {{ EMOTION_LABELS[unit.emotion] ?? unit.emotion }}
                · {{ INTENSITY_LABELS[unit.intensity] ?? unit.intensity }}
              </NTag>
            </span>
            <span v-if="unit.delivery !== 'normal'" class="inline-flex items-center gap-1.5">
              <span>表演</span>
              <NTag size="tiny" :bordered="false" type="warning">
                {{ DELIVERY_LABELS[unit.delivery] ?? unit.delivery }}
              </NTag>
            </span>
            <NTag v-if="needsReviewUnitIds.has(unit.id)" size="tiny" :bordered="false" type="warning">
              内容问题
            </NTag>
          </div>

          <p
            class="mt-2.5 whitespace-pre-wrap text-base leading-7"
            :class="unit.unit_type === 'dialogue' ? 'text-brass-200' : 'text-fog-200'"
          >
            {{ unitText(unit) }}
          </p>
          <p
            v-if="unit.source_text && unit.source_text !== unit.text"
            class="mt-1.5 line-clamp-2 text-fog-500"
          >
            原文：{{ unit.source_text }}
          </p>

          <div v-if="unit.unit_type === 'dialogue'" class="mt-2 flex flex-wrap items-center gap-2">
            <span class="text-[10px] text-fog-500">确认说话人</span>
            <NSelect
              :value="selectedSpeakerValue(unit)"
              :options="speakerOptions"
              size="tiny"
              class="w-40"
              :loading="speakerActionUnitId === unit.id"
              :disabled="Boolean(speakerActionUnitId)"
              @update:value="updateSpeaker(unit, $event)"
            />
            <span class="text-[10px] text-fog-600">修改后原音频保留为历史版本</span>
          </div>

          <div
            v-if="isTtsUnit(unit)"
            class="mt-2.5 flex flex-wrap items-center gap-2"
          >
            <audio
              v-if="unit.current_audio_asset_id"
              :src="unitAudioSrc(unit)"
              controls
              preload="none"
              class="h-9 min-w-[240px] flex-1"
            />
            <span v-else class="flex-1 text-fog-500">
              生成后可在这里直接试听；音效与停顿不进入 TTS。
            </span>
            <NButton
              size="tiny"
              secondary
              :type="unit.audio_status === 'ready' ? 'default' : 'primary'"
              :loading="audioActionUnitId === unit.id"
              :disabled="Boolean(audioActionUnitId) || !unit.voice_actor_id"
              @click="generateAudio(unit)"
            >
              {{ audioActionLabel(unit) }}
            </NButton>
            <NButton size="tiny" quaternary @click="openAudioVersions(unit)">
              版本
            </NButton>
          </div>
          <p
            v-if="isTtsUnit(unit) && unit.audio_status === 'stale'"
            class="mt-1.5 text-sm text-brass-300"
          >
            文本、表演或声线已变化，当前音频可继续试听，但建议重新生成。
          </p>
          <p
            v-else-if="isTtsUnit(unit) && unit.audio_status === 'failed'"
            class="mt-1.5 text-sm text-rust-400"
          >
            上次生成失败，这是系统任务故障；请在本章上方任务状态中查看原因，然后点击“重试”。
          </p>

          <div class="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-fog-500">
            <span v-if="unit.tts_instruction">提示：{{ unit.tts_instruction }}</span>
            <span v-if="unit.pause_before_ms || unit.pause_after_ms">
              停顿 {{ unit.pause_before_ms }}/{{ unit.pause_after_ms }}ms
            </span>
            <span v-if="unit.speed !== 1">语速 {{ unit.speed }}×</span>
          </div>
        </article>
      </div>

    </template>

    <section v-if="reviewItems.length" class="nv-card rounded-xl px-4 py-3.5">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 class="font-display text-base text-fog-100">
            {{ needsReview ? '本章待处理内容问题' : '本章内容问题' }}
          </h3>
          <p class="mt-0.5 text-[10px] text-fog-500">
            点击条目定位到问题行；只有 AI 无法可靠判断的内容问题会出现在这里。
          </p>
        </div>
        <NButton size="tiny" quaternary @click="openChapterReviews">去收件箱</NButton>
      </div>
      <div class="mt-2 space-y-1.5">
        <button
          v-for="item in reviewItems"
          :key="item.id"
          type="button"
          class="flex w-full items-start gap-2 rounded-lg bg-ink-900/60 px-2.5 py-2 text-left"
          :title="item.code"
          @click="focusReview(item)"
        >
          <NTag size="tiny" :bordered="false" :type="severityTagType(item.severity)">
            {{ REVIEW_SEVERITY_LABELS[item.severity] ?? item.severity }}
          </NTag>
          <span class="min-w-0 flex-1 text-[10px] leading-5 text-fog-300">{{ item.title }}</span>
          <span class="shrink-0 text-[9px] text-brass-300">定位问题行</span>
        </button>
      </div>
    </section>

    <UnitAudioVersionsModal
      v-model:show="showAudioVersions"
      :unit="versionUnit"
      @changed="handleAudioVersionsChanged"
    />
  </div>
</template>