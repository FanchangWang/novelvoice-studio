import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BookDetail, ChapterDetail, ChapterSummary } from '@/api/types'

const mocks = vi.hoisted(() => ({ push: vi.fn() }))
const route = { query: {} as Record<string, string> }

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('@/api/client', () => ({
  api: { getChapter: vi.fn(), getBook: vi.fn(), listChapters: vi.fn() },
}))

import { api } from '@/api/client'
import ChapterReaderView from '@/views/ChapterReaderView.vue'

const chapter = (id: string, sequence: number, title: string) => ({
  id, sequence, title, volume_number: 1, volume_title: '第一卷',
}) as ChapterSummary
const chapters = [chapter('ch_1', 1, '开始'), chapter('ch_2', 2, '继续')]
const detail = {
  ...chapters[0], book_id: 'book_1', text: '第一章\n\n正文内容。',
  char_count: 8, paragraphs: [
    { index: 0, text: '第一章', start_offset: 0, end_offset: 3 },
    { index: 1, text: '正文内容。', start_offset: 5, end_offset: 10 },
  ],
} as unknown as ChapterDetail

beforeEach(() => {
  vi.clearAllMocks()
  route.query = {}
  vi.mocked(api.getChapter).mockResolvedValue(detail)
  vi.mocked(api.getBook).mockResolvedValue({ id: 'book_1', title: '测试书' } as BookDetail)
  vi.mocked(api.listChapters).mockResolvedValue({ items: chapters, total: 2, page: 1, page_size: 100 })
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.scrollTo = vi.fn()
})

describe('ChapterReaderView', () => {
  it('opens the current chapter and renders an independently scrolling reader', async () => {
    const wrapper = mount(ChapterReaderView, { props: { bookId: 'book_1', chapterId: 'ch_1' } })
    await flushPromises()

    expect(api.getChapter).toHaveBeenCalledWith('ch_1')
    expect(api.listChapters).toHaveBeenCalledWith('book_1', { page: 1, pageSize: 100 })
    expect(wrapper.find('.reader-content').classes()).toContain('overflow-y-auto')
    expect(wrapper.text()).toContain('正文内容。')
    expect(wrapper.find('[aria-current="page"]').text()).toContain('开始')
  })

  it('loads large directories in API-supported pages', async () => {
    const all = Array.from({ length: 205 }, (_, index) => chapter(`ch_${index + 1}`, index + 1, `章节 ${index + 1}`))
    vi.mocked(api.listChapters).mockImplementation(async (_bookId, options) => {
      const page = options?.page ?? 1
      const pageSize = options?.pageSize ?? 100
      return { items: all.slice((page - 1) * pageSize, page * pageSize), total: all.length, page, page_size: pageSize }
    })
    const wrapper = mount(ChapterReaderView, { props: { bookId: 'book_1', chapterId: 'ch_205' } })
    await flushPromises()

    expect(api.listChapters).toHaveBeenCalledTimes(3)
    expect(api.listChapters).toHaveBeenLastCalledWith('book_1', { page: 3, pageSize: 100 })
    expect(wrapper.text()).toContain('205 章')
    expect(wrapper.findAll('button').find((button) => button.text() === '下一章')?.attributes('disabled')).toBeDefined()
  })

  it('switches chapters from the directory and clears source-location parameters', async () => {
    route.query = { quote: '旧片段', start: '1', end: '3' }
    const wrapper = mount(ChapterReaderView, { props: { bookId: 'book_1', chapterId: 'ch_1' } })
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('继续'))?.trigger('click')
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith({
      name: 'chapter-reader', params: { bookId: 'book_1', chapterId: 'ch_2' }, query: {},
    })
  })
})
