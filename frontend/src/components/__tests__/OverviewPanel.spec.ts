import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AnalysisJob, BibleStateResponse, BookDetail, ProductionOverviewOut } from '@/api/types'

const mocks = vi.hoisted(() => ({
  startAutoProduction: vi.fn(),
  cancelCurrentJob: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: {
    getBook: vi.fn(),
    getBible: vi.fn(),
  },
}))
vi.mock('@/stores/production', () => ({
  useProductionStore: () => ({
    busyAction: null,
    jobFinishedTick: 0,
    startAutoProduction: mocks.startAutoProduction,
    cancelCurrentJob: mocks.cancelCurrentJob,
  }),
}))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => ({ success: vi.fn(), error: vi.fn() }) }
})

import { api } from '@/api/client'
import OverviewPanel from '@/components/production/OverviewPanel.vue'

const book: BookDetail = {
  id: 'book_1', title: '测试小说', author: '作者', description: '书籍简介', language: 'zh-CN',
  status: 'ready', source_encoding: 'utf-8', source_char_count: 1000, chapter_count: 4,
  created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z', deleted_at: null,
  project_dir: 'books/book_1', source_file_path: null, import_warnings: [],
}

function overview(job: AnalysisJob | null = null): ProductionOverviewOut {
  return {
    book_id: 'book_1', title: '测试小说', production_status: 'ready', derived_status: 'ready',
    bible_status: 'approved', bible_version: 1, bible_ready: true, bible_summary: '全书大纲',
    character_count: 1, arc_count: 1, chapter_count: 3, chapters_with_scenes: 3,
    chapters_with_scripts: 3, scene_count: 3, script_unit_count: 10, tts_unit_count: 10,
    cue_count: 0, audio_ready_units: 10, audio_stale_units: 0, cast_count: 2,
    narrator_ready: true, open_reviews: 0, blocking_reviews: 0, warning_reviews: 0,
    info_reviews: 0, next_action: 'done', job, chapters: [],
  }
}

const bible: BibleStateResponse = {
  book_id: 'book_1', bible_status: 'approved', open_reviews: 0, bible_id: 'bible_1', version: 1,
  job: null,
  bible: {
    id: 'bible_1', book_id: 'book_1', version: 1, status: 'approved', source: 'mock',
    prompt_version: null, model: null, summary: '全书大纲', confidence: 1, is_active: true,
    approved_at: null, stats: {}, warnings: [], relationships: [],
    timeline: { narrative_mode: 'linear', chapters: [], notes: [] }, review_items: [],
    created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
    characters: [{
      id: 'char_1', canonical_name: '林见秋', role_tier: 'protagonist', gender: 'female',
      age_range: 'young_adult', character_type: 'human', identity_description: '旧书店店主',
      personality: [], appearance_desc: null, voice_description: null, aliases: [],
      confidence: 1, is_provisional: false, user_locked: false, evidence: [],
      arcs: [{
        id: 'arc_1', arc_index: 0, label: '青年', age_stage: 'young_adult',
        chapter_start_id: null, chapter_end_id: null, story_time_start: null, story_time_end: null,
        story_order_start: null, story_order_end: null, summary: '回到旧城', traits: [],
        appearance: {}, voice_traits: {}, confidence: 1, evidence: [], source: 'mock',
        created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
      }],
    }],
  },
}

function mountPanel(job: AnalysisJob | null = null) {
  return mount(OverviewPanel, {
    props: { bookId: 'book_1', overview: overview(job) },
  })
}

describe('OverviewPanel task layout', () => {
  it('places compact task progress immediately after book information', async () => {
    const job = { id: 'job_1', status: 'running', job_type: 'auto_production', progress: 0.5 } as AnalysisJob
    const wrapper = mountPanel(job)
    await flushPromises()
    const sections = wrapper.findAll('section')
    const bookIndex = sections.findIndex((section) => section.text().includes('书籍资料'))
    const taskIndex = sections.findIndex((section) => section.attributes('aria-label') === '后台任务进度')

    expect(bookIndex).toBeGreaterThanOrEqual(0)
    expect(taskIndex).toBe(bookIndex + 1)
    expect(wrapper.find('[aria-label^="总进度"]').exists()).toBe(true)
  })
})

beforeEach(() => {

  vi.clearAllMocks()
  vi.mocked(api.getBook).mockResolvedValue(book)
  vi.mocked(api.getBible).mockResolvedValue(bible)
  mocks.startAutoProduction.mockResolvedValue(undefined)
  mocks.cancelCurrentJob.mockResolvedValue(undefined)
})

