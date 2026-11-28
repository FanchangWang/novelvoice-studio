import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { AnalysisJob, ProductionOverviewOut } from '@/api/types'

vi.mock('@/api/client', () => ({
  api: {
    getProductionOverview: vi.fn(),
    startProductionBatch: vi.fn(),
    getProductionBatch: vi.fn(),
    cancelProductionBatch: vi.fn(),
  },
}))

import { api } from '@/api/client'
import { useProductionStore } from '@/stores/production'

const getOverview = vi.mocked(api.getProductionOverview)
const startBatch = vi.mocked(api.startProductionBatch)
const getBatch = vi.mocked(api.getProductionBatch)

function overview(): ProductionOverviewOut {
  return { book_id: 'book_1', job: null, chapters: [] } as unknown as ProductionOverviewOut
}

function runningJob(): AnalysisJob {
  return {
    id: 'job_1',
    book_id: 'book_1',
    job_type: 'production_batch',
    status: 'running',
  } as AnalysisJob
}

async function flushPromises(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  getOverview.mockResolvedValue(overview())
  startBatch.mockResolvedValue(runningJob())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('production batch polling', () => {
  it('keeps the overview object when only the job timestamp changes', async () => {
    const job = runningJob()
    getOverview.mockResolvedValue({ ...overview(), job })
    const store = useProductionStore()
    await store.load('book_1')
    const first = store.overview

    getOverview.mockResolvedValue({
      ...overview(),
      job: { ...job, updated_at: '2026-11-27T00:00:05Z' },
    } as ProductionOverviewOut)
    await store.load('book_1', true)

    expect(store.overview).toBe(first)
  })

  it('updates the overview when stage progress changes', async () => {
    const job = runningJob()
    getOverview.mockResolvedValue({ ...overview(), job })
    const store = useProductionStore()
    await store.load('book_1')
    const first = store.overview

    getOverview.mockResolvedValue({
      ...overview(),
      job: { ...job, progress: 0.5, completed_items: 1 },
    } as ProductionOverviewOut)
    await store.load('book_1', true)

    expect(store.overview).not.toBe(first)
    expect(store.overview?.job?.progress).toBe(0.5)
  })

  it('accepts the same overview response again after reset', async () => {
    getOverview.mockResolvedValue({ ...overview(), job: runningJob() })
    const store = useProductionStore()
    await store.load('book_1')
    store.reset()
    await store.load('book_1', true)

    expect(store.overview?.job?.id).toBe('job_1')
  })

  it('stops scheduled polling when the store is reset', async () => {
    vi.useFakeTimers()
    getBatch.mockResolvedValue(runningJob())
    const store = useProductionStore()
    await store.load('book_1')
    await store.startBatch({ stages: ['scenes'] })
    await flushPromises()
    expect(getBatch).toHaveBeenCalledTimes(1)

    store.reset()
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()

    expect(getBatch).toHaveBeenCalledTimes(1)
    expect(store.batchJob).toBeNull()
  })

  it('ignores a batch response that arrives after reset', async () => {
    let resolveBatch!: (job: AnalysisJob) => void
    getBatch.mockReturnValue(new Promise((resolve) => { resolveBatch = resolve }))
    const store = useProductionStore()
    await store.load('book_1')
    await store.startBatch({ stages: ['scenes'] })
    await flushPromises()

    store.reset()
    resolveBatch(runningJob())
    await flushPromises()

    expect(store.batchJob).toBeNull()
    expect(getOverview).toHaveBeenCalledTimes(2)
  })
})
