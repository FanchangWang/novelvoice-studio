import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { BookSummary } from '@/api/types'
import { errorMessage } from '@/utils/errors'

export const useLibraryStore = defineStore('library', () => {
  const books = ref<BookSummary[]>([])
  const deletedBooks = ref<BookSummary[]>([])
  const loading = ref(false)
  const trashLoading = ref(false)
  const error = ref<string | null>(null)

  const total = computed(() => books.value.length)
  const trashTotal = computed(() => deletedBooks.value.length)
  const totalChapters = computed(() =>
    books.value.reduce((sum, book) => sum + book.chapter_count, 0),
  )
  const totalCharacters = computed(() =>
    books.value.reduce((sum, book) => sum + book.source_char_count, 0),
  )

  async function fetchBooks(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const response = await api.listBooks()
      books.value = response.items
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchTrash(): Promise<void> {
    trashLoading.value = true
    error.value = null
    try {
      const response = await api.listBooks(false, true)
      deletedBooks.value = response.items
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      trashLoading.value = false
    }
  }

  async function refreshAll(): Promise<void> {
    await Promise.all([fetchBooks(), fetchTrash()])
  }

  async function removeBook(bookId: string): Promise<void> {
    await api.deleteBook(bookId)
    await refreshAll()
  }

  async function restoreBook(bookId: string): Promise<void> {
    await api.restoreBook(bookId)
    await refreshAll()
  }

  async function permanentDeleteBook(bookId: string): Promise<void> {
    await api.permanentlyDeleteBook(bookId)
    await refreshAll()
  }

  return {
    books,
    deletedBooks,
    loading,
    trashLoading,
    error,
    total,
    trashTotal,
    totalChapters,
    totalCharacters,
    fetchBooks,
    fetchTrash,
    refreshAll,
    removeBook,
    restoreBook,
    permanentDeleteBook,
  }
})
