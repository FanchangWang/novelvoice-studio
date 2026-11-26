/** v0.5.0 制作台使用的状态与标签。 */

export type ProductionTab = 'plan' | 'bible' | 'scenes' | 'script' | 'casting' | 'reviews'

export const PRODUCTION_STATUS_LABELS: Record<string, string> = {
  not_started: '未开始',
  analyzing: '设定集分析中',
  scripting: '分场 / 剧本生成中',
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

export const REVIEW_STATUS_LABELS: Record<string, string> = {
  open: '待处理',
  resolved: '已接受',
  ignored: '已忽略',
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