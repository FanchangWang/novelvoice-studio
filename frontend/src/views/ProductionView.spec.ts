import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AnalysisJob, ProductionChapterOut, ProductionOverviewOut } from '@/api/types'

const mocks = vi.hoisted(() => ({
  route: { params: { bookId: 'book_1' }, query: { tab: 'script' } },
  push: vi.fn(),
  replace: vi.fn(),
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return {
    ...actual,
    useRoute: () => mocks.route,
    useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
  }
})
vi.mock('@/api/client', () => ({
  api: { getProductionOverview: vi.fn() },
}))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => ({ success: vi.fn(), error: vi.fn() }) }
})

import { api } from '@/api/client'
import CharactersPanel from '@/components/production/CharactersPanel.vue'
import ScriptPanel from '@/components/production/ScriptPanel.vue'
import { useProductionStore } from '@/stores/production'
import ProductionView from '@/views/ProductionView.vue'

function chapter(
  id: string,
  kind: ProductionChapterOut['chapter_kind'],
  analysisIncluded: boolean,
): ProductionChapterOut {
  return {
    id,
    title: `${id} 标题`,
    sequence: 1,
    volume_number: 1,
    volume_title: '第一卷',
    chapter_kind: kind,
    analysis_included: analysisIncluded,
    char_count: 100,
    scene_status: 'ready',
    scene_version: 1,
    scene_count: 1,
    script_status: 'ready',
    script_version: 1,
    unit_count: 2,
    tts_unit_count: 2,
    cue_count: 0,
    audio_ready_units: 0,
    audio_stale_units: 0,
    review_count: 0,
    summary: null,
  }
}

function overview(chapters: ProductionChapterOut[]): ProductionOverviewOut {
  return {
    book_id: 'book_1',
    title: '测试小说',
    production_status: 'ready',
    derived_status: 'ready',
    bible_status: 'approved',
    bible_version: 1,
    bible_ready: true,
    bible_summary: null,
    character_count: 0,
    arc_count: 0,
    chapter_count: chapters.length,
    chapters_with_scenes: 0,
    chapters_with_scripts: chapters.length,
    scene_count: 0,
    script_unit_count: 5,
    tts_unit_count: 5,
    cue_count: 0,
    audio_ready_units: 0,
    audio_stale_units: 0,
    cast_count: 0,
    narrator_ready: false,
    open_reviews: 0,
    blocking_reviews: 0,
    warning_reviews: 0,
    info_reviews: 0,
    next_action: null,
    job: null,
    chapters,
  }
}

