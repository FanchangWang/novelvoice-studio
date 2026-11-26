import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { ProductionOverviewOut } from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { isJobActive } from '@/utils/analysis'

const POLL_INTERVAL_MS = 1800

export const useProductionStore = defineStore('production', () => {
  const overview = ref<ProductionOverviewOut | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const busyAction = ref<string | null>(null)
  const jobFinishedTick = ref(0)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollBookId: string | null = null
  let loadToken = 0
  let lastJobActive = false

  const currentJob = computed(() => overview.value?.job ?? null)
  const jobActive = computed(() => isJobActive(currentJob.value?.status))
  const bookId = computed(() => overview.value?.book_id ?? null)
  const chapters = computed(() => overview.value?.chapters ?? [])

  function stopPolling(): void {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    pollBookId = null
  }

  function schedulePoll(): void {
    if (!overview.value || !pollBookId || !jobActive.value || pollTimer) {
      return
    }
    const target = pollBookId
    pollTimer = setTimeout(async () => {
      pollTimer = null
      await load(target, true).catch(() => undefined)
      if (pollBookId === target) {
        schedulePoll()
      }
    }, POLL_INTERVAL_MS)
  }

  async function load(targetBookId: string, silent = false): Promise<void> {
    const token = ++loadToken
    if (!silent) {
      loading.value = true
    }
    if (overview.value && overview.value.book_id !== targetBookId) {
      overview.value = null
    }
    error.value = null
    try {
      const response = await api.getProductionOverview(targetBookId)
      if (token !== loadToken) {
        return
      }
      const wasActive = lastJobActive
      overview.value = response
      lastJobActive = isJobActive(response.job?.status)
      pollBookId = targetBookId
      if (lastJobActive) {
        schedulePoll()
      } else {
        stopPolling()
        pollBookId = targetBookId
        if (wasActive) {
          jobFinishedTick.value += 1
        }
        if (response.job?.status === 'failed' && response.job.error_message) {
          error.value = response.job.error_message
        }
      }
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

  async function refresh(): Promise<void> {
    if (pollBookId) {
      await load(pollBookId, true)
    }
  }

  async function runAction<T>(name: string, action: () => Promise<T>): Promise<T> {
    busyAction.value = name
    error.value = null
    try {
      const result = await action()
      if (pollBookId) {
        await load(pollBookId, true)
      }
      return result
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      busyAction.value = null
    }
  }

  async function startBible(): Promise<void> {
    const id = overview.value?.book_id
    if (!id) {
      return
    }
    await runAction('bible', () => api.startBible(id))
  }

  async function cancelBible(): Promise<void> {
    const id = overview.value?.book_id
    if (!id) {
      return
    }
    await runAction('bible-cancel', () => api.cancelBible(id))
  }

  async function startScenes(chapterId: string): Promise<void> {
    await runAction(`scenes:${chapterId}`, () => api.startScenes(chapterId))
  }

  async function cancelScenes(chapterId: string): Promise<void> {
    await runAction(`scenes-cancel:${chapterId}`, () => api.cancelScenes(chapterId))
  }

  async function startScript(chapterId: string): Promise<void> {
    await runAction(`script:${chapterId}`, () => api.startScript(chapterId))
  }

  async function cancelScript(chapterId: string): Promise<void> {
    await runAction(`script-cancel:${chapterId}`, () => api.cancelScript(chapterId))
  }

  async function startCasting(): Promise<void> {
    const id = overview.value?.book_id
    if (!id) {
      return
    }
    await runAction('casting', () => api.startCasting(id))
  }

  async function cancelCasting(): Promise<void> {
    const id = overview.value?.book_id
    if (!id) {
      return
    }
    await runAction('casting-cancel', () => api.cancelCasting(id))
  }

  async function cancelCurrentJob(): Promise<void> {
    const job = currentJob.value
    const book = overview.value
    if (!job || !book) {
      return
    }
    const chapterId =
      typeof job.params.chapter_id === 'string' ? job.params.chapter_id : null
    await runAction('job-cancel', async () => {
      if (job.job_type === 'book_bible') {
        await api.cancelBible(book.book_id)
      } else if (job.job_type === 'chapter_scenes' || job.job_type === 'chapter_scene') {
        if (!chapterId) {
          throw new Error('任务缺少章节信息，无法取消')
        }
        await api.cancelScenes(chapterId)
      } else if (job.job_type === 'chapter_script') {
        if (!chapterId) {
          throw new Error('任务缺少章节信息，无法取消')
        }
        await api.cancelScript(chapterId)
      } else if (job.job_type === 'book_casting') {
        await api.cancelCasting(book.book_id)
      } else {
        throw new Error('该任务类型暂不支持从制作台取消')
      }
    })
  }

  function reset(): void {
    stopPolling()
    loadToken += 1
    overview.value = null
    loading.value = false
    error.value = null
    busyAction.value = null
    jobFinishedTick.value = 0
    lastJobActive = false
  }

  return {
    overview,
    loading,
    error,
    busyAction,
    jobFinishedTick,
    currentJob,
    jobActive,
    bookId,
    chapters,
    load,
    refresh,
    runAction,
    startBible,
    cancelBible,
    startScenes,
    cancelScenes,
    startScript,
    cancelScript,
    startCasting,
    cancelCasting,
    cancelCurrentJob,
    reset,
  }
})