import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { AnalysisJob, ProductionBatchRequest, ProductionOverviewOut } from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { isJobActive } from '@/utils/analysis'

const POLL_INTERVAL_MS = 3000

export const useProductionStore = defineStore('production', () => {
  const overview = ref<ProductionOverviewOut | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const busyAction = ref<string | null>(null)
  const jobFinishedTick = ref(0)
  const batchJob = ref<AnalysisJob | null>(null)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollTimer: ReturnType<typeof setTimeout> | null = null
  let batchPollToken = 0
  let pollBookId: string | null = null
  let loadToken = 0
  let lastJobActive = false
  let lastOverviewSignature = ''

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

  function stopBatchPolling(): void {
    batchPollToken += 1
    if (batchPollTimer) {
      clearTimeout(batchPollTimer)
      batchPollTimer = null
    }
  }

  function scheduleBatchPoll(jobId: string, token: number): void {
    if (token !== batchPollToken || batchJob.value?.id !== jobId) return
    batchPollTimer = setTimeout(() => {
      batchPollTimer = null
      void pollBatch(jobId, token)
    }, POLL_INTERVAL_MS)
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
      stopBatchPolling()
      batchJob.value = null
      overview.value = null
    }
    if (!silent) {
      error.value = null
    }
    try {
      const response = await api.getProductionOverview(targetBookId)
      if (token !== loadToken) {
        return
      }
      const wasActive = lastJobActive
      const signature = JSON.stringify({
        ...response,
        job: response.job ? { ...response.job, updated_at: null } : null,
      })
      if (signature !== lastOverviewSignature) {
        overview.value = response
        lastOverviewSignature = signature
      }
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

  async function startAutoProduction(refreshBible = false): Promise<void> {
    const id = overview.value?.book_id
    if (!id) {
      return
    }
    await runAction('auto-production', () => api.startAutoProduction(id, refreshBible))
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

  async function startBatch(payload: ProductionBatchRequest): Promise<AnalysisJob | null> {
    const id = overview.value?.book_id
    if (!id) return null
    const job = await runAction('batch', () => api.startProductionBatch(id, payload))
    stopBatchPolling()
    batchJob.value = job
    const token = batchPollToken
    void pollBatch(job.id, token)
    return job
  }

  async function pollBatch(jobId: string, token = batchPollToken): Promise<void> {
    if (token !== batchPollToken || batchJob.value?.id !== jobId) return
    try {
      const job = await api.getProductionBatch(jobId)
      if (token !== batchPollToken || batchJob.value?.id !== jobId) return
      batchJob.value = job
      if (overview.value?.book_id === job.book_id) {
        await load(job.book_id, true)
      }
      if (token !== batchPollToken || batchJob.value?.id !== jobId) return
      if (isJobActive(job.status)) {
        scheduleBatchPoll(jobId, token)
      } else {
        stopBatchPolling()
        jobFinishedTick.value += 1
        if (job.status === 'failed' && job.error_message) {
          error.value = job.error_message
        }
      }
    } catch (err) {
      if (token === batchPollToken) error.value = errorMessage(err)
      stopBatchPolling()
    }
  }

  async function cancelBatch(): Promise<void> {
    const job = batchJob.value
    if (!job || !isJobActive(job.status)) return
    batchJob.value = await runAction('batch-cancel', () => api.cancelProductionBatch(job.id))
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
      if (job.job_type === 'auto_production') {
        await api.cancelAutoProduction(job.id)
      } else if (job.job_type === 'production_batch') {
        await api.cancelProductionBatch(job.id)
      } else if (job.job_type === 'book_bible') {
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
    stopBatchPolling()
    loadToken += 1
    overview.value = null
    loading.value = false
    error.value = null
    busyAction.value = null
    jobFinishedTick.value = 0
    batchJob.value = null
    lastJobActive = false
    lastOverviewSignature = ''
  }

  return {
    overview,
    loading,
    error,
    busyAction,
    jobFinishedTick,
    batchJob,
    currentJob,
    jobActive,
    bookId,
    chapters,
    load,
    refresh,
    runAction,
    startBible,
    startAutoProduction,
    cancelBible,
    startScenes,
    cancelScenes,
    startScript,
    cancelScript,
    startCasting,
    startBatch,
    pollBatch,
    cancelBatch,
    cancelCasting,
    cancelCurrentJob,
    reset,
  }
})