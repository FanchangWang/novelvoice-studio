<script setup lang="ts">
import { computed } from 'vue'
import {
  NAlert,
  NButton,
  NProgress,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import { useAnalysisStore } from '@/stores/analysis'
import { useSystemStore } from '@/stores/system'
import { useWorkspaceStore } from '@/stores/workspace'
import { JOB_STATUS_LABELS } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { formatNumber } from '@/utils/format'

const props = defineProps<{ bookId: string }>()

const analysis = useAnalysisStore()
const system = useSystemStore()
const workspace = useWorkspaceStore()
const dialog = useDialog()
const message = useMessage()

const job = computed(() => analysis.currentJob)

const jobActive = computed(() => analysis.jobActive)

const jobStatusLabel = computed(() => {
  const status = job.value?.status
  return status ? (JOB_STATUS_LABELS[status] ?? status) : '未运行'
})

const progressPercent = computed(() =>
  Math.round((job.value?.progress ?? 0) * 100),
)

const pendingChapterIds = computed(() =>
  (analysis.status?.chapters ?? [])
    .filter(
      (item) => item.analysis_status !== 'completed' && item.analysis_status !== 'needs_review',
    )
    .map((item) => item.id),
)

const ollamaComponent = computed(() =>
  system.status?.components.find((item) => item.id === 'ollama'),
)

const ollamaReady = computed(() => {
  const component = ollamaComponent.value
  if (!component || !component.ok) {
    return false
  }
  return component.meta?.model_available !== false
})

const realMode = computed(() => system.mockMode === false)

const analyzeDisabled = computed(
  () =>
    jobActive.value ||
    !analysis.status ||
    analysis.status.total_chapters === 0 ||
    workspace.loadingBook,
)

const modeHeadline = computed(() => {
  if (system.mockMode === null) {
    return '正在检测 Provider'
  }
  if (system.mockMode) {
    return '模拟模式'
  }
  return '真实模式'
})

const modeDetail = computed(() => {
  if (system.mockMode === null) {
    return '读取后端状态…'
  }
  if (system.mockMode) {
    return 'Mock LLM 返回确定性结果，不会调用任何模型，也不会联网。'
  }
  return 'LLM 使用本机 Ollama；音色设计与合成使用本机语音引擎服务（需单独启动）。'
})

function confirmStart(mode: 'all' | 'pending'): void {
  if (analyzeDisabled.value) {
    return
  }
  const chapterIds = mode === 'pending' ? pendingChapterIds.value : []
  const count = mode === 'pending' ? chapterIds.length : analysis.status?.total_chapters ?? 0
  if (mode === 'pending' && !count) {
    message.info('所有章节都已分析')
    return
  }
  dialog.warning({
    title: mode === 'all' ? '分析全书' : '分析未完成章节',
    content:
      `将依次分析 ${count} 个章节。低置信度的说话人、情绪与别名会进入审核列表；` +
      '已人工编辑或锁定的内容不会被覆盖。可以随时取消任务。',
    positiveText: '开始分析',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await analysis.startBookAnalysis(chapterIds)
        message.success('分析任务已开始')
      } catch (err) {
        message.error(errorMessage(err))
      }
    },
  })
}

function confirmCancel(): void {
  dialog.warning({
    title: '取消分析',
    content: '将在当前章节处理完成后停止，已完成的结果会保留。',
    positiveText: '取消任务',
    negativeText: '继续运行',
    onPositiveClick: async () => {
      try {
        await analysis.cancelBookAnalysis()
        message.info('分析任务已取消')
      } catch (err) {
        message.error(errorMessage(err))
      }
    },
  })
}

