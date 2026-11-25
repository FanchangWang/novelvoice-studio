/** 阶段 2 审核界面使用的枚举与中文标签。 */

export const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  not_started: '未分析',
  queued: '排队中',
  running: '分析中',
  completed: '已完成',
  needs_review: '待审核',
  failed: '失败',
}

export const JOB_STATUS_LABELS: Record<string, string> = {
  pending: '等待启动',
  queued: '排队中',
  running: '分析中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  auto: '自动通过',
  needs_review: '待审核',
  approved: '已审核',
}

export const SEGMENT_TYPE_LABELS: Record<string, string> = {
  narration: '旁白',
  dialogue: '对白',
  inner_monologue: '内心独白',
  unknown: '未分类',
}

export const SPEAKER_TYPE_LABELS: Record<string, string> = {
  character: '角色',
  narrator: '旁白',
  unknown: '未知',
}

export const EMOTION_LABELS: Record<string, string> = {
  neutral: '平静',
  happy: '愉快',
  excited: '兴奋',
  angry: '愤怒',
  sad: '悲伤',
  fearful: '恐惧',
  surprised: '惊讶',
  doubtful: '疑惑',
  sarcastic: '讽刺',
  tender: '温柔',
  embarrassed: '窘迫',
  determined: '坚定',
  tired: '疲惫',
}

export const DELIVERY_LABELS: Record<string, string> = {
  normal: '正常',
  whispering: '低语',
  shouting: '喊叫',
  crying: '哭泣',
  laughing: '带笑',
  breathless: '气喘',
  slow: '放慢',
  hesitant: '迟疑',
}

export const GENDER_LABELS: Record<string, string> = {
  male: '男',
  female: '女',
  other: '其他',
  unknown: '未知',
}

export const AGE_LABELS: Record<string, string> = {
  child: '儿童',
  teenager: '少年',
  young_adult: '青年',
  adult: '成年',
  middle_aged: '中年',
  elderly: '老年',
  unknown: '年龄未知',
}

export const CHARACTER_TYPE_LABELS: Record<string, string> = {
  human: '人类',
  animal: '动物',
  non_human: '非人类',
  unknown: '类型未知',
}

export const BASIS_LABELS: Record<string, string> = {
  explicit: '原文明确',
  inferred: '推断',
  unknown: '无依据',
}

export const INTENSITY_LABELS: Record<number, string> = {
  1: '轻',
  2: '中',
  3: '强',
}

export const LOCKABLE_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'speech_text', label: '朗读文本' },
  { key: 'segment_type', label: '片段类型' },
  { key: 'speaker_type', label: '说话人类型' },
  { key: 'speaker_id', label: '说话角色' },
  { key: 'emotion', label: '情绪' },
  { key: 'intensity', label: '强度' },
  { key: 'delivery', label: '表演方式' },
  { key: 'tts_instruction', label: '表演提示' },
  { key: 'speed', label: '语速' },
  { key: 'pause_before_ms', label: '段前停顿' },
  { key: 'pause_after_ms', label: '段后停顿' },
]

export function labelOf(map: Record<string, string>, value: string | null | undefined): string {
  if (!value) {
    return '—'
  }
  return map[value] ?? value
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '—'
  }
  return `${Math.round(value * 100)}%`
}

export function isChapterAnalyzed(status: string): boolean {
  return status === 'completed' || status === 'needs_review'
}

export function isJobActive(status: string | null | undefined): boolean {
  return status === 'pending' || status === 'queued' || status === 'running'
}