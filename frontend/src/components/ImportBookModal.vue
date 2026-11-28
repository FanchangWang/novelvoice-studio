<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import {
  NAlert,
  NButton,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpin,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type { BookSummary, DuplicatePolicy, ImportPreview } from '@/api/types'
import { useSystemStore } from '@/stores/system'
import { ENCODING_OPTIONS } from '@/utils/encodings'
import { errorMessage } from '@/utils/errors'
import { formatBytes, formatDateTime, formatNumber, truncate } from '@/utils/format'

const props = withDefaults(defineProps<{ initialBookId?: string | null }>(), {
  initialBookId: null,
})
const show = defineModel<boolean>('show', { default: false })
const emit = defineEmits<{ imported: [bookId: string] }>()

const message = useMessage()
const system = useSystemStore()

const fileInput = ref<HTMLInputElement | null>(null)
const file = shallowRef<File | null>(null)
const preview = ref<ImportPreview | null>(null)
const busy = ref(false)
const importing = ref(false)
const error = ref<string | null>(null)
const dragActive = ref(false)

const encoding = ref<string | null>(null)
const customRegex = ref('')
const appliedRegex = ref('')
const showAdvanced = ref(false)

const title = ref('')
const titleTouched = ref(false)
const author = ref('')
const authorTouched = ref(false)
const description = ref('')
const descriptionTouched = ref(false)
const duplicatePolicy = ref<DuplicatePolicy>('new')
const replaceBookId = ref<string | null>(null)
const books = ref<BookSummary[]>([])

let previewToken = 0

const maxUploadMb = computed(() => {
  const raw = system.settings?.app?.max_upload_mb
  const value = Number(raw)
  return Number.isFinite(value) && value > 0 ? value : 64
})

const encodingOptions = computed(() => {
  const options = new Map<string, string>()
  for (const candidate of preview.value?.encoding_candidates ?? []) {
    options.set(
      candidate.encoding,
      `${candidate.label} · ${Math.round(candidate.confidence * 100)}%`,
    )
  }
  for (const option of ENCODING_OPTIONS) {
    if (!options.has(option.value)) {
      options.set(option.value, option.label)
    }
  }
  return Array.from(options, ([value, label]) => ({ value, label }))
})

const currentEncoding = computed(() =>
  preview.value?.encoding_candidates.find((item) => item.encoding === encoding.value),
)

const confidencePercent = computed(() => {
  const value = currentEncoding.value?.confidence ?? preview.value?.encoding_confidence ?? 0
  return Math.max(0, Math.min(100, Math.round(value * 100)))
})

const confidenceClass = computed(() => {
  if (confidencePercent.value >= 90) {
    return 'bg-moss-400'
  }
  if (confidencePercent.value >= 75) {
    return 'bg-brass-400'
  }
  return 'bg-rust-400'
})

const regexDirty = computed(
  () => customRegex.value.trim() !== appliedRegex.value,
)

const duplicate = computed(() => preview.value?.duplicate ?? null)
const metadata = computed(() => preview.value?.metadata ?? null)
const targetBook = computed(() =>
  books.value.find((book) => book.id === replaceBookId.value) ?? null,
)
const targetRequired = computed(() =>
  ['merge', 'replace'].includes(duplicatePolicy.value),
)
const isMerging = computed(() => duplicatePolicy.value === 'merge')
const targetBookOptions = computed(() =>
  books.value.map((book) => ({
    value: book.id,
    label: `${book.title} · ${book.chapter_count} 章`,
  })),
)

const canImport = computed(() => {
  if (!preview.value || busy.value || importing.value || regexDirty.value) {
    return false
  }
  if (duplicate.value && duplicatePolicy.value === 'reject') {
    return false
  }
  if (targetRequired.value && !replaceBookId.value) {
    return false
  }
  return true
})

const footerHint = computed(() => {
  if (!preview.value) {
    return `支持 UTF-8 / GBK / Big5 / UTF-16（BOM），单文件上限 ${maxUploadMb.value} MB`
  }
  if (regexDirty.value) {
    return '自定义正则已修改，请先重新解析再导入'
  }
  if (duplicate.value && duplicatePolicy.value === 'reject') {
    return '检测到重复内容，请选择新的处理方式'
  }
  if (targetRequired.value && !replaceBookId.value) {
    return '请选择要更新或覆盖的目标书籍'
  }
  if (isMerging.value) {
    return '将保留未变化章节的分析、选角与音频；变化章节提示重新生成'
  }
  return `将创建 ${formatNumber(preview.value.chapter_count)} 个章节 · ${formatNumber(
    preview.value.total_chars,
  )} 字`
})

const isReplacing = computed(
  () => Boolean(duplicate.value) && duplicatePolicy.value === 'replace',
)

function pickFile(): void {
  if (busy.value || importing.value) {
    return
  }
  if (fileInput.value) {
    fileInput.value.value = ''
    fileInput.value.click()
  }
}

function onFileChange(event: Event): void {
  const input = event.target as HTMLInputElement
  acceptFile(input.files?.[0])
}

function onDrop(event: DragEvent): void {
  dragActive.value = false
  if (busy.value || importing.value) {
    return
  }
  acceptFile(event.dataTransfer?.files?.[0])
}

function acceptFile(candidate: File | null | undefined): void {
  if (!candidate) {
    return
  }
  if (!candidate.name.toLowerCase().endsWith('.txt')) {
    error.value = '仅支持 .txt 纯文本文件。'
    return
  }
  if (candidate.size === 0) {
    error.value = '文件为空。'
    return
  }
  if (candidate.size > maxUploadMb.value * 1024 * 1024) {
    error.value = `文件超过大小上限（${maxUploadMb.value} MB）。`
    return
  }

  error.value = null
  file.value = candidate
  resetPreviewState()
  void loadPreview(true)
}

function resetPreviewState(): void {
  previewToken += 1
  preview.value = null
  encoding.value = null
  customRegex.value = ''
  appliedRegex.value = ''
  showAdvanced.value = false
  title.value = ''
  titleTouched.value = false
  author.value = ''
  authorTouched.value = false
  description.value = ''
  descriptionTouched.value = false
  duplicatePolicy.value = 'new'
  replaceBookId.value = null
  error.value = null
  busy.value = false
}

async function loadPreview(initial = false): Promise<void> {
  if (!file.value) {
    return
  }
  const token = ++previewToken
  busy.value = true
  error.value = null

  try {
    const result = await api.previewImport(file.value, {
      encoding: encoding.value ?? undefined,
      customRegex: customRegex.value.trim() || undefined,
    })
    if (token !== previewToken) {
      return
    }

    const previousDuplicateId = preview.value?.duplicate?.id ?? null
    preview.value = result
    appliedRegex.value = customRegex.value.trim()
    encoding.value = result.encoding

    const nextDuplicateId = result.duplicate?.id ?? null
    if (initial || previousDuplicateId !== nextDuplicateId) {
      if (result.duplicate) {
        duplicatePolicy.value = 'merge'
        replaceBookId.value = props.initialBookId ?? result.duplicate.id
      } else {
        duplicatePolicy.value = props.initialBookId ? 'merge' : 'new'
        replaceBookId.value = props.initialBookId
      }
    }
    applyMetadataDefaults(initial)
  } catch (err) {
    if (token !== previewToken) {
      return
    }
    error.value = errorMessage(err)
    if (initial) {
      preview.value = null
    } else if (preview.value) {
      encoding.value = preview.value.encoding
    }
  } finally {
    if (token === previewToken) {
      busy.value = false
    }
  }
}

function filenameStem(filename: string): string {
  return filename.replace(/\.txt$/i, '').trim().slice(0, 255)
}

function applyMetadataDefaults(force = false): void {
  if (!preview.value) {
    return
  }
  const isUpdate = ['merge', 'replace'].includes(duplicatePolicy.value)
  const target = isUpdate ? targetBook.value : null
  const extracted = metadata.value

  if (force || !titleTouched.value) {
    title.value = target?.title || extracted?.title || filenameStem(preview.value.filename)
    titleTouched.value = false
  }
  if (force || !authorTouched.value) {
    author.value = target?.author || extracted?.author || ''
    authorTouched.value = false
  }
  if (force || !descriptionTouched.value) {
    description.value = target?.description || extracted?.description || ''
    descriptionTouched.value = false
  }
}

function metadataSourceLabel(source: string | null | undefined): string {
  if (source === 'content_label') return '来自正文标签'
  if (source === 'content_heading') return '来自正文开头'
  if (source === 'ai_content') return '本地 AI 从正文识别'
  if (source === 'filename') return '来自文件名'
  if (source === 'target_book') return '沿用目标书籍'
  return '未识别'
}

function handleTitleInput(): void {
  titleTouched.value = true
}

function handleAuthorInput(): void {
  authorTouched.value = true
}

function handleDescriptionInput(): void {
  descriptionTouched.value = true
}

function handleEncodingChange(value: string | null): void {
  if (!value || value === encoding.value || busy.value || importing.value) {
    return
  }
  encoding.value = value
  void loadPreview(false)
}

function applyRegex(): void {
  if (busy.value || importing.value) {
    return
  }
  void loadPreview(false)
}

function clearRegex(): void {
  if (busy.value || importing.value) {
    return
  }
  customRegex.value = ''
  void loadPreview(false)
}

function onDuplicatePolicyChange(value: string | number): void {
  duplicatePolicy.value = value as DuplicatePolicy
  if (duplicate.value) {
    replaceBookId.value = duplicate.value.id
  }
  applyMetadataDefaults()
}

watch(replaceBookId, () => {
  if (preview.value) {
    applyMetadataDefaults()
  }
})

async function doImport(): Promise<void> {
  if (!file.value || !preview.value || !canImport.value) {
    return
  }
  importing.value = true
  error.value = null

  try {
    const result = await api.importBook(file.value, {
      encoding: encoding.value ?? undefined,
      title: title.value.trim() || undefined,
      author: author.value.trim() || undefined,
      description: description.value.trim() || undefined,
      customRegex: appliedRegex.value || undefined,
      duplicatePolicy: duplicatePolicy.value,
      replaceBookId:
        duplicatePolicy.value === 'replace' || duplicatePolicy.value === 'merge'
          ? replaceBookId.value ?? duplicate.value?.id
          : undefined,
    })
    if (result.mode === 'merge') {
      message.success(
        `已更新《${result.book.title}》：新增 ${result.chapters_created} 章，` +
          `变化 ${result.chapters_changed} 章，未变化 ${result.chapters_unchanged} 章`,
      )
    } else {
      message.success(
        result.replaced
          ? `已覆盖导入《${result.book.title}》`
          : `已导入《${result.book.title}》，共 ${result.chapters_created} 章`,
      )
    }
    emit('imported', result.book.id)
    show.value = false
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    importing.value = false
  }
}

function resetAll(): void {
  previewToken += 1
  file.value = null
  preview.value = null
  busy.value = false
  importing.value = false
  encoding.value = null
  customRegex.value = ''
  appliedRegex.value = ''
  showAdvanced.value = false
  title.value = ''
  titleTouched.value = false
  author.value = ''
  authorTouched.value = false
  description.value = ''
  descriptionTouched.value = false
  duplicatePolicy.value = 'new'
  replaceBookId.value = null
  books.value = []
  error.value = null
  dragActive.value = false
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

watch(show, async (open) => {
  if (!open) {
    resetAll()
    return
  }
  replaceBookId.value = props.initialBookId
  duplicatePolicy.value = props.initialBookId ? 'merge' : 'new'
  try {
    const result = await api.listBooks()
    books.value = result.items.filter((book) => book.status !== 'deleted')
  } catch (err) {
    error.value = errorMessage(err)
  }
})
</script>

<template>
  <NModal
    v-model:show="show"
    preset="card"
    :bordered="false"
    :mask-closable="!busy && !importing"
    :closable="!importing"
    :close-on-esc="!importing"
    :style="{ width: '900px', maxWidth: 'calc(100vw - 32px)' }"
  >
    <template #header>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span class="font-display text-lg text-fog-100">导入 TXT</span>
        <span class="h-4 w-px bg-ink-600" />
        <div class="flex items-center gap-3 text-[11px]">
          <span :class="preview ? 'text-fog-500' : 'text-brass-300'">01 选择文件</span>
          <span class="h-px w-5" :class="preview ? 'bg-brass-500/60' : 'bg-ink-600'" />
          <span :class="preview ? 'text-brass-300' : 'text-fog-500'">02 预览确认</span>
        </div>
      </div>
    </template>

    <div class="max-h-[68vh] overflow-y-auto pr-1">
      <NAlert v-if="error" type="error" :bordered="false" class="mb-4">
        <div class="flex items-start justify-between gap-4">
          <span class="text-xs leading-5">{{ error }}</span>
          <NButton
            v-if="file && !busy && !importing"
            size="tiny"
            quaternary
            @click="loadPreview(!preview)"
          >
            重试解析
          </NButton>
        </div>
      </NAlert>

      <div v-if="!preview" class="space-y-4">
        <div v-if="file" class="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-850/80 px-4 py-3">
          <div class="grid h-9 w-9 place-items-center rounded-lg border border-brass-600/40 bg-brass-500/10 font-mono text-[10px] text-brass-300">
            TXT
          </div>
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm text-fog-100">{{ file.name }}</p>
            <p class="mt-0.5 text-[10px] text-fog-500">{{ formatBytes(file.size) }}</p>
          </div>
          <NButton size="small" :disabled="busy || importing" @click="pickFile">
            更换文件
          </NButton>
        </div>

        <div
          v-if="busy"
          class="flex min-h-[260px] flex-col items-center justify-center gap-4 rounded-xl border border-ink-700 bg-ink-850/50"
        >
          <NSpin size="medium" />
          <p class="text-xs text-fog-400">正在识别编码、章节与书目信息，本地 AI 将参与结构校验…</p>
        </div>

        <button
          v-else-if="!file"
          type="button"
          class="nv-focus group flex min-h-[300px] w-full flex-col items-center justify-center gap-4 rounded-xl border border-dashed px-8 py-12 text-center transition-colors"
          :class="
            dragActive
              ? 'border-brass-500/80 bg-brass-500/10'
              : 'border-ink-600 bg-ink-850/35 hover:border-brass-600/60 hover:bg-ink-850/70'
          "
          @click="pickFile"
          @dragover.prevent="dragActive = true"
          @dragleave.prevent="dragActive = false"
          @drop.prevent="onDrop"
        >
          <div class="grid h-14 w-14 place-items-center rounded-xl border border-ink-600 bg-ink-800 text-brass-400">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 16V4" />
              <path d="m7.5 8.5 4.5-4.5 4.5 4.5" />
              <path d="M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15" />
            </svg>
          </div>
          <div>
            <p class="text-sm font-medium text-fog-100">拖入小说 TXT，或点击选择</p>
            <p class="mt-2 text-xs leading-6 text-fog-500">
              自动检测 UTF-8 / GBK / Big5 / UTF-16 BOM；不会上传到任何服务器。
            </p>
          </div>
          <span class="rounded-lg border border-brass-600/40 bg-brass-500/10 px-4 py-2 text-xs font-medium text-brass-300">
            选择 TXT 文件
          </span>
        </button>

        <div
          v-else
          class="flex min-h-[220px] flex-col items-center justify-center gap-3 rounded-xl border border-ink-700 bg-ink-850/40 px-8 text-center"
        >
          <p class="text-sm text-fog-200">预览尚未就绪</p>
          <p class="max-w-md text-xs leading-6 text-fog-500">
            可以重试解析，或更换文件。低置信度编码需要在预览中手动确认。
          </p>
          <div class="mt-2 flex gap-2">
            <NButton size="small" @click="pickFile">更换文件</NButton>
            <NButton size="small" type="primary" @click="loadPreview(true)">重试解析</NButton>
          </div>
        </div>
      </div>

      <div v-else class="space-y-4">
        <div class="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-850/70 px-4 py-3">
          <div class="grid h-9 w-9 place-items-center rounded-lg border border-brass-600/40 bg-brass-500/10 font-mono text-[10px] text-brass-300">
            TXT
          </div>
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm text-fog-100">{{ preview.filename }}</p>
            <p class="mt-0.5 text-[10px] text-fog-500">
              {{ formatBytes(preview.size_bytes) }} · SHA256 {{ preview.sha256.slice(0, 12) }}…
            </p>
          </div>
          <div class="flex items-center gap-2">
            <span class="rounded-md border border-ink-600 px-2 py-1 text-[10px] text-fog-400">
              {{ preview.line_count }} 行
            </span>
            <NButton size="small" :disabled="busy || importing" @click="pickFile">
              更换文件
            </NButton>
          </div>
        </div>

        <NAlert
          v-if="preview.needs_encoding_confirmation && !busy"
          type="warning"
          :bordered="false"
        >
          <p class="text-xs leading-5">
            自动识别置信度较低（{{ Math.round(preview.encoding_confidence * 100) }}%）。请在左侧确认编码，
            必要时切换候选后再导入。
          </p>
        </NAlert>

        <NAlert v-if="preview.warnings.length" type="warning" :bordered="false">
          <ul class="list-disc space-y-1 pl-4 text-xs leading-5">
            <li v-for="warning in preview.warnings" :key="warning">{{ warning }}</li>
          </ul>
        </NAlert>

        <div
          v-if="duplicate"
          class="rounded-xl border border-brass-600/40 bg-brass-500/[0.07] p-4"
        >
          <div class="flex items-start gap-3">
            <span class="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-brass-400" />
            <div class="min-w-0 flex-1">
              <p class="text-xs font-medium text-brass-200">检测到相同内容的书籍</p>
              <p class="mt-1 text-xs leading-5 text-fog-300">
                书库中已有《{{ duplicate.title }}》（{{ formatDateTime(duplicate.created_at) }} 导入）。
                连载更新建议选择“智能合并”，也可明确选择新书或整本覆盖。
              </p>
            </div>
          </div>
          <NRadioGroup
            :value="duplicatePolicy"
            class="mt-3 flex flex-col gap-2 pl-5"
            @update:value="onDuplicatePolicyChange"
          >
            <NRadio value="reject">
              <span class="text-xs text-fog-200">中止导入</span>
              <span class="ml-2 text-[10px] text-fog-500">保留现有书籍，不做修改</span>
            </NRadio>
            <NRadio value="new">
              <span class="text-xs text-fog-200">作为新书导入</span>
              <span class="ml-2 text-[10px] text-fog-500">保留两份相同内容</span>
            </NRadio>
            <NRadio value="merge">
              <span class="text-xs text-fog-200">智能合并更新（推荐）</span>
              <span class="ml-2 text-[10px] text-fog-500">
                支持完整新版或新增章节片段；保留未变化章节的现有成果
              </span>
            </NRadio>
            <NRadio value="replace">
              <span class="text-xs text-fog-200">覆盖已有书籍</span>
              <span class="ml-2 text-[10px] text-fog-500">
                旧目录移入 trash；有音频或未完成任务时会被拒绝
              </span>
            </NRadio>
          </NRadioGroup>
        </div>

        <section
          v-if="targetRequired"
          class="rounded-xl border border-ink-700 bg-ink-850/55 p-4"
        >
          <label class="block">
            <span class="mb-1.5 block text-[11px] text-fog-300">目标书籍</span>
            <NSelect
              v-model:value="replaceBookId"
              :options="targetBookOptions"
              filterable
              placeholder="选择要更新的书籍"
              :disabled="busy || importing"
            />
          </label>
          <p class="mt-2 text-[10px] leading-5 text-fog-500">
            智能合并不会删除原书；整本覆盖会移入旧目录，有音频或运行中任务时将拒绝。
          </p>
        </section>

        <div class="grid gap-4 lg:grid-cols-[1.02fr_0.98fr]">
          <div class="space-y-4">
            <section class="rounded-xl border border-ink-700 bg-ink-850/55 p-4">
              <div class="flex items-center justify-between gap-3">
                <p class="eyebrow">书目信息</p>
                <span
                  v-if="metadata"
                  class="font-mono text-[9px] text-fog-500"
                >
                  自动识别 {{ Math.round(metadata.confidence * 100) }}%
                </span>
              </div>
              <div class="mt-3 space-y-3">
                <label class="block">
                  <span class="mb-1.5 block text-[11px] text-fog-300">书名</span>
                  <NInput
                    v-model:value="title"
                    :maxlength="255"
                    placeholder="自动提取书名，可修改"
                    @update:value="handleTitleInput"
                  />
                  <span class="mt-1 block text-[10px] text-fog-500">
                    {{ ['merge', 'replace'].includes(duplicatePolicy) && targetBook
                      ? '沿用目标书籍，避免更新导入覆盖已有书名'
                      : metadataSourceLabel(metadata?.title_source) }}
                  </span>
                </label>
                <label class="block">
                  <span class="mb-1.5 block text-[11px] text-fog-300">作者</span>
                  <NInput
                    v-model:value="author"
                    :maxlength="255"
                    placeholder="自动提取，选填"
                    @update:value="handleAuthorInput"
                  />
                  <span class="mt-1 block text-[10px] text-fog-500">
                    {{ ['merge', 'replace'].includes(duplicatePolicy) && targetBook?.author
                      ? '沿用目标书籍'
                      : metadataSourceLabel(metadata?.author_source) }}
                  </span>
                </label>
                <label class="block">
                  <span class="mb-1.5 block text-[11px] text-fog-300">简介</span>
                  <NInput
                    v-model:value="description"
                    type="textarea"
                    :maxlength="5000"
                    :autosize="{ minRows: 2, maxRows: 4 }"
                    placeholder="自动提取，选填"
                    @update:value="handleDescriptionInput"
                  />
                  <span class="mt-1 block text-[10px] text-fog-500">
                    {{ ['merge', 'replace'].includes(duplicatePolicy) && targetBook?.description
                      ? '沿用目标书籍'
                      : metadataSourceLabel(metadata?.description_source) }}
                  </span>
                </label>
              </div>
            </section>

            <section class="rounded-xl border border-ink-700 bg-ink-850/55 p-4">
              <div class="flex items-center justify-between gap-3">
                <p class="eyebrow">编码</p>
                <span class="font-mono text-[10px] text-fog-500">CONFIDENCE {{ confidencePercent }}%</span>
              </div>
              <div class="mt-3 flex items-center gap-2">
                <NSelect
                  :value="encoding"
                  :options="encodingOptions"
                  :consistent-menu-width="false"
                  :disabled="busy || importing"
                  class="min-w-0 flex-1"
                  @update:value="handleEncodingChange"
                />
                <span
                  v-if="busy"
                  class="shrink-0 text-[10px] text-fog-500"
                >
                  重新解析…
                </span>
              </div>
              <div class="mt-3 h-1 overflow-hidden rounded-full bg-ink-700">
                <div
                  class="h-full rounded-full transition-[width] duration-300"
                  :class="confidenceClass"
                  :style="{ width: `${confidencePercent}%` }"
                />
              </div>
              <p class="mt-2 text-[10px] leading-5 text-fog-500">
                {{ currentEncoding?.reason ?? '当前编码由用户选择' }}
              </p>
              <p v-if="preview.needs_encoding_confirmation" class="mt-1 text-[10px] leading-5 text-brass-400">
                低置信度：建议对照右侧正文样例确认无乱码。
              </p>
            </section>

            <section class="rounded-xl border border-ink-700 bg-ink-850/55 p-4">
              <button
                type="button"
                class="nv-focus flex w-full items-center justify-between gap-3 text-left"
                @click="showAdvanced = !showAdvanced"
              >
                <span class="eyebrow">章节识别（高级）</span>
                <span class="text-[10px] text-fog-500">{{ showAdvanced ? '收起' : '展开' }}</span>
              </button>

              <div v-if="showAdvanced" class="mt-3 space-y-2">
                <NInput
                  v-model:value="customRegex"
                  placeholder="自定义正则，例如：^第[零一二三四五六七八九十百千]+章.*$"
                  :disabled="busy || importing"
                />
                <p class="text-[10px] leading-5 text-fog-500">
                  留空使用内置规则；若正则含捕获组，第 1 组作为章节标题。修改后需要重新解析。
                </p>
                <div class="flex items-center gap-2">
                  <NButton size="tiny" type="primary" :loading="busy" :disabled="importing" @click="applyRegex">
                    重新解析
                  </NButton>
                  <NButton size="tiny" quaternary :disabled="busy || importing || !customRegex" @click="clearRegex">
                    恢复内置规则
                  </NButton>
                  <span v-if="regexDirty" class="ml-auto text-[10px] text-brass-400">尚未应用</span>
                </div>
              </div>
            </section>
          </div>

          <div class="space-y-4">
            <section class="rounded-xl border border-ink-700 bg-ink-850/55 p-4">
              <div class="flex items-end justify-between gap-3">
                <div>
                  <p class="eyebrow">章节结构</p>
                  <p class="mt-1 text-[10px] text-fog-500">
                    {{ preview.structure_source === 'custom'
                      ? '自定义正则识别'
                      : preview.ai_assisted
                        ? '规则候选 + 本地 AI 辅助识别'
                        : '规则识别' }}
                  </p>
                </div>
                <span class="text-[11px] text-fog-400">
                  <span class="font-mono text-base text-brass-300">{{ formatNumber(preview.chapter_count) }}</span>
                  章
                </span>
              </div>
              <ol class="mt-3 max-h-44 space-y-1 overflow-y-auto pr-1">
                <li
                  v-for="(chapterTitle, index) in preview.chapter_titles"
                  :key="`${index}-${chapterTitle}`"
                  class="flex gap-2 rounded-md px-2 py-1 text-[11px] leading-5 text-fog-300 odd:bg-ink-800/45"
                >
                  <span class="w-6 shrink-0 text-right font-mono text-[10px] text-fog-600">
                    {{ String(index + 1).padStart(2, '0') }}
                  </span>
                  <span class="truncate">{{ truncate(chapterTitle, 46) }}</span>
                </li>
              </ol>
              <p v-if="preview.chapters_truncated" class="mt-2 text-[10px] text-fog-500">
                仅显示前 {{ preview.chapter_titles.length }} 个章节标题。
              </p>
            </section>

            <section class="rounded-xl border border-ink-700 bg-ink-850/55 p-4">
              <div class="flex items-center justify-between">
                <p class="eyebrow">正文样例</p>
                <span class="text-[10px] text-fog-500">前 800 字</span>
              </div>
              <pre class="mt-3 max-h-56 overflow-y-auto whitespace-pre-wrap break-words font-sans text-[11px] leading-6 text-fog-300">{{ preview.sample_text }}</pre>
            </section>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="min-w-0 flex-1 text-[10px] leading-5 text-fog-500">{{ footerHint }}</p>
        <div class="flex items-center gap-2">
          <NButton :disabled="importing" @click="show = false">取消</NButton>
          <NButton
            v-if="!preview"
            type="primary"
            :disabled="busy || importing"
            @click="pickFile"
          >
            选择 TXT
          </NButton>
          <NButton
            v-else
            type="primary"
            :loading="importing"
            :disabled="!canImport"
            @click="doImport"
          >
            {{ isReplacing ? '覆盖并打开' : isMerging ? '合并更新并打开' : '导入并打开' }}
          </NButton>
        </div>
      </div>
    </template>
  </NModal>

  <input
    ref="fileInput"
    type="file"
    accept=".txt,text/plain"
    class="hidden"
    @change="onFileChange"
  />
</template>