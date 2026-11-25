import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { BookSummary } from '@/api/types'
import { errorMessage } from '@/utils/errors'

export const useLibraryStore = defineStore('library', () => {
  const books = ref<BookSummary[]>([])
  const includeDeleted = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const total = computed(() => books.value.length)
  const totalChapters = computed(() =>
    books.value
      .filter((book) => book.status !== 'deleted')
      .reduce((sum, book) => sum + book.chapter_count, 0),
  )
  const totalCharacters = computed(() =>
    books.value
      .filter((book) => book.status !== 'deleted')
      .reduce((sum, book) => sum + book.source_char_count, 0),
  )

  async function fetchBooks(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const response = await api.listBooks(includeDeleted.value)
      books.value = response.items
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function setIncludeDeleted(value: boolean): Promise<void> {
    includeDeleted.value = value
    await fetchBooks()
  }

  async function removeBook(bookId: string): Promise<void> {
    await api.deleteBook(bookId)
    await fetchBooks()
  }

  async function restoreBook(bookId: string): Promise<void> {
    await api.restoreBook(bookId)
    await fetchBooks()
  }

  return {
    books,
    includeDeleted,
    loading,
    error,
    total,
    totalChapters,
    totalCharacters,
    fetchBooks,
    setIncludeDeleted,
    removeBook,
    restoreBook,
  }
})