describe('OverviewPanel', () => {
  it('shows metadata, outline and protagonist history without extra plot sections', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.text()).toContain('测试小说')
    expect(wrapper.text()).toContain('书籍简介')
    expect(wrapper.text()).toContain('全书大纲')
    expect(wrapper.text()).toContain('林见秋')
    expect(wrapper.text()).toContain('回到旧城')
    expect(wrapper.text()).not.toContain('重要剧情走向')
    expect(wrapper.text()).not.toContain('设定集')
  })

  it('starts one-click production from the overview', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '一键自动制作')?.trigger('click')

    expect(mocks.startAutoProduction).toHaveBeenCalledWith(false)
  })

  it('shows only the primary action before production has started', async () => {
    vi.mocked(api.getBible).mockResolvedValue({ ...bible, bible: null, bible_status: 'not_started' })
    const wrapper = mount(OverviewPanel, {
      props: {
        bookId: 'book_1',
        overview: {
          ...overview(), bible_ready: false, bible_status: 'not_started', bible_version: null,
          character_count: 0, cast_count: 0, script_unit_count: 0, chapters_with_scripts: 0,
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('从导入到有声书，一次完成')
    expect(wrapper.text()).toContain('一键自动制作')
    expect(wrapper.text()).not.toContain('已导入内容的整体故事')
    expect(wrapper.text()).not.toContain('制作进度')
  })

  it('does not reload static sections for progress-only overview updates', async () => {
    const job: AnalysisJob = {
      id: 'job_1', book_id: 'book_1', job_type: 'auto_production', status: 'running',
      total_items: 4, completed_items: 0, failed_items: 0, progress: 0,
      params: {}, result: { stage: 'bible', stages: { bible: { status: 'running', progress: 0 } } },
      error_code: null, error_message: null, started_at: null, finished_at: null,
      created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
    }
    const wrapper = mountPanel(job)
    await flushPromises()
    vi.mocked(api.getBook).mockClear()
    vi.mocked(api.getBible).mockClear()

    await wrapper.setProps({
      overview: overview({ ...job, progress: 0.25, completed_items: 1 }),
    })
    await flushPromises()

    expect(api.getBook).not.toHaveBeenCalled()
    expect(api.getBible).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('lists every automatic stage with waiting and processing states', async () => {
    const job: AnalysisJob = {
      id: 'job_1', book_id: 'book_1', job_type: 'auto_production', status: 'running',
      total_items: 4, completed_items: 1, failed_items: 0, progress: 0.25,
      params: {}, result: {
        stage: 'casting',
        stages: {
          bible: { status: 'completed', total_items: 2, completed_items: 2 },
          casting: { status: 'running', total_items: 11, completed_items: 3, progress: 3 / 11 },
          script: { status: 'pending' },
          audio: { status: 'pending' },
        },
      },
      error_code: null, error_message: null, started_at: null, finished_at: null,
      created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
    }
    const wrapper = mountPanel(job)
    await flushPromises()

    expect(wrapper.text()).toContain('全书分析')
    expect(wrapper.text()).toContain('角色整理与自动选角')
    expect(wrapper.text()).toContain('逐章生成剧本')
    expect(wrapper.text()).toContain('自动配音')
    expect(wrapper.text()).toContain('27%')
    expect(wrapper.text()).toContain('等待')
    expect(wrapper.find('[aria-label="后台任务进度"]').exists()).toBe(true)
  })

  it('keeps a failed automatic task visible and marks later legacy stages as blocked', async () => {
    const job: AnalysisJob = {
      id: 'job_legacy', book_id: 'book_1', job_type: 'auto_production', status: 'failed',
      total_items: 4, completed_items: 1, failed_items: 1, progress: 0.25,
      params: {}, result: {
        stage: 'audio',
        stages: {
          bible: { status: 'completed' },
          casting: { status: 'failed' },
          script: { status: 'blocked' },
          audio: { status: 'blocked' },
        },
      }, error_code: 'auto_production_failed',
      error_message: '自动选角失败', started_at: null, finished_at: null,
      created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
    }
    const wrapper = mountPanel(job)
    await flushPromises()

    expect(wrapper.text()).toContain('角色整理与自动选角')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).toContain('已阻塞')
    expect(wrapper.text()).toContain('重试未完成项')
  })

  it('shows system failures separately and retries from current progress', async () => {
    const job: AnalysisJob = {
      id: 'job_1', book_id: 'book_1', job_type: 'auto_production', status: 'failed',
      total_items: 8, completed_items: 7, failed_items: 1, progress: 1,
      params: {}, result: { stage: 'audio', system_failures: [{ stage: 'audio', unit_id: 'unit_123', message: '语音引擎连接失败' }] },
      error_code: null, error_message: null, started_at: null, finished_at: null,
      created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
    }
    const wrapper = mountPanel(job)
    await flushPromises()

    expect(wrapper.text()).toContain('查看失败详情（1 项）')
    expect(wrapper.text()).toContain('语音引擎连接失败')
    await wrapper.findAll('button').find((button) => button.text() === '重试未完成项')?.trigger('click')
    expect(mocks.startAutoProduction).toHaveBeenCalledWith(false)
  })

  it('offers to continue a cancelled automatic task without discarding completed work', async () => {
    const job: AnalysisJob = {
      id: 'job_cancelled', book_id: 'book_1', job_type: 'auto_production', status: 'cancelled',
      total_items: 4, completed_items: 1, failed_items: 0, progress: 0.25,
      params: {},
      result: { stage: 'casting', stages: { bible: { status: 'completed' }, casting: { status: 'cancelled' } } },
      error_code: null, error_message: null, started_at: null, finished_at: null,
      created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
    }
    const wrapper = mountPanel(job)
    await flushPromises()

    const continueButton = wrapper.findAll('button').find((button) => button.text() === '继续未完成项')
    expect(continueButton).toBeDefined()
    expect(wrapper.text()).toContain('已取消')
    await continueButton?.trigger('click')
    expect(mocks.startAutoProduction).toHaveBeenCalledWith(false)
  })

  it('shows parent stage progress and offers cancellation', async () => {
    const job: AnalysisJob = {
      id: 'job_1', book_id: 'book_1', job_type: 'auto_production', status: 'running',
      total_items: 8, completed_items: 3, failed_items: 0, progress: 0.375,
      params: {}, result: { stage: 'audio' }, error_code: null, error_message: null,
      started_at: null, finished_at: null, created_at: '2026-11-27T00:00:00Z',
      updated_at: '2026-11-27T00:00:00Z',
    }
    const wrapper = mountPanel(job)
    await flushPromises()

    expect(wrapper.text()).toContain('一键自动制作')
    expect(wrapper.text()).toContain('自动配音')
    expect(wrapper.text()).toContain('38%')
    await wrapper.findAll('button').find((button) => button.text() === '中止')?.trigger('click')
    expect(mocks.cancelCurrentJob).toHaveBeenCalledOnce()
  })
})
