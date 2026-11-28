import { flushPromises, mount } from '@vue/test-utils'
import { NPagination } from 'naive-ui'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BookDetail, ChapterDetail, ChapterListResponse, ChapterSummary, ProductionOverviewOut } from '@/api/types'

vi.mock('@/api/client', () => ({
  api: { listChapters: vi.fn(), getChapter: vi.fn(), getBook: vi.fn(), updateChapter: vi.fn() },
}))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => ({ success: vi.fn(), error: vi.fn() }) }
})

import { api } from '@/api/client'
import ChaptersPanel from '@/components/production/ChaptersPanel.vue'

const listChapters = vi.mocked(api.listChapters)

function chapter(id: string, kind: ChapterSummary['chapter_kind']): ChapterSummary {
  return {
    id,
    book_id: 'book_1',
    title: `${kind} 章节`,
    sequence: 1,
    char_count: 100,
    source_start_offset: 0,
    source_end_offset: 100,
    analysis_status: 'not_started',
    volume_number: 0,
    volume_title: '默认卷',
    chapter_kind: kind,
    classification_status: 'confirmed',
    analysis_included: kind === 'story',
    created_at: '2026-11-27T00:00:00Z',
    updated_at: '2026-11-27T00:00:00Z',
  }
}

function mountPanel() {
  return mount(ChaptersPanel, {
    props: {
      bookId: 'book_1',
      overview: { book_id: 'book_1', chapter_count: 2, chapters: [] } as unknown as ProductionOverviewOut,
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  listChapters.mockResolvedValue({
    items: [chapter('ch_1', 'story'), chapter('ch_2', 'unclassified')],
    total: 22,
    page: 1,
    page_size: 20,
  } satisfies ChapterListResponse)
  vi.mocked(api.getChapter).mockResolvedValue({
    ...chapter('ch_1', 'story'),
    book_id: 'book_1',
    text: '正文内容。',
    paragraphs: [{ index: 0, text: '正文内容。', start_offset: 0, end_offset: 5 }],
  } as unknown as ChapterDetail)
  vi.mocked(api.getBook).mockResolvedValue({ id: 'book_1', title: '测试书' } as BookDetail)
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.scrollTo = vi.fn()
})

describe('ChaptersPanel', () => {
  it('loads a paginated all-chapters list and shows chapter actions', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(listChapters).toHaveBeenCalledWith('book_1', {
      page: 1,
      pageSize: 20,
      kind: undefined,
    })
    expect(wrapper.text()).toContain('全书 2 章')
    expect(wrapper.text()).toContain('共 22 章')
    expect(wrapper.html().match(/查看剧本/g)).toHaveLength(1)
    expect(wrapper.text()).toContain('需要先确认是正文、番外还是作者说明')
  })

  it('requests only unclassified chapters when that filter is selected', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-filter="unclassified"]').trigger('click')
    await flushPromises()

    expect(listChapters).toHaveBeenLastCalledWith('book_1', {
      page: 1,
      pageSize: 20,
      kind: 'unclassified',
    })
  })

  it('opens source inside the chapters tab and restores the same filter and page', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('[data-filter="story"]').trigger('click')
    await flushPromises()
    wrapper.findComponent(NPagination).vm.$emit('update:page', 2)
    await flushPromises()
    const listCallsBeforeReading = listChapters.mock.calls.length

    await wrapper.findAll('button').find((button) => button.text() === '查看原文')?.trigger('click')
    await flushPromises()

    expect(open).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('返回章节管理')
    expect(wrapper.text()).toContain('正文内容。')
    expect(listChapters).toHaveBeenLastCalledWith('book_1', { page: 1, pageSize: 100 })
    const callsAfterReading = listChapters.mock.calls.length
    expect(callsAfterReading).toBeGreaterThan(listCallsBeforeReading)

    await wrapper.findAll('button').find((button) => button.text() === '← 返回章节管理')?.trigger('click')
    await flushPromises()

    expect(listChapters).toHaveBeenCalledTimes(callsAfterReading)
    expect(wrapper.get('[data-filter="story"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.findComponent(NPagination).props('page')).toBe(2)
    expect(wrapper.text()).toContain('章节管理')
    open.mockRestore()
  })
})
