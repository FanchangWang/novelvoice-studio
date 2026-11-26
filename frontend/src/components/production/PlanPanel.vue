<script setup lang="ts">
import { computed } from 'vue'
import { NAlert, NButton, NTag, useMessage } from 'naive-ui'

import type { ProductionChapterOut, ProductionOverviewOut } from '@/api/types'
import { useProductionStore } from '@/stores/production'
import { errorMessage } from '@/utils/errors'
import { formatNumber } from '@/utils/format'
import {
  NEXT_ACTION_LABELS,
  PRODUCTION_STATUS_LABELS,
  STAGE_STATUS_LABELS,
  statusTagType,
  type ProductionTab,
} from '@/utils/production'

const props = defineProps<{ overview: ProductionOverviewOut }>()

const emit = defineEmits<{
  (event: 'navigate', tab: ProductionTab): void
  (
    event: 'open-chapter',
    chapterId: string,
    tab: 'scenes' | 'script',
  ): void
}>()

const production = useProductionStore()
const message = useMessage()

const scenePercent = computed(() =>
  props.overview.chapter_count
    ? Math.round(
        (props.overview.chapters_with_scenes / props.overview.chapter_count) * 100,
      )
    : 0,
)
const scriptPercent = computed(() =>
  props.overview.chapter_count
    ? Math.round(
        (props.overview.chapters_with_scripts / props.overview.chapter_count) * 100,
      )
    : 0,
)
const castPercent = computed(() => {
  const missing = props.overview.cast_count === 0
  if (missing) {
    return 0
  }
  return props.overview.narrator_ready ? 100 : 60
})
const audioPercent = computed(() =>
  props.overview.tts_unit_count
    ? Math.round(
        (props.overview.audio_ready_units / props.overview.tts_unit_count) * 100,
      )
    : 0,
)

const firstSceneChapter = computed(
  () =>
    props.overview.chapters.find(
      (chapter) => chapter.scene_status === 'not_started' || chapter.scene_count === 0,
    ) ??
    props.overview.chapters.find((chapter) => chapter.scene_status === 'needs_review') ??
    props.overview.chapters[0] ??
    null,
)
const firstScriptChapter = computed(
  () =>
    props.overview.chapters.find(
      (chapter) => chapter.scene_count > 0 && chapter.unit_count === 0,
    ) ??
    props.overview.chapters.find((chapter) => chapter.script_status === 'needs_review') ??
    props.overview.chapters[0] ??
    null,
)

const nextLabel = computed(
  () => NEXT_ACTION_LABELS[props.overview.next_action ?? 'done'] ?? '查看制作台',
)
const nextButtonLabel = computed(() => {
  switch (props.overview.next_action) {
    case 'bible':
      return '进入设定集'
    case 'scenes':
      return '打开分场'
    case 'script':
      return '打开剧本'
    case 'casting':
      return '进入选角'
    case 'reviews':
      return '处理异常'
    case 'audio':
      return '进入剧本与音频'
    case 'done':
      return '查看制作台'
    default:
      return '查看制作台'
  }
})
const nextHint = computed(() => {
  switch (props.overview.next_action) {
    case 'bible':
      return '先把全书人物、别名、人物弧与剧情时间线整理成设定集。'
    case 'scenes':
      return `还有 ${props.overview.chapter_count - props.overview.chapters_with_scenes} 章没有分场。分场会切出场景与角色状态。`
    case 'script':
      return `还有 ${props.overview.chapter_count - props.overview.chapters_with_scripts} 章没有有声剧本。`
    case 'casting':
      return '为旁白与角色分配全局配音员，选角完成后才进入音频生成。'
    case 'reviews':
      return `有 ${props.overview.blocking_reviews} 条阻断、${props.overview.warning_reviews} 条警告等待判断。`
    case 'audio':
      return '制作链已完成，下一步在工作台为选中的剧本单元生成音频并试听。'
    case 'done':
      return '设定集、分场、剧本、选角与音频都已完成。'
    default:
      return '先导入章节，再开始制作。'
  }
})

async function handleNext(): Promise<void> {
  const action = props.overview.next_action
  if (action === 'bible') {
    emit('navigate', 'bible')
    return
  }
  if (action === 'scenes') {
    if (firstSceneChapter.value) {
      emit('open-chapter', firstSceneChapter.value.id, 'scenes')
    } else {
      emit('navigate', 'scenes')
    }
    return
  }
  if (action === 'script') {
    if (firstScriptChapter.value) {
      emit('open-chapter', firstScriptChapter.value.id, 'script')
    } else {
      emit('navigate', 'script')
    }
    return
  }
  if (action === 'casting') {
    emit('navigate', 'casting')
    return
  }
  if (action === 'reviews') {
    emit('navigate', 'reviews')
    return
  }
  if (action === 'audio') {
    emit('navigate', 'script')
    return
  }
  message.info('制作流程已经完成')
}

