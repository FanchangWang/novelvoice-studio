import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { ReviewCountsOut, ReviewItemOut } from '@/api/types'
import { errorMessage } from '@/utils/errors'

export interface ReviewQuery {
  bookId?: string | null
  status?: string | null
  severity?: string | null
  code?: string | null
  entityType?: string | null
  chapterId?: string | null
  category?: 'content' | 'system' | 'all'
  limit?: number
  offset?: number
}

const EMPTY_COUNTS: ReviewCountsOut = {
  open: 0,
  blocking: 0,
  warning: 0,
  info: 0,
  resolved: 0,
  ignored: 0,
}

export const useReviewStore = defineStore('reviews', () => {
  const items = ref<ReviewItemOut[]>([])
  const counts = ref<ReviewCountsOut>({ ...EMPTY_COUNTS })
  const total = ref(0)
  const loading = ref(false)
  const resolving = ref(false)
  const error = ref<string | null>(null)
  const query = ref<ReviewQuery>({
    status: 'open',
    category: 'content',
    limit: 100,
    offset: 0,
  })

  let loadToken = 0

  const isEmpty = computed(() => !loading.value && items.value.length === 0)
  const selectedCount = computed(() => items.value.filter((item) => item.status === 'open').length)

  async function load(overrides: ReviewQuery = {}): Promise<void> {
    const token = ++loadToken
    const next: ReviewQuery = { ...query.value, ...overrides }
    query.value = next
    loading.value = true
    error.value = null
    try {
      const response = await api.listReviews({
        bookId: next.bookId ?? null,
        status: next.status ? next.status : null,
        severity: next.severity ? next.severity : null,
        code: next.code ?? null,
        entityType: next.entityType ?? null,
        chapterId: next.chapterId ?? null,
        category: next.category ?? 'content',
        limit: next.limit ?? 100,
        offset: next.offset ?? 0,
      })
      if (token !== loadToken) {
        return
      }
      items.value = response.items
      counts.value = response.counts
      total.value = response.total
    } catch (err) {
      if (token === loadToken) {
        error.value = errorMessage(err)
      }
      throw err
    } finally {
      if (token === loadToken) {
        loading.value = false
      }
    }
  }

  async function resolve(
    reviewId: string,
    action: 'accept' | 'ignore',
    note?: string,
  ): Promise<ReviewItemOut> {
    resolving.value = true
    error.value = null
    try {
      const updated = await api.resolveReview(reviewId, {
        action,
        note: note?.trim() || null,
      })
      await load()
      return updated
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      resolving.value = false
    }
  }

  async function batchResolve(
    reviewIds: string[],
    action: 'accept' | 'ignore',
    note?: string,
  ): Promise<number> {
    if (!reviewIds.length) {
      return 0
    }
    resolving.value = true
    error.value = null
    try {
      const response = await api.batchResolveReviews({
        review_ids: reviewIds,
        action,
        note: note?.trim() || null,
      })
      await load()
      return response.resolved + response.ignored
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      resolving.value = false
    }
  }

  function reset(): void {
    loadToken += 1
    items.value = []
    counts.value = { ...EMPTY_COUNTS }
    total.value = 0
    loading.value = false
    resolving.value = false
    error.value = null
    query.value = {
      status: 'open',
      category: 'content',
      limit: 100,
      offset: 0,
    }
  }

  return {
    items,
    counts,
    total,
    loading,
    resolving,
    error,
    query,
    isEmpty,
    selectedCount,
    load,
    resolve,
    batchResolve,
    reset,
  }
})