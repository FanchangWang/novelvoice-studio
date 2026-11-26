<script setup lang="ts">
import { computed, onMounted } from 'vue'
import {
  NAlert,
  NButton,
  NRadioButton,
  NRadioGroup,
  NSpin,
  useMessage,
} from 'naive-ui'

import type { ComponentStatus, PublicSettings } from '@/api/types'
import StatusDot from '@/components/StatusDot.vue'
import { useSystemStore } from '@/stores/system'
import { isThemeMode, useThemeStore } from '@/stores/theme'
import { formatDateTime, formatSettingValue } from '@/utils/format'

const system = useSystemStore()
const theme = useThemeStore()
const message = useMessage()

type SectionKey = keyof Omit<PublicSettings, 'read_only'>

interface SectionDefinition {
  key: SectionKey
  title: string
  caption: string
  fields: string[]
}

const sectionDefinitions: SectionDefinition[] = [
  {
    key: 'app',
    title: '应用',
    caption: 'app',
    fields: [
      'host',
      'port',
      'language',
      'log_level',
      'auto_migrate',
      'max_upload_mb',
      'frontend_dir',
    ],
  },
  {
    key: 'llm',
    title: 'LLM',
    caption: 'llm',
    fields: [
      'provider',
      'base_url',
      'model',
      'enable_thinking',
      'context_length',
      'keep_alive',
      'timeout_seconds',
    ],
  },
  {
    key: 'voice_design',
    title: '音色设计引擎服务',
    caption: 'voice_design',
    fields: [
      'provider',
      'model_id',
      'worker_url',
      'timeout_seconds',
      'candidates',
      'seeds',
    ],
  },
  {
    key: 'tts',
    title: 'TTS 引擎服务',
    caption: 'tts',
    fields: [
      'provider',
      'model_id',
      'worker_url',
      'timeout_seconds',
      'default_seed',
      'max_text_chars',
      'default_speed',
    ],
  },
  {
    key: 'jobs',
    title: '任务调度',
    caption: 'jobs',
    fields: ['max_gpu_concurrency', 'max_retries', 'auto_resume'],
  },
  {
    key: 'export',
    title: '导出',
    caption: 'export',
    fields: ['audio_codec', 'audio_bitrate', 'chapter_gap_ms'],
  },
  {
    key: 'providers',
    title: 'Provider 模式',
    caption: 'providers',
    fields: ['mode', 'is_mock'],
  },
]

const FIELD_LABELS: Record<string, string> = {
  host: '监听地址',
  port: '端口',
  language: '语言',
  log_level: '日志级别',
  auto_migrate: '启动自动迁移',
  max_upload_mb: '上传上限 MB',
  frontend_dir: '前端目录',
  provider: 'Provider',
  base_url: '服务地址',
  model: '模型',
  enable_thinking: '启用思考',
  context_length: '上下文长度',
  keep_alive: '模型保活',
  timeout_seconds: '超时（秒）',
  model_id: '模型 ID',
  worker_url: '引擎服务地址',
  candidates: '默认候选数',
  seeds: '默认种子',
  default_seed: '生成种子',
  max_text_chars: '单段最大字数',
  default_speed: '默认语速',
  max_gpu_concurrency: 'GPU 并发',
  max_retries: '最大重试',
  auto_resume: '自动续跑',
  audio_codec: '音频编码',
  audio_bitrate: '音频码率',
  chapter_gap_ms: '章节间隔 ms',
  mode: '模式',
  is_mock: '模拟模式',
}

const configSections = computed(() => {
  const settings = system.settings
  if (!settings) {
    return []
  }
  return sectionDefinitions.map((definition) => ({
    ...definition,
    entries: definition.fields
      .filter((field) => field in settings[definition.key])
      .map((field) => ({
        field,
        label: FIELD_LABELS[field] ?? field,
        value: formatSettingValue(settings[definition.key][field]),
      })),
  }))
})

const components = computed(() => system.status?.components ?? [])

const coreComponentsOk = computed(() => {
  if (!system.status) {
    return null
  }
  return system.status.components
    .filter((component) => component.critical)
    .every((component) => component.ok)
})

const mockBannerTitle = computed(() =>
  system.mockMode ? '模拟模式运行中' : '真实 Provider 模式',
)

const resolvedThemeLabel = computed(() =>
  theme.resolvedTheme === 'dark' ? '深色' : '浅色',
)

function updateThemeMode(value: string): void {
  if (isThemeMode(value)) {
    theme.setMode(value)
  }
}

function componentTone(component: ComponentStatus): 'ok' | 'warn' | 'error' | 'idle' {
  if (component.ok) {
    return 'ok'
  }
  return component.critical ? 'error' : 'warn'
}

function componentState(component: ComponentStatus): string {
  if (component.ok) {
    return '正常'
  }
  return component.critical ? '异常' : '未就绪'
}

async function copyDataDir(): Promise<void> {
  const path = system.status?.data_dir
  if (!path) {
    return
  }
  try {
    await navigator.clipboard.writeText(path)
    message.success('数据目录已复制')
  } catch {
    message.error('复制失败，请手动选择路径')
  }
}

