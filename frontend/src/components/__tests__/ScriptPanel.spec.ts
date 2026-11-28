import { flushPromises, shallowMount } from '@vue/test-utils'
import { NButton, NSelect } from 'naive-ui'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ProductionChapterOut } from '@/api/types'

vi.mock('@/api/client', () => ({
  api: {
    getScript: vi.fn(),
    listScriptVersions: vi.fn(),
    getScenes: vi.fn(),
    listCharacters: vi.fn(),
    startScript: vi.fn(),
    cancelScript: vi.fn(),
  },
}))
vi.mock('@/stores/production', () => ({
  useProductionStore: () => ({ refresh: vi.fn() }),
}))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => ({ success: vi.fn(), error: vi.fn() }) }
})

import { api } from '@/api/client'
import ScriptPanel from '@/components/production/ScriptPanel.vue'

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
    script_status: 'not_started',
    script_version: 0,
    unit_count: 0,
    tts_unit_count: 0,
    cue_count: 0,
    audio_ready_units: 0,
    audio_stale_units: 0,
    review_count: 0,
    summary: null,
  }
}

const chapters = [
  chapter('extra', 'extra', false),
  chapter('unclassified', 'unclassified', false),
  chapter('author_note', 'author_note', false),
  chapter('story_excluded', 'story', false),
  chapter('story_1', 'story', true),
  chapter('story_2', 'story', true),
  chapter('extra_included', 'extra', true),
]

function mountPanel(chapterId: string | null = null, rows = chapters, autoScriptRunning = false, autoProductionActive = false) {
  return shallowMount(ScriptPanel, {
    props: { bookId: 'book_1', chapters: rows, chapterId, autoScriptRunning, autoProductionActive },
  })
}

function mockScript(units: Array<Record<string, unknown>> = []) {
  vi.mocked(api.getScript).mockResolvedValue({
    chapter_id: 'story_1',
    book_id: 'book_1',
    script_status: units.length ? 'completed' : 'not_started',
    script_version: units.length ? 1 : 0,
    open_reviews: 0,
    job: null,
    version: null,
    units,
    review_items: [],
  } as never)
  vi.mocked(api.listScriptVersions).mockResolvedValue({
    chapter_id: 'story_1', items: [], total: 0,
  } as never)
  vi.mocked(api.getScenes).mockResolvedValue({
    chapter_id: 'story_1', scenes: [], characters: [], version: 1,
  } as never)
  vi.mocked(api.listCharacters).mockResolvedValue({ items: [], total: 0 } as never)
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ScriptPanel chapter eligibility', () => {
  it('offers only included chapters and defaults to the first included chapter', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const options = wrapper.findComponent(NSelect).props('options') as Array<{ value: string }>
    expect(options.map((option) => option.value)).toEqual(['story_1', 'story_2', 'extra_included'])
    expect(wrapper.emitted('update:chapterId')).toEqual([['story_1']])
  })

  it('moves an excluded selection to the first included chapter, then clears it', async () => {
    const wrapper = mountPanel('story_1')
    await flushPromises()

    await wrapper.setProps({
      chapters: chapters.map((row) => row.id === 'story_1'
        ? { ...row, analysis_included: false }
        : row),
    })
    expect(wrapper.emitted('update:chapterId')?.at(-1)).toEqual(['story_2'])

    await wrapper.setProps({
      chapters: chapters.map((row) => ({ ...row, analysis_included: false })),
    })
    expect(wrapper.emitted('update:chapterId')?.at(-1)).toEqual([null])
  })

  it('shows the precise empty state without a script generation button', async () => {
    const wrapper = mountPanel('extra', [
      chapter('extra', 'extra', false),
      chapter('author_note', 'author_note', false),
      chapter('unclassified', 'unclassified', false),
    ])
    await flushPromises()

    expect(wrapper.text()).toContain('没有纳入制作的章节，请先在章节页确认正文')
    expect(wrapper.text()).not.toContain('本书还没有章节')
    expect(wrapper.findAll('button').some((button) => button.text().includes('生成剧本'))).toBe(false)
    expect(wrapper.findComponent(NSelect).props('options')).toEqual([])
    expect(api.getScript).not.toHaveBeenCalled()
  })

  it('keeps completed units visible while automatic chapter generation is running', async () => {
    mockScript([{
      id: 'unit_ready', unit_index: 0, unit_type: 'narration', text: '已完成的章节内容',
      display_text: '已完成的章节内容', speaker_type: 'narrator', audio_status: 'ready',
    }])
    const wrapper = mountPanel('story_1', chapters, true)
    await flushPromises()

    expect(wrapper.text()).toContain('已完成的章节内容')
    expect(wrapper.text()).not.toContain('本章正在等待自动生成')
  })

  it('waits for automatic generation without offering a conflicting manual action', async () => {
    mockScript()
    const wrapper = mountPanel('story_1', chapters, true)
    await flushPromises()

    expect(wrapper.text()).toContain('本章正在等待自动生成')
    const generate = wrapper.findAllComponents(NButton).find((button) => button.props('type') === 'primary')
    expect(generate).toBeUndefined()
  })

  it('does not offer manual generation while another automatic stage is running', async () => {
    mockScript()
    const wrapper = mountPanel('story_1', chapters, false, true)
    await flushPromises()

    expect(wrapper.text()).toContain('自动制作进行中')
    expect(wrapper.findAllComponents(NButton).find((button) => button.props('type') === 'primary')).toBeUndefined()
  })

  it('retries scenes and script together after automatic production ends', async () => {
    mockScript()
    vi.mocked(api.startScript).mockResolvedValue({ id: 'job_retry' } as never)
    const row = { ...chapter('story_1', 'story', true), scene_count: 0, scene_status: 'needs_review' }
    const wrapper = mountPanel('story_1', [row], false)
    await flushPromises()
    const generate = wrapper.findAllComponents(NButton).find((button) => button.props('type') === 'primary')
    expect(generate?.props('disabled')).toBe(false)
    const onClick = generate?.props('onClick')
    if (typeof onClick !== 'function') {
      throw new Error('生成剧本按钮没有点击处理函数')
    }
    await onClick(new MouseEvent('click'))
    await flushPromises()

    expect(api.startScript).toHaveBeenCalledWith('story_1', true)
  })
})