async function cancelJob(): Promise<void> {
  try {
    await production.cancelCurrentJob()
    message.success('已请求取消当前任务')
  } catch (err) {
    message.error(errorMessage(err))
  }
}

function sceneAction(chapter: ProductionChapterOut): void {
  emit('open-chapter', chapter.id, 'scenes')
}

function scriptAction(chapter: ProductionChapterOut): void {
  emit('open-chapter', chapter.id, 'script')
}

function stageTagType(percent: number, active: boolean): 'success' | 'warning' | 'default' | 'info' {
  if (percent >= 100) {
    return 'success'
  }
  if (active) {
    return 'info'
  }
  return percent > 0 ? 'warning' : 'default'
}
</script>

<template>
  <div class="space-y-5">
    <NAlert v-if="production.error" type="error" :bordered="false">
      {{ production.error }}
    </NAlert>

    <section
      class="nv-card relative overflow-hidden rounded-2xl px-5 py-5 md:px-6"
    >
      <div class="absolute inset-x-0 top-0 h-px hairline" />
      <div class="flex flex-wrap items-start justify-between gap-5">
        <div class="min-w-0 max-w-2xl">
          <p class="eyebrow">NEXT ACTION / 下一步</p>
          <h2 class="mt-2.5 font-display text-2xl text-fog-100">{{ nextLabel }}</h2>
          <p class="mt-2 text-[12px] leading-6 text-fog-400">{{ nextHint }}</p>
          <div class="mt-4 flex flex-wrap items-center gap-2">
            <NButton
              type="primary"
              :loading="production.busyAction === 'bible'"
              :disabled="overview.next_action === null"
              @click="handleNext"
            >
              {{ nextButtonLabel }}
            </NButton>
            <NButton
              v-if="overview.next_action === 'bible' && !overview.bible_ready"
              secondary
              :loading="production.busyAction === 'bible'"
              @click="production.startBible"
            >
              直接生成设定集
            </NButton>
            <NButton
              v-if="overview.next_action === 'casting' && overview.bible_ready"
              secondary
              :loading="production.busyAction === 'casting'"
              @click="production.startCasting"
            >
              自动选角
            </NButton>
          </div>
        </div>

        <div class="flex shrink-0 items-center gap-5">
          <div>
            <p class="text-[10px] uppercase tracking-[0.18em] text-fog-500">当前状态</p>
            <div class="mt-2 flex items-center gap-2">
              <NTag
                :type="statusTagType(overview.derived_status)"
                :bordered="false"
                size="small"
              >
                {{ PRODUCTION_STATUS_LABELS[overview.derived_status] ?? overview.derived_status }}
              </NTag>
              <span class="font-mono text-[10px] text-fog-500">
                设定集 {{ overview.bible_version ? `v${overview.bible_version}` : '—' }}
              </span>
            </div>
          </div>
          <div class="hidden h-12 w-px bg-ink-700 sm:block" />
          <div class="hidden text-right sm:block">
            <p class="font-mono text-2xl text-fog-100">{{ overview.chapter_count }}</p>
            <p class="text-[10px] uppercase tracking-[0.18em] text-fog-500">章节</p>
          </div>
        </div>
      </div>

      <div
        v-if="overview.job"
        class="mt-5 rounded-xl border border-brass-600/25 bg-brass-500/[0.04] px-4 py-3"
      >
        <div class="flex flex-wrap items-center gap-3">
          <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-brass-400" />
          <span class="text-[11px] text-brass-200">
            {{ overview.job.job_type }} ·
            {{ Math.round((overview.job.progress || 0) * 100) }}%
          </span>
          <span class="text-[10px] text-fog-500">
            {{ overview.job.completed_items }} / {{ overview.job.total_items }} 个子任务
          </span>
          <NButton size="tiny" quaternary class="ml-auto" @click="cancelJob">
            取消任务
          </NButton>
        </div>
        <div class="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
          <div
            class="h-full rounded-full bg-brass-400 transition-all"
            :style="{ width: `${Math.round((overview.job.progress || 0) * 100)}%` }"
          />
        </div>
      </div>
    </section>

    <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <button
        type="button"
        class="nv-card nv-card-hover rounded-xl px-4 py-3.5 text-left"
        @click="emit('navigate', 'bible')"
      >
        <div class="flex items-center justify-between">
          <p class="text-[10px] uppercase tracking-[0.16em] text-fog-500">设定集</p>
          <NTag
            size="tiny"
            :bordered="false"
            :type="overview.bible_ready ? 'success' : 'default'"
          >
            {{ overview.bible_ready ? '已就绪' : '未完成' }}
          </NTag>
        </div>
        <p class="mt-2 font-mono text-lg text-fog-100">{{ formatNumber(overview.character_count) }} 角色</p>
        <p class="mt-0.5 text-[10px] text-fog-500">
          {{ formatNumber(overview.arc_count) }} 段人物弧 · 时间线 {{ overview.chapters_with_scenes ? '已整理' : '待整理' }}
        </p>
      </button>

      <button
        type="button"
        class="nv-card nv-card-hover rounded-xl px-4 py-3.5 text-left"
        @click="emit('navigate', 'scenes')"
      >
        <div class="flex items-center justify-between">
          <p class="text-[10px] uppercase tracking-[0.16em] text-fog-500">分场</p>
          <NTag size="tiny" :bordered="false" :type="stageTagType(scenePercent, !!overview.job)">
            {{ scenePercent }}%
          </NTag>
        </div>
        <p class="mt-2 font-mono text-lg text-fog-100">{{ formatNumber(overview.scene_count) }} 场景</p>
        <p class="mt-0.5 text-[10px] text-fog-500">
          {{ overview.chapters_with_scenes }} / {{ overview.chapter_count }} 章已完成分场
        </p>
      </button>

      <button
        type="button"
        class="nv-card nv-card-hover rounded-xl px-4 py-3.5 text-left"
        @click="emit('navigate', 'script')"
      >
        <div class="flex items-center justify-between">
          <p class="text-[10px] uppercase tracking-[0.16em] text-fog-500">剧本</p>
          <NTag size="tiny" :bordered="false" :type="stageTagType(scriptPercent, !!overview.job)">
            {{ scriptPercent }}%
          </NTag>
        </div>
        <p class="mt-2 font-mono text-lg text-fog-100">{{ formatNumber(overview.tts_unit_count) }} 可配音单元</p>
        <p class="mt-0.5 text-[10px] text-fog-500">
          {{ formatNumber(overview.script_unit_count) }} 个单元 · {{ formatNumber(overview.cue_count) }} 个音效/停顿
        </p>
      </button>

      <button
        type="button"
        class="nv-card nv-card-hover rounded-xl px-4 py-3.5 text-left"
        @click="emit('navigate', 'casting')"
      >
        <div class="flex items-center justify-between">
          <p class="text-[10px] uppercase tracking-[0.16em] text-fog-500">选角</p>
          <NTag size="tiny" :bordered="false" :type="castPercent >= 100 ? 'success' : 'default'">
            {{ castPercent }}%
          </NTag>
        </div>
        <p class="mt-2 font-mono text-lg text-fog-100">{{ formatNumber(overview.cast_count) }} 个配音绑定</p>
        <p class="mt-0.5 text-[10px] text-fog-500">
          旁白 {{ overview.narrator_ready ? '已绑定' : '未绑定' }} · 异常 {{ overview.open_reviews }} 条
        </p>
      </button>
    </div>

    <section class="grid gap-3 lg:grid-cols-3">
      <div class="nv-card rounded-xl px-4 py-3.5 lg:col-span-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <p class="eyebrow">PROGRESS / 进度</p>
            <h3 class="mt-1.5 font-display text-base text-fog-100">章节制作进度</h3>
          </div>
          <div class="flex items-center gap-3 text-[10px] text-fog-500">
            <span>音频 {{ formatNumber(overview.audio_ready_units) }} / {{ formatNumber(overview.tts_unit_count) }}</span>
            <span v-if="overview.audio_stale_units" class="text-brass-300">
              过期 {{ overview.audio_stale_units }}
            </span>
          </div>
        </div>
        <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-ink-700">
          <div
            class="h-full rounded-full bg-gradient-to-r from-brass-500 to-brass-300 transition-all"
            :style="{ width: `${audioPercent}%` }"
          />
        </div>
        <p class="mt-2 text-[10px] text-fog-500">
          音频进度只统计旁白与对白单元；音效、环境与停顿单独作为 cue。
        </p>
      </div>

      <div class="nv-card rounded-xl px-4 py-3.5">
        <p class="eyebrow">REVIEWS / 异常</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="font-mono text-2xl text-fog-100">{{ overview.open_reviews }}</span>
          <span class="text-[10px] text-fog-500">条待处理</span>
          <NButton
            size="tiny"
            quaternary
            class="ml-auto"
            :disabled="!overview.open_reviews"
            @click="emit('navigate', 'reviews')"
          >
            去处理
          </NButton>
        </div>
        <div class="mt-2 flex flex-wrap gap-2 text-[10px]">
          <span class="rounded border border-rust-500/25 bg-rust-500/[0.08] px-1.5 py-0.5 text-rust-400">
            阻断 {{ overview.blocking_reviews }}
          </span>
          <span class="rounded border border-brass-600/25 bg-brass-500/[0.08] px-1.5 py-0.5 text-brass-300">
            警告 {{ overview.warning_reviews }}
          </span>
          <span class="rounded border border-lake-400/25 bg-lake-400/[0.08] px-1.5 py-0.5 text-lake-400">
            提示 {{ overview.info_reviews }}
          </span>
        </div>
      </div>
    </section>

    <section class="nv-card overflow-hidden rounded-xl">
      <header class="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/80 px-4 py-3">
        <div>
          <p class="eyebrow">CHAPTERS / 章节</p>
          <h3 class="mt-1 font-display text-base text-fog-100">逐章推进</h3>
        </div>
        <div class="flex items-center gap-2">
          <NButton size="tiny" secondary @click="emit('navigate', 'scenes')">打开分场</NButton>
          <NButton size="tiny" secondary @click="emit('navigate', 'script')">打开剧本</NButton>
        </div>
      </header>

      <div v-if="!overview.chapters.length" class="px-4 py-10 text-center text-[11px] text-fog-500">
        这本书还没有章节。请先在书库导入 TXT。
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full min-w-[760px] text-left text-[11px]">
          <thead class="border-b border-ink-700/70 text-[9px] uppercase tracking-[0.14em] text-fog-500">
            <tr>
              <th class="w-10 px-4 py-2.5 font-medium">#</th>
              <th class="px-2 py-2.5 font-medium">章节</th>
              <th class="px-2 py-2.5 font-medium">分场</th>
              <th class="px-2 py-2.5 font-medium">剧本</th>
              <th class="px-2 py-2.5 font-medium">单元</th>
              <th class="px-2 py-2.5 font-medium">音频</th>
              <th class="px-2 py-2.5 font-medium">异常</th>
              <th class="px-4 py-2.5 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-ink-700/50">
            <tr
              v-for="chapter in overview.chapters"
              :key="chapter.id"
              class="transition-colors hover:bg-ink-850/60"
            >
              <td class="px-4 py-3 font-mono text-fog-600">{{ chapter.sequence }}</td>
              <td class="max-w-[220px] px-2 py-3">
                <p class="truncate text-fog-200">{{ chapter.title }}</p>
                <p class="mt-0.5 truncate text-[9px] text-fog-600">
                  {{ chapter.char_count }} 字
                  <span v-if="chapter.summary"> · {{ chapter.summary }}</span>
                </p>
              </td>
              <td class="px-2 py-3">
                <NTag size="tiny" :bordered="false" :type="statusTagType(chapter.scene_status)">
                  {{ STAGE_STATUS_LABELS[chapter.scene_status] ?? chapter.scene_status }}
                </NTag>
                <span v-if="chapter.scene_count" class="ml-1.5 font-mono text-[9px] text-fog-500">
                  {{ chapter.scene_count }}
                </span>
              </td>
              <td class="px-2 py-3">
                <NTag size="tiny" :bordered="false" :type="statusTagType(chapter.script_status)">
                  {{ STAGE_STATUS_LABELS[chapter.script_status] ?? chapter.script_status }}
                </NTag>
              </td>
              <td class="px-2 py-3 font-mono text-fog-300">
                {{ chapter.tts_unit_count || 0 }}
                <span v-if="chapter.cue_count" class="text-fog-600">+{{ chapter.cue_count }}</span>
              </td>
              <td class="px-2 py-3">
                <span
                  :class="chapter.audio_ready_units ? 'text-moss-400' : 'text-fog-600'"
                  class="font-mono"
                >
                  {{ chapter.audio_ready_units }} / {{ chapter.tts_unit_count }}
                </span>
                <span v-if="chapter.audio_stale_units" class="ml-1 text-[9px] text-brass-300">
                  stale {{ chapter.audio_stale_units }}
                </span>
              </td>
              <td class="px-2 py-3">
                <button
                  v-if="chapter.review_count"
                  type="button"
                  class="rounded border border-brass-600/25 bg-brass-500/[0.08] px-1.5 py-0.5 font-mono text-[9px] text-brass-300"
                  @click="emit('navigate', 'reviews')"
                >
                  {{ chapter.review_count }}
                </button>
                <span v-else class="text-[9px] text-fog-600">—</span>
              </td>
              <td class="px-4 py-3">
                <div class="flex justify-end gap-1">
                  <NButton size="tiny" quaternary @click="sceneAction(chapter)">分场</NButton>
                  <NButton
                    size="tiny"
                    quaternary
                    :disabled="!chapter.scene_count"
                    @click="scriptAction(chapter)"
                  >
                    剧本
                  </NButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <p class="pb-2 text-[10px] leading-5 text-fog-600">
      制作台只显示当前有效版本。重跑分场或剧本会生成新版本，旧版本仍保留在版本历史中。
    </p>
  </div>
</template>