function refresh(): void {
  void system.refresh(true)
}

onMounted(() => {
  void system.refresh()
})
</script>

<template>
  <div class="min-h-0 flex-1 overflow-y-auto">
    <div class="mx-auto w-full max-w-[1180px] px-8 py-8">
      <header class="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p class="eyebrow">SETTINGS / 设置</p>
          <h1 class="mt-3 font-display text-3xl text-fog-100">系统状态与配置</h1>
          <p class="mt-2 max-w-2xl text-sm leading-6 text-fog-400">
            系统配置为只读视图；外观主题可在此调整。检查本机依赖、数据库与两个语音引擎服务的连通性，并展示实际生效的配置。
          </p>
        </div>
        <div class="flex items-center gap-2">
          <NButton quaternary :loading="system.loading" @click="refresh">重新检查</NButton>
          <a
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            class="nv-focus rounded-lg border border-ink-600 px-3.5 py-2 text-xs text-fog-300 transition-colors hover:border-brass-600/60 hover:text-brass-300"
          >
            API 文档
          </a>
        </div>
      </header>

      <NAlert v-if="system.error" type="error" :bordered="false" class="mt-6">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span class="text-xs leading-5">{{ system.error }}</span>
          <NButton size="tiny" quaternary @click="refresh">重试</NButton>
        </div>
      </NAlert>

      <NAlert
        v-if="system.mockMode !== null"
        :type="system.mockMode ? 'warning' : 'success'"
        :bordered="false"
        class="mt-6"
      >
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p class="text-xs font-semibold">{{ mockBannerTitle }}</p>
            <p class="mt-1 max-w-3xl text-[11px] leading-6 opacity-90">
              {{
                system.mockMode
                  ? 'LLM、音色设计与 TTS 均返回确定性模拟结果，不加载真实模型、不访问外部 AI 服务。'
                  : '真实模式使用本机 Ollama 与 Qwen3-TTS / CosyVoice3 引擎服务；服务未启动时相关任务会明确报错，不会伪装成功。'
              }}
            </p>
          </div>
          <span
            class="shrink-0 rounded-md border px-2 py-1 font-mono text-[10px] tracking-wide"
            :class="
              system.mockMode
                ? 'border-brass-600/40 bg-brass-500/10 text-brass-300'
                : 'border-moss-500/40 bg-moss-500/10 text-moss-400'
            "
          >
            {{ system.providersMode.toUpperCase() }}
          </span>
        </div>
      </NAlert>

      <section class="nv-card mt-5 rounded-xl p-5">
        <div class="flex flex-wrap items-start justify-between gap-5">
          <div class="min-w-0">
            <p class="eyebrow">APPEARANCE / 外观</p>
            <h2 class="mt-1.5 font-display text-xl text-fog-100">主题模式</h2>
            <p class="mt-2 max-w-2xl text-sm leading-6 text-fog-400">
              默认跟随系统；手动选择会保存在本机浏览器。当前实际主题：
              <span class="font-medium text-fog-100">{{ resolvedThemeLabel }}</span>
            </p>
          </div>
          <NRadioGroup
            name="theme-mode"
            :value="theme.mode"
            @update:value="updateThemeMode"
          >
            <NRadioButton value="system">跟随系统</NRadioButton>
            <NRadioButton value="light">浅色</NRadioButton>
            <NRadioButton value="dark">深色</NRadioButton>
          </NRadioGroup>
        </div>
      </section>

      <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div class="nv-card rounded-xl p-4">
          <p class="text-[9px] uppercase tracking-[0.18em] text-fog-500">版本</p>
          <p class="mt-2 font-mono text-sm text-fog-100">v{{ system.version }}</p>
        </div>
        <div class="nv-card rounded-xl p-4">
          <p class="text-[9px] uppercase tracking-[0.18em] text-fog-500">核心组件</p>
          <p class="mt-2 flex items-center gap-2 text-sm text-fog-100">
            <StatusDot
              :status="
                coreComponentsOk === null ? 'idle' : coreComponentsOk ? 'ok' : 'error'
              "
              size="sm"
            />
            {{
              coreComponentsOk === null
                ? '检测中'
                : coreComponentsOk
                  ? '全部正常'
                  : '存在异常'
            }}
          </p>
        </div>
        <div class="nv-card rounded-xl p-4">
          <p class="text-[9px] uppercase tracking-[0.18em] text-fog-500">Provider</p>
          <p class="mt-2 font-mono text-sm text-fog-100">{{ system.providersMode }}</p>
        </div>
        <div class="nv-card rounded-xl p-4">
          <p class="text-[9px] uppercase tracking-[0.18em] text-fog-500">上次检查</p>
          <p class="mt-2 text-[11px] text-fog-200">
            {{ formatDateTime(system.status?.checked_at) }}
          </p>
        </div>
      </div>

      <div class="nv-card mt-4 rounded-xl p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="min-w-0">
            <p class="text-[9px] uppercase tracking-[0.18em] text-fog-500">数据目录</p>
            <p class="mt-1.5 break-all font-mono text-[11px] text-fog-200">
              {{ system.status?.data_dir || '—' }}
            </p>
          </div>
          <NButton size="tiny" :disabled="!system.status?.data_dir" @click="copyDataDir">
            复制路径
          </NButton>
        </div>
        <p class="mt-2 text-[10px] leading-5 text-fog-500">
          包含 app.db、books/、logs/、backups/、trash/。音频文件只保存路径与元数据，不写入 SQLite。
        </p>
      </div>

      <section class="mt-8">
        <div class="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p class="eyebrow">COMPONENTS</p>
            <h2 class="mt-2 font-display text-xl text-fog-100">服务状态</h2>
          </div>
          <p class="text-[10px] text-fog-500">
            核心组件异常会影响使用；语音引擎服务未启动时只影响音色设计与配音生成。
          </p>
        </div>

        <div v-if="system.loading && components.length === 0" class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <div
            v-for="index in 6"
            :key="index"
            class="h-32 animate-pulse rounded-xl border border-ink-700/70 bg-ink-850/50"
          />
        </div>

        <div v-else class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <article
            v-for="component in components"
            :key="component.id"
            class="nv-card rounded-xl p-4"
          >
            <div class="flex items-start gap-3">
              <StatusDot
                :status="componentTone(component)"
                :pulse="componentTone(component) === 'error'"
                size="sm"
              />
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <h3 class="text-xs font-medium text-fog-100">{{ component.label }}</h3>
                  <span
                    class="rounded border px-1 py-0.5 text-[8px] tracking-wider"
                    :class="
                      component.critical
                        ? 'border-ink-600 text-fog-400'
                        : 'border-ink-700 text-fog-500'
                    "
                  >
                    {{ component.critical ? '核心' : '可选' }}
                  </span>
                </div>
                <p class="mt-1.5 text-[10px] leading-5 text-fog-400">{{ component.detail }}</p>

                <details v-if="Object.keys(component.meta).length" class="group mt-2">
                  <summary
                    class="inline-flex cursor-pointer list-none items-center gap-1 text-sm text-fog-500 transition-colors hover:text-fog-300"
                  >
                    <span class="text-[10px] transition-transform group-open:rotate-90">▶</span>
                    查看元数据
                  </summary>
                  <pre class="mt-2 max-h-36 overflow-auto rounded-md bg-ink-950/70 p-2 font-mono text-[9px] leading-4 text-fog-500">{{ JSON.stringify(component.meta, null, 2) }}</pre>
                </details>
              </div>
              <span
                class="shrink-0 rounded px-1.5 py-0.5 text-[9px]"
                :class="
                  component.ok
                    ? 'bg-moss-500/10 text-moss-400'
                    : component.critical
                      ? 'bg-rust-500/10 text-rust-400'
                      : 'bg-brass-500/10 text-brass-400'
                "
              >
                {{ componentState(component) }}
              </span>
            </div>
          </article>
        </div>

        <div
          v-if="!system.loading && components.length === 0"
          class="mt-4 flex items-center justify-center gap-2 rounded-xl border border-dashed border-ink-700 py-8 text-xs text-fog-500"
        >
          <NSpin v-if="system.loading" size="small" />
          暂无组件状态，请重新检查。
        </div>
      </section>

      <section class="mt-9">
        <div class="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p class="eyebrow">CONFIGURATION</p>
            <h2 class="mt-2 font-display text-xl text-fog-100">只读配置</h2>
          </div>
          <p class="text-[10px] text-fog-500">
            修改 config/config.yaml 或 NOVELVOICE_* 环境变量后重启生效
          </p>
        </div>

        <div v-if="configSections.length" class="mt-4 grid gap-3 lg:grid-cols-2">
          <section
            v-for="section in configSections"
            :key="section.key"
            class="nv-card rounded-xl p-5"
          >
            <div class="flex items-baseline justify-between gap-3">
              <h3 class="text-xs font-medium text-fog-100">{{ section.title }}</h3>
              <span class="font-mono text-[9px] tracking-wide text-fog-600">{{ section.caption }}</span>
            </div>
            <dl class="mt-3 divide-y divide-ink-700/60">
              <div
                v-for="entry in section.entries"
                :key="entry.field"
                class="flex gap-4 py-2.5 text-[11px]"
              >
                <dt class="w-28 shrink-0 text-fog-500">{{ entry.label }}</dt>
                <dd class="min-w-0 break-all font-mono text-fog-200">{{ entry.value }}</dd>
              </div>
            </dl>
          </section>
        </div>

        <div
          v-else
          class="mt-4 flex items-center justify-center gap-2 rounded-xl border border-dashed border-ink-700 py-8 text-xs text-fog-500"
        >
          <NSpin size="small" />
          正在读取配置…
        </div>
      </section>

      <footer class="mt-8 border-t border-ink-700/70 py-5 text-[10px] leading-5 text-fog-600">
        设置页当前只读；所有服务仅监听 127.0.0.1。数据目录中的 backups/ 会在数据库升级前自动备份。
        日志见 logs/app.log。
      </footer>
    </div>
  </div>
</template>