function autoOverview(stages: Record<string, unknown>): ProductionOverviewOut {
  const job: AnalysisJob = {
    id: 'job_auto', book_id: 'book_1', job_type: 'auto_production', status: 'running',
    total_items: 4, completed_items: 1, failed_items: 0, progress: 0.25,
    params: {}, result: { stages }, error_code: null, error_message: null,
    started_at: null, finished_at: null,
    created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z',
  }
  return { ...overview([]), job, script_unit_count: 0, tts_unit_count: 0 }
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  vi.clearAllMocks()
  mocks.route.query.tab = 'script'
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ProductionView script chapter defaults', () => {
  it('defaults to the first included chapter instead of an excluded first chapter', async () => {
    const chapters = [
      chapter('extra', 'extra', false),
      chapter('story_1', 'story', true),
      chapter('story_2', 'story', true),
    ]
    vi.mocked(api.getProductionOverview).mockResolvedValue(overview(chapters))
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    const panel = wrapper.findComponent(ScriptPanel)
    expect(panel.props('chapterId')).toBe('story_1')
    expect(panel.props('chapters')).toEqual(chapters)
    wrapper.unmount()
  })

  it('keeps the script selection empty when no chapters are included', async () => {
    vi.mocked(api.getProductionOverview).mockResolvedValue(overview([
      chapter('extra', 'extra', false),
      chapter('author_note', 'author_note', false),
      chapter('unclassified', 'unclassified', false),
    ]))
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.findComponent(ScriptPanel).props('chapterId')).toBeNull()
    wrapper.unmount()
  })

  it('shows a stage placeholder instead of the script panel while scripts are pending', async () => {
    vi.mocked(api.getProductionOverview).mockResolvedValue(autoOverview({
      bible: { status: 'completed' },
      casting: { status: 'completed' },
      script: { status: 'pending' },
      audio: { status: 'pending' },
    }))
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(false)
    expect(wrapper.text()).toContain('剧本还未开放')
    wrapper.unmount()
  })

  it('shows casting progress and reveals the character panel after the stage finishes', async () => {
    mocks.route.query.tab = 'casting'
    const data = autoOverview({
      bible: { status: 'completed' },
      casting: { status: 'running' },
      script: { status: 'pending' },
      audio: { status: 'pending' },
    })
    vi.mocked(api.getProductionOverview).mockResolvedValue(data)
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.findComponent(CharactersPanel).exists()).toBe(false)
    expect(wrapper.text()).toContain('正在整理角色与分配声音')

    const production = useProductionStore()
    ;(production.overview!.job!.result.stages as Record<string, unknown>).casting = { status: 'failed' }
    await flushPromises()
    expect(wrapper.findComponent(CharactersPanel).exists()).toBe(true)
    expect(wrapper.text()).not.toContain('正在整理角色与分配声音')
    wrapper.unmount()
  })

  it('reacts when the selected chapter stops being included and clears an empty selection', async () => {
    vi.mocked(api.getProductionOverview).mockResolvedValue(overview([
      chapter('story_1', 'story', true),
      chapter('story_2', 'story', true),
    ]))
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()
    const production = useProductionStore()
    const panel = wrapper.findComponent(ScriptPanel)
    expect(panel.props('chapterId')).toBe('story_1')

    production.overview!.chapters[0].analysis_included = false
    await flushPromises()
    expect(panel.props('chapterId')).toBe('story_2')

    production.overview!.chapters[1].analysis_included = false
    await flushPromises()
    expect(panel.props('chapterId')).toBeNull()
    wrapper.unmount()
  })
})

describe('ProductionView script stage gate', () => {
  it('keeps scripts closed before one-click production reaches the script stage', async () => {
    vi.mocked(api.getProductionOverview).mockResolvedValue({
      ...overview([]), script_unit_count: 0, tts_unit_count: 0,
    })
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.text()).toContain('剧本还未开放')
    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(false)
    wrapper.unmount()
  })

  it('opens the script panel after automatic production completes the script stage', async () => {
    const data = autoOverview({ script: { status: 'running' } })
    vi.mocked(api.getProductionOverview).mockResolvedValue(data)
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.text()).toContain('正在生成逐章剧本')
    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(false)

    const production = useProductionStore()
    ;(production.overview!.job!.result.stages as Record<string, unknown>).script = { status: 'completed' }
    await flushPromises()
    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(true)
    wrapper.unmount()
  })

  it('opens a completed script stage even when stage details are absent', async () => {
    const data = autoOverview({})
    data.job!.status = 'completed'
    data.job!.result = { stage: 'script' }
    vi.mocked(api.getProductionOverview).mockResolvedValue(data)
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps failed script results accessible for diagnosis and retry', async () => {
    const data = autoOverview({ script: { status: 'failed' } })
    data.job!.status = 'failed'
    vi.mocked(api.getProductionOverview).mockResolvedValue(data)
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(true)
    expect(wrapper.text()).not.toContain('剧本还未开放')
    wrapper.unmount()
  })

  it('allows existing script results without requiring a new task', async () => {
    vi.mocked(api.getProductionOverview).mockResolvedValue(overview([]))
    const wrapper = shallowMount(ProductionView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.findComponent(ScriptPanel).exists()).toBe(true)
    wrapper.unmount()
  })
})