async function refresh(): Promise<void> {
  try {
    await analysis.loadStatus(props.bookId)
  } catch (err) {
    message.error(errorMessage(err))
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-2">
      <p class="eyebrow">AI 分析</p>
      <button
        type="button"
        class="nv-focus rounded px-1 text-[9px] text-fog-500 transition-colors hover:text-fog-200"
        :disabled="analysis.loadingStatus"
        @click="refresh"
      >
        {{ analysis.loadingStatus ? '刷新中…' : '刷新状态' }}
      </button>
    </div>

    <div
      class="rounded-lg border p-3"
      :class="
        system.mockMode === false
          ? 'border-moss-500/25 bg-moss-400/[0.05]'
          : 'border-brass-600/25 bg-brass-500/[0.06]'
      "
    >
      <div class="flex items-center gap-2">
        <span
          class="h-1.5 w-1.5 rounded-full"
          :class="system.mockMode === false ? 'bg-moss-400' : 'bg-brass-400'"
        />
        <p class="text-[11px] font-medium text-fog-100">{{ modeHeadline }}</p>
        <NTag size="small" :bordered="false" :type="system.mockMode === false ? 'success' : 'warning'">
          {{ system.mockMode === false ? 'REAL' : system.mockMode ? 'MOCK' : '…' }}
        </NTag>
      </div>
      <p class="mt-1.5 text-[10px] leading-5 text-fog-400">{{ modeDetail }}</p>
      <p
        v-if="realMode && !ollamaReady"
        class="mt-1.5 rounded border border-rust-500/30 bg-rust-500/[0.06] px-2 py-1 text-[9px] leading-4 text-rust-400"
      >
        Ollama 未连接或缺少模型 {{ (system.settings?.llm?.model as string) || '' }}。
        请在设置页确认模型已在本机安装。
      </p>
    </div>

    <div v-if="analysis.status" class="rounded-lg border border-ink-700/80 bg-ink-850/40 p-3">
      <div class="flex items-center justify-between gap-2">
        <p class="text-[10px] text-fog-400">全书进度</p>
        <span class="font-mono text-[10px]" :class="jobActive ? 'text-brass-300' : 'text-fog-500'">
          {{ jobStatusLabel }}
        </span>
      </div>
      <NProgress
        type="line"
        :percentage="progressPercent"
        :height="4"
        :show-indicator="false"
        :status="job?.status === 'failed' ? 'error' : jobActive ? 'default' : 'success'"
        color="#c99a45"
        class="mt-2"
      />
      <p class="mt-1.5 text-[9px] text-fog-500">
        <template v-if="job">
          {{ job.completed_items }}/{{ job.total_items }} 章 · {{ jobStatusLabel }}
          <span v-if="job.failed_items"> · 失败 {{ job.failed_items }}</span>
        </template>
        <template v-else>尚未运行全书分析</template>
      </p>

      <div class="mt-3 flex gap-2">
        <NButton
          v-if="jobActive"
          size="small"
          type="error"
          secondary
          class="flex-1"
          @click="confirmCancel"
        >
          取消任务
        </NButton>
        <template v-else>
          <NButton
            size="small"
            type="primary"
            class="flex-1"
            :disabled="analyzeDisabled"
            @click="confirmStart('all')"
          >
            分析全书
          </NButton>
          <NButton
            size="small"
            class="flex-1"
            :disabled="analyzeDisabled || !pendingChapterIds.length"
            @click="confirmStart('pending')"
          >
            未完成章节
          </NButton>
        </template>
      </div>
      <NButton
        v-if="jobActive"
        size="tiny"
        quaternary
        class="mt-2 w-full"
        :disabled="analysis.loadingStatus"
        @click="refresh"
      >
        刷新进度
      </NButton>
      <div v-if="analysis.loadingStatus && !analysis.status" class="mt-2 flex justify-center">
        <NSpin size="small" />
      </div>
    </div>

    <div v-else class="flex items-center gap-2 rounded-lg border border-ink-700 bg-ink-850/40 p-3 text-[10px] text-fog-500">
      <NSpin size="small" />
      正在读取分析状态…
    </div>

    <div v-if="analysis.status" class="grid grid-cols-2 gap-2">
      <div class="rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2">
        <p class="font-mono text-base text-fog-100">
          {{ analysis.status.completed_chapters }}<span class="text-[10px] text-fog-500">/{{ analysis.status.total_chapters }}</span>
        </p>
        <p class="mt-0.5 text-[9px] text-fog-500">已完成章节</p>
      </div>
      <div class="rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2">
        <p class="font-mono text-base" :class="analysis.status.review_chapters ? 'text-brass-300' : 'text-fog-100'">
          {{ analysis.status.review_chapters }}
        </p>
        <p class="mt-0.5 text-[9px] text-fog-500">待审章节</p>
      </div>
      <div class="rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2">
        <p class="font-mono text-base text-fog-100">{{ formatNumber(analysis.status.total_segments) }}</p>
        <p class="mt-0.5 text-[9px] text-fog-500">片段</p>
      </div>
      <div class="rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2">
        <p class="font-mono text-base" :class="analysis.status.review_segments ? 'text-brass-300' : 'text-fog-100'">
          {{ formatNumber(analysis.status.review_segments) }}
        </p>
        <p class="mt-0.5 text-[9px] text-fog-500">待审片段</p>
      </div>
      <div class="col-span-2 rounded-lg border border-ink-700/80 bg-ink-850/50 px-3 py-2">
        <div class="flex items-baseline justify-between gap-3">
          <span class="text-[9px] text-fog-500">角色</span>
          <span class="font-mono text-[11px] text-fog-200">
            {{ formatNumber(analysis.status.total_characters) }}
            <span v-if="analysis.pendingCharacters" class="text-[9px] text-brass-300">
              · {{ analysis.pendingCharacters }} 待确认
            </span>
          </span>
        </div>
      </div>
    </div>

    <NAlert v-if="analysis.status?.failed_chapters" type="error" :bordered="false">
      <span class="text-[10px]">
        {{ analysis.status.failed_chapters }} 个章节分析失败。打开章节可查看错误并重跑。
      </span>
    </NAlert>

    <NAlert v-if="analysis.status?.job?.error_message && analysis.status.job.status === 'failed'" type="error" :bordered="false">
      <span class="text-[10px] leading-5">
        最近任务失败：{{ analysis.status.job.error_message }}
      </span>
    </NAlert>

    <p class="text-[9px] leading-5 text-fog-600">
      低置信度说话人、情绪与待合并别名会自动进入审核。人工编辑的字段会锁定，重跑不覆盖。
      原文只发送到本机 Ollama。
    </p>
  </div>
</template>