const numberFormatter = new Intl.NumberFormat('zh-CN')

const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '0'
  }
  return numberFormatter.format(value)
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) {
    return '—'
  }
  if (value < 1024) {
    return `${value} B`
  }
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '—'
  }
  return dateTimeFormatter.format(date)
}

export function truncate(text: string, max = 80): string {
  const cleaned = text.replace(/\s+/g, ' ').trim()
  if (cleaned.length <= max) {
    return cleaned
  }
  return `${cleaned.slice(0, max)}…`
}

/** 判断所选章节是否在序列上连续（合并约束）。 */
export function selectionIsContiguous(
  chapters: ReadonlyArray<{ id: string; sequence: number }>,
  selectedIds: readonly string[],
): boolean {
  if (selectedIds.length < 2) {
    return false
  }
  const selectedSet = new Set(selectedIds)
  const sequences = chapters
    .filter((chapter) => selectedSet.has(chapter.id))
    .map((chapter) => chapter.sequence)
    .sort((a, b) => a - b)

  if (sequences.length !== selectedIds.length) {
    return false
  }
  return sequences.every(
    (value, index) => index === 0 || value === (sequences[index - 1] as number) + 1,
  )
}

export function formatSettingValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—'
  }
  if (typeof value === 'boolean') {
    return value ? '开启' : '关闭'
  }
  if (Array.isArray(value)) {
    return value.length ? value.map((item) => String(item)).join('、') : '—'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}