/** v0.5.0 制作台使用的状态与标签。 */

export type ProductionTab = 'overview' | 'chapters' | 'script' | 'casting' | 'reviews'

export const PRODUCTION_STATUS_LABELS: Record<string, string> = {
  not_started: '未开始',
  analyzing: '全书分析中',
  scripting: '剧本生成中',
  casting: '选角中',
  needs_review: '待处理异常',
  ready: '可以生成音频',
  generating: '音频生成中',
  failed: '失败',
}

export const BIBLE_STATUS_LABELS: Record<string, string> = {
  not_started: '未生成',
  running: '生成中',
  needs_review: '待确认',
  approved: '已确认',
  failed: '失败',
}

export const STAGE_STATUS_LABELS: Record<string, string> = {
  not_started: '未开始',
  running: '生成中',
  completed: '已完成',
  needs_review: '待审核',
  failed: '失败',
}

export const UNIT_TYPE_LABELS: Record<string, string> = {
  narration: '旁白',
  dialogue: '对白',
  inner_monologue: '内心独白',
  sfx: '音效',
  ambience: '环境音',
  pause: '停顿',
}

export const AUDIO_STATUS_LABELS: Record<string, string> = {
  not_applicable: '无需配音',
  none: '未生成',
  stale: '待重新生成',
  queued: '排队中',
  generating: '生成中',
  ready: '已生成',
  failed: '失败',
}

export function audioStatusTagType(status: string): TagType {
  if (status === 'ready') {
    return 'success'
  }
  if (status === 'failed') {
    return 'error'
  }
  if (status === 'stale' || status === 'queued' || status === 'generating') {
    return 'warning'
  }
  return 'default'
}

export const CHAPTER_KIND_LABELS: Record<string, string> = {
  story: '正文',
  extra: '番外',
  author_note: '请假单 / 作者说明',
  unclassified: '未分类',
}

export const CHAPTER_KIND_OPTIONS = Object.entries(CHAPTER_KIND_LABELS).map(
  ([value, label]) => ({ value, label }),
)

export const ROLE_TIER_LABELS: Record<string, string> = {
  protagonist: '主角',
  major: '重要配角',
  minor: '次要角色',
  extra: '群演',
  unknown: '层级未知',
}

export const TIME_MODE_LABELS: Record<string, string> = {
  present: '现在',
  flashback: '倒叙',
  flashforward: '预叙',
  dream: '梦境',
  unknown: '时间未知',
}

export const PRESENCE_LABELS: Record<string, string> = {
  speaker: '发言',
  present: '在场',
  mentioned: '被提及',
}

export const NARRATIVE_MODE_LABELS: Record<string, string> = {
  linear: '顺叙',
  nonlinear: '非线性',
  unknown: '未知',
}

export const ADAPTATION_LEVEL_LABELS: Record<string, string> = {
  light: '轻度改编',
  standard: '标准改编',
  strong: '较大改编',
  legacy: '旧版数据',
}

/** 剧本版本自身的状态，和章节阶段状态不是同一套枚举。 */
export const SCRIPT_VERSION_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  running: '生成中',
  needs_review: '待审核',
  approved: '已审核',
  superseded: '已被新版本替换',
  failed: '失败',
}

export const JOB_TYPE_LABELS: Record<string, string> = {
  auto_production: '一键自动制作',
  production_batch: '批量生成剧本',
  chapter_analysis: '章节分析',
  book_bible: '全书分析',
  chapter_scenes: '本章分析',
  chapter_script: '本章剧本',
  book_casting: '自动选角',
  segment_audio: '单元音频',
  voice_design: '音色设计',
}

/** 制作链任务的运行状态；与阶段 2 的“分析中”区分。 */
export const PRODUCTION_JOB_STATUS_LABELS: Record<string, string> = {
  pending: '等待启动',
  queued: '排队中',
  running: '生成中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  book: '书籍',
  chapter: '章节',
  segment: '片段',
  character: '角色',
  scene: '场景',
  script_unit: '剧本单元',
  voice_actor: '配音员',
  voice_profile: '音色候选',
}

export const ACTOR_STATUS_LABELS: Record<string, string> = {
  ready: '可用',
  archived: '已归档',
  failed: '生成失败',
  generating: '生成中',
}

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  open: '待处理',
  resolved: '已接受',
  ignored: '已忽略',
}

export function chapterDisplayTitle(sequence: number, title: string): string {
  const cleaned = (title || '').trim()
  if (!cleaned) return String(sequence).padStart(2, '0')
  if (/^(?:第\s*[零一二三四五六七八九十百千万\d]+\s*[卷章节回部篇]|[零一二三四五六七八九十百千万\d]+\s*[、.．、:：-])/.test(cleaned)) {
    return cleaned
  }
  return `${String(sequence).padStart(2, '0')} · ${cleaned}`
}

export function jobTypeLabel(value: string | null | undefined): string {
  return value ? (JOB_TYPE_LABELS[value] ?? value) : '任务'
}

export function jobStatusLabel(value: string | null | undefined): string {
  return value ? (PRODUCTION_JOB_STATUS_LABELS[value] ?? value) : '未知状态'
}

export const REVIEW_SEVERITY_LABELS: Record<string, string> = {
  blocking: '阻断',
  warning: '警告',
  info: '提示',
}

export const NEXT_ACTION_LABELS: Record<string, string> = {
  bible: '生成全书设定集',
  scenes: '补全章节分场',
  script: '生成有声剧本',
  casting: '自动选择配音员',
  reviews: '处理待办异常',
  audio: '生成选中单元音频',
  done: '制作流程已完成',
}

export const REVIEW_STATUS_OPTIONS = [
  { label: '待处理', value: 'open' },
  { label: '已接受', value: 'resolved' },
  { label: '已忽略', value: 'ignored' },
  { label: '全部状态', value: '' },
]

export const REVIEW_SEVERITY_OPTIONS = [
  { label: '全部级别', value: '' },
  { label: '阻断', value: 'blocking' },
  { label: '警告', value: 'warning' },
  { label: '提示', value: 'info' },
]

export type TagType = 'default' | 'success' | 'warning' | 'error' | 'info'

export function statusTagType(status: string): TagType {
  if (status === 'ready' || status === 'completed' || status === 'approved') {
    return 'success'
  }
  if (status === 'needs_review' || status === 'warning') {
    return 'warning'
  }
  if (status === 'failed' || status === 'blocking') {
    return 'error'
  }
  if (status === 'running' || status === 'analyzing' || status === 'scripting' || status === 'casting' || status === 'generating') {
    return 'info'
  }
  return 'default'
}

export function severityTagType(severity: string): TagType {
  if (severity === 'blocking') {
    return 'error'
  }
  if (severity === 'warning') {
    return 'warning'
  }
  return 'info'
}

export function yesNo(value: boolean): string {
  return value ? '是' : '否'
}