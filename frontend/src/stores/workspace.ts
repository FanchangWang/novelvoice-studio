import { ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { BookDetail, ChapterDetail, ChapterSummary } from '@/api/types'
import { errorMessage } from '@/utils/errors'

export const useWorkspaceStore = defineStore('workspace', () => {
  const book = ref<BookDetail | null>(null)
  const chapters = ref<ChapterSummary[]>([])
  const currentId = ref<string | null>(null)
  const current = ref<ChapterDetail | null>(null)
  const loadingBook = ref(false)
  const loadingChapter = ref(false)
  const error = ref<string | null>(null)

  let bookToken = 0
  let chapterToken = 0

  async function loadBook(bookId: string, preferredChapterId?: string): Promise<void> {
    const token = ++bookToken
    loadingBook.value = true
    loadingChapter.value = false
    error.value = null
    current.value = null
    currentId.value = null
    if (book.value && book.value.id !== bookId) {
      book.value = null
      chapters.value = []
    }

    try {
      const [bookResponse, chapterResponse] = await Promise.all([
        api.getBook(bookId),
        api.listChapters(bookId),
      ])
      if (token !== bookToken) {
        return
      }
      book.value = bookResponse
      chapters.value = chapterResponse.items
      const preferred = chapterResponse.items.find(
        (item) => item.id === preferredChapterId,
      )
      const target = preferred ?? chapterResponse.items[0]
      if (target) {
        await select(target.id)
      }
    } catch (err) {
      if (token === bookToken) {
        book.value = null
        chapters.value = []
        error.value = errorMessage(err)
      }
    } finally {
      if (token === bookToken) {
        loadingBook.value = false
      }
    }
  }

  async function select(chapterId: string): Promise<void> {
    if (!chapterId) {
      return
    }
    const token = ++chapterToken
    currentId.value = chapterId
    loadingChapter.value = true
    error.value = null

    try {
      const detail = await api.getChapter(chapterId)
      if (token === chapterToken) {
        current.value = detail
      }
    } catch (err) {
      if (token === chapterToken) {
        current.value = null
        error.value = errorMessage(err)
      }
    } finally {
      if (token === chapterToken) {
        loadingChapter.value = false
      }
    }
  }

  async function refreshChapters(): Promise<void> {
    if (!book.value) {
      return
    }
    const response = await api.listChapters(book.value.id)
    chapters.value = response.items
  }

  async function renameCurrent(title: string): Promise<void> {
    if (!current.value) {
      return
    }
    const updated = await api.updateChapter(current.value.id, title)
    current.value = {
      ...current.value,
      title: updated.title,
      updated_at: updated.updated_at,
    }
    await refreshChapters()
  }

  async function splitCurrent(
    paragraphIndex: number,
    newTitle?: string,
  ): Promise<ChapterSummary | null> {
    if (!current.value) {
      return null
    }
    const response = await api.splitChapter(current.value.id, paragraphIndex, newTitle)
    chapters.value = response.items
    await select(response.chapter.id)
    return response.chapter
  }

  async function mergeSelected(
    chapterIds: string[],
    newTitle?: string,
  ): Promise<ChapterSummary | null> {
    if (!book.value) {
      return null
    }
    const response = await api.mergeChapters(chapterIds, newTitle)
    chapters.value = response.items
    await select(response.chapter.id)
    return response.chapter
  }

  function reset(): void {
    bookToken += 1
    chapterToken += 1
    book.value = null
    chapters.value = []
    currentId.value = null
    current.value = null
    loadingBook.value = false
    loadingChapter.value = false
    error.value = null
  }

  return {
    book,
    chapters,
    currentId,
    current,
    loadingBook,
    loadingChapter,
    error,
    loadBook,
    select,
    refreshChapters,
    renameCurrent,
    splitCurrent,
    mergeSelected,
    reset,
  }
})