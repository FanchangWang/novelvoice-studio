import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type {
  AnalysisStatusResponse,
  CharacterOut,
  RelationshipOut,
  SegmentListFilters,
  SegmentOut,
  SegmentUpdatePayload,
  ChapterAnalysisResponse,
} from '@/api/types'
import { errorMessage } from '@/utils/errors'
import { isJobActive } from '@/utils/analysis'

const POLL_INTERVAL_MS = 1600

export const useAnalysisStore = defineStore('analysis', () => {
  const status = ref<AnalysisStatusResponse | null>(null)
  const characters = ref<CharacterOut[]>([])
  const relationships = ref<RelationshipOut[]>([])
  const segments = ref<SegmentOut[]>([])
  const segmentsChapterId = ref<string | null>(null)

  const loadingStatus = ref(false)
  const loadingCharacters = ref(false)
  const loadingSegments = ref(false)
  const savingSegment = ref(false)
  const analyzingChapter = ref(false)
  const error = ref<string | null>(null)
  const jobFinishedTick = ref(0)

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollBookId: string | null = null
  let statusToken = 0
  let segmentsToken = 0
  let lastJobActive = false

  const activeCharacters = computed(() =>
    characters.value.filter((item) => !item.merged_into_id),
  )

  const mergedCharacters = computed(() =>
    characters.value.filter((item) => Boolean(item.merged_into_id)),
  )

  const pendingCharacters = computed(
    () =>
      activeCharacters.value.filter(
        (item) =>
          item.is_provisional ||
          item.alias_candidates.length > 0 ||
          item.confidence < 0.7,
      ).length,
  )

  const currentJob = computed(() => status.value?.job ?? null)
  const jobActive = computed(() => isJobActive(currentJob.value?.status))

  const chapterStatus = computed(() => {
    const map = new Map<string, AnalysisStatusResponse['chapters'][number]>()
    for (const item of status.value?.chapters ?? []) {
      map.set(item.id, item)
    }
    return map
  })

  const currentChapterStatus = computed(() => {
    if (!segmentsChapterId.value) {
      return null
    }
    return chapterStatus.value.get(segmentsChapterId.value) ?? null
  })

  const reviewSegmentCount = computed(
    () => segments.value.filter((item) => item.review_status === 'needs_review').length,
  )

  const speakerUnknownCount = computed(
    () =>
      segments.value.filter(
        (item) =>
          item.review_status === 'needs_review' ||
          item.speaker_type === 'unknown' ||
          item.low_speaker_confidence,
      ).length,
  )

  function characterName(characterId: string | null | undefined): string | null {
    if (!characterId) {
      return null
    }
    const match = characters.value.find((item) => item.id === characterId)
    return match?.canonical_name ?? null
  }

  function stopPolling(): void {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    pollBookId = null
  }

  function schedulePoll(): void {
    if (!status.value || !pollBookId || !jobActive.value || pollTimer) {
      return
    }
    const bookId = pollBookId
    pollTimer = setTimeout(async () => {
      pollTimer = null
      await loadStatus(bookId, true).catch(() => undefined)
      if (pollBookId === bookId) {
        schedulePoll()
      }
    }, POLL_INTERVAL_MS)
  }

  async function loadStatus(bookId: string, silent = false): Promise<void> {
    const token = ++statusToken
    if (!silent) {
      loadingStatus.value = true
    }
    try {
      const response = await api.getAnalysisStatus(bookId)
      if (token !== statusToken) {
        return
      }
      const wasActive = lastJobActive
      status.value = response
      lastJobActive = isJobActive(response.job?.status)
      pollBookId = bookId

      if (lastJobActive) {
        schedulePoll()
      } else {
        stopPolling()
        if (wasActive) {
          jobFinishedTick.value += 1
        } else if (response.job?.status === 'failed' && response.job.error_message) {
          error.value = response.job.error_message
        }
      }
    } catch (err) {
      if (token === statusToken) {
        error.value = errorMessage(err)
      }
      throw err
    } finally {
      if (token === statusToken) {
        loadingStatus.value = false
      }
    }
  }

  async function startBookAnalysis(chapterIds: string[] = []): Promise<void> {
    if (!status.value) {
      return
    }
    error.value = null
    try {
      await api.startBookAnalysis(status.value.book_id, chapterIds)
      await loadStatus(status.value.book_id)
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    }
  }

  async function cancelBookAnalysis(): Promise<void> {
    if (!status.value) {
      return
    }
    error.value = null
    await api.cancelBookAnalysis(status.value.book_id)
    await loadStatus(status.value.book_id)
  }

  async function analyzeChapter(chapterId: string): Promise<ChapterAnalysisResponse> {
    analyzingChapter.value = true
    error.value = null
    try {
      const result = await api.analyzeChapter(chapterId)
      const bookId = result.chapter ? status.value?.book_id : null
      if (bookId) {
        await loadStatus(bookId, true)
        await Promise.all([loadCharacters(bookId, true), loadRelationships(bookId)])
      }
      return result
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      analyzingChapter.value = false
    }
  }

  async function loadCharacters(bookId: string, silent = false): Promise<void> {
    if (!silent) {
      loadingCharacters.value = true
    }
    error.value = null
    try {
      const response = await api.listCharacters(bookId, true)
      characters.value = response.items
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      loadingCharacters.value = false
    }
  }

  async function loadRelationships(bookId: string): Promise<void> {
    try {
      const response = await api.listRelationships(bookId)
      relationships.value = response.items
    } catch (err) {
      error.value = errorMessage(err)
    }
  }

  async function loadSegments(
    chapterId: string,
    filters: SegmentListFilters = {},
    silent = false,
  ): Promise<void> {
    const token = ++segmentsToken
    if (!silent) {
      loadingSegments.value = true
    }
    error.value = null
    try {
      const response = await api.listSegments(chapterId, filters)
      if (token === segmentsToken) {
        segments.value = response.items
        segmentsChapterId.value = chapterId
      }
    } catch (err) {
      if (token === segmentsToken) {
        segments.value = []
        segmentsChapterId.value = chapterId
        error.value = errorMessage(err)
      }
      throw err
    } finally {
      if (token === segmentsToken) {
        loadingSegments.value = false
      }
    }
  }

  async function refreshSegments(): Promise<void> {
    if (segmentsChapterId.value) {
      await loadSegments(segmentsChapterId.value, {}, true)
    }
  }

  async function saveSegment(
    segmentId: string,
    payload: SegmentUpdatePayload,
  ): Promise<SegmentOut> {
    savingSegment.value = true
    error.value = null
    try {
      const updated = await api.updateSegment(segmentId, payload)
      const index = segments.value.findIndex((item) => item.id === updated.id)
      if (index >= 0) {
        segments.value[index] = updated
      }
      if (status.value) {
        await loadStatus(status.value.book_id, true)
      }
      return updated
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      savingSegment.value = false
    }
  }

  async function batchUpdateSegments(
    segmentIds: string[],
    payload: SegmentUpdatePayload,
  ): Promise<number> {
    savingSegment.value = true
    error.value = null
    try {
      const response = await api.batchUpdateSegments(segmentIds, payload)
      await refreshSegments()
      if (status.value) {
        await loadStatus(status.value.book_id, true)
      }
      return response.updated_count
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      savingSegment.value = false
    }
  }

  async function saveCharacter(
    characterId: string,
    payload: Parameters<typeof api.updateCharacter>[1],
  ): Promise<CharacterOut> {
    error.value = null
    try {
      const updated = await api.updateCharacter(characterId, payload)
      const index = characters.value.findIndex((item) => item.id === updated.id)
      if (index >= 0) {
        characters.value[index] = updated
      } else {
        characters.value.push(updated)
      }
      return updated
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    }
  }

  async function acceptAlias(character: CharacterOut, name: string): Promise<CharacterOut> {
    const aliases = [
      ...character.aliases.map((item) => item.name),
      name,
    ]
    return saveCharacter(character.id, { aliases })
  }

  async function mergeCharacters(
    targetCharacterId: string,
    sourceCharacterIds: string[],
  ): Promise<number> {
    error.value = null
    try {
      const response = await api.mergeCharacters(targetCharacterId, sourceCharacterIds)
      if (status.value) {
        const bookId = status.value.book_id
        await Promise.all([
          loadCharacters(bookId, true),
          loadRelationships(bookId),
          loadStatus(bookId, true),
        ])
      }
      await refreshSegments()
      return response.updated_segments
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    }
  }

  async function splitCharacter(
    characterId: string,
    sourceCharacterId: string,
  ): Promise<number> {
    error.value = null
    try {
      const response = await api.splitCharacter(characterId, sourceCharacterId)
      if (status.value) {
        const bookId = status.value.book_id
        await Promise.all([
          loadCharacters(bookId, true),
          loadRelationships(bookId),
          loadStatus(bookId, true),
        ])
      }
      await refreshSegments()
      return response.updated_segments
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    }
  }

  function reset(): void {
    stopPolling()
    statusToken += 1
    segmentsToken += 1
    status.value = null
    characters.value = []
    relationships.value = []
    segments.value = []
    segmentsChapterId.value = null
    loadingStatus.value = false
    loadingCharacters.value = false
    loadingSegments.value = false
    savingSegment.value = false
    analyzingChapter.value = false
    error.value = null
    lastJobActive = false
  }

  return {
    status,
    characters,
    relationships,
    segments,
    segmentsChapterId,
    loadingStatus,
    loadingCharacters,
    loadingSegments,
    savingSegment,
    analyzingChapter,
    error,
    jobFinishedTick,
    activeCharacters,
    mergedCharacters,
    pendingCharacters,
    currentJob,
    jobActive,
    chapterStatus,
    currentChapterStatus,
    reviewSegmentCount,
    speakerUnknownCount,
    characterName,
    loadStatus,
    startBookAnalysis,
    cancelBookAnalysis,
    analyzeChapter,
    loadCharacters,
    loadRelationships,
    loadSegments,
    refreshSegments,
    saveSegment,
    batchUpdateSegments,
    saveCharacter,
    acceptAlias,
    mergeCharacters,
    splitCharacter,
    reset,
  }
})