import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { BookSummary } from '@/api/types'

vi.mock('@/api/client', () => {
  class ApiError extends Error {
    readonly status: number
    readonly code: string
    readonly details: unknown

    constructor(message: string, status = 0, code = 'network_error', details: unknown = {}) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.code = code
      this.details = details
    }
  }

  return {
    ApiError,
    api: {
      listBooks: vi.fn(),
      deleteBook: vi.fn(),
      restoreBook: vi.fn(),
      permanentlyDeleteBook: vi.fn(),
    },
  }
})

import { api } from '@/api/client'
import { useLibraryStore } from '@/stores/library'

const listBooks = vi.mocked(api.listBooks)
const deleteBook = vi.mocked(api.deleteBook)
const restoreBook = vi.mocked(api.restoreBook)
const permanentlyDeleteBook = vi.mocked(api.permanentlyDeleteBook)

function makeBook(overrides: Partial<BookSummary> = {}): BookSummary {
  return {
    id: 'book_1',
    title: '长夜将明',
    author: '测试作者',
    description: null,
    language: 'zh-CN',
    status: 'ready',
    source_encoding: 'utf-8',
    source_char_count: 1200,
    chapter_count: 3,
    created_at: '2026-11-25T09:00:00Z',
    updated_at: '2026-11-25T09:10:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  listBooks.mockResolvedValue({ items: [makeBook()], total: 1 })
})

describe('library store', () => {
  it('loads active books and derives totals', async () => {
    const store = useLibraryStore()
    await store.fetchBooks()

    expect(listBooks).toHaveBeenCalledWith()
    expect(store.total).toBe(1)
    expect(store.totalChapters).toBe(3)
    expect(store.totalCharacters).toBe(1200)
  })

  it('loads the recycle bin through a dedicated query', async () => {
    const store = useLibraryStore()
    listBooks.mockResolvedValue({
      items: [makeBook({ status: 'deleted' })],
      total: 1,
    })

    await store.fetchTrash()

    expect(listBooks).toHaveBeenCalledWith(false, true)
    expect(store.trashTotal).toBe(1)
    expect(store.totalChapters).toBe(0)
  })

  it('moves a book to trash and refreshes both lists', async () => {
    const store = useLibraryStore()
    await store.fetchBooks()
    deleteBook.mockResolvedValue(undefined)
    listBooks
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [makeBook({ status: 'deleted' })], total: 1 })

    await store.removeBook('book_1')

    expect(deleteBook).toHaveBeenCalledWith('book_1')
    expect(store.books).toEqual([])
    expect(store.deletedBooks).toHaveLength(1)
  })

  it('restores a book through the API', async () => {
    const store = useLibraryStore()
    restoreBook.mockResolvedValue(makeBook() as never)
    listBooks
      .mockResolvedValueOnce({ items: [makeBook()], total: 1 })
      .mockResolvedValueOnce({ items: [], total: 0 })

    await store.restoreBook('book_1')

    expect(restoreBook).toHaveBeenCalledWith('book_1')
    expect(store.books).toHaveLength(1)
    expect(store.deletedBooks).toEqual([])
  })

  it('permanently deletes a book and refreshes both lists', async () => {
    const store = useLibraryStore()
    permanentlyDeleteBook.mockResolvedValue(undefined)
    listBooks.mockResolvedValue({ items: [], total: 0 })

    await store.permanentDeleteBook('book_1')

    expect(permanentlyDeleteBook).toHaveBeenCalledWith('book_1')
    expect(store.books).toEqual([])
    expect(store.deletedBooks).toEqual([])
  })

  it('captures load errors without throwing', async () => {
    listBooks.mockRejectedValue(new Error('数据库不可用'))
    const store = useLibraryStore()

    await store.fetchBooks()

    expect(store.error).toBe('数据库不可用')
    expect(store.books).toEqual([])
  })
})
