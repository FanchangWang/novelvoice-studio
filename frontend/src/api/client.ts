import type {
  AnalysisJob,
  AnalysisStatusResponse,
  ApiErrorPayload,
  AudioAssetOut,
  AudioVersionListResponse,
  BookDetail,
  BookListResponse,
  ChapterAnalysisResponse,
  ChapterDetail,
  ChapterListResponse,
  ChapterMutationResponse,
  ChapterSummary,
  ChapterUpdatePayload,
  CharacterListResponse,
  CharacterMergeResponse,
  CharacterOut,
  CharacterSplitResponse,
  CharacterUpdatePayload,
  CreateBookPayload,
  ImportOptions,
  ImportPreview,
  ImportResult,
  PreviewOptions,
  PublicSettings,
  RelationshipListResponse,
  SegmentBatchUpdateResponse,
  SegmentAudioOut,
  SegmentListFilters,
  SegmentListResponse,
  SegmentOut,
  SegmentUpdatePayload,
  SystemStatus,
  VoiceDesignPayload,
  VoiceDesignResponse,
  VoiceLibraryResponse,
  VoicePreviewPayload,
  VoiceProfileOut,
  VoiceUpdatePayload,
  BookCastsResponse,
  BibleStateResponse,
  BibleStatusResponse,
  CastAssignPayload,
  CastingStatusResponse,
  GeneralVoicePresetListOut,
  ProductionBatchRequest,
  ProductionOverviewOut,
  ReviewBatchResolvePayload,
  ReviewBatchResolveResponse,
  ReviewListResponse,
  ReviewResolvePayload,
  ReviewItemOut,
  SceneStateResponse,
  SceneStatusResponse,
  ScriptStateResponse,
  ScriptStatusResponse,
  ScriptVersionListResponse,
  ScriptVersionOut,
  ScriptUnitAudioOut,
  ScriptUnitOut,
  UnitAudioVersionListResponse,
  VoiceActorCreatePayload,
  VoiceActorListResponse,
  VoiceActorOut,
  VoiceActorUpdatePayload,
} from './types'

const API_BASE = '/api/v1'

export class ApiError extends Error {
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

function buildHeaders(init: RequestInit): Headers {
  const headers = new Headers(init.headers)
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  return headers
}

async function readError(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.json()) as ApiErrorPayload
    if (payload?.error?.message) {
      return new ApiError(
        payload.error.message,
        response.status,
        payload.error.code || `http_${response.status}`,
        payload.error.details ?? {},
      )
    }
  } catch {
    // 非 JSON 错误体，走通用提示
  }
  return new ApiError(
    `请求失败（HTTP ${response.status}）`,
    response.status,
    `http_${response.status}`,
  )
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: buildHeaders(init),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    throw new ApiError('无法连接本地后端，请确认服务已经启动。', 0, 'network_error')
  }

  if (!response.ok) {
    throw await readError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }

  const raw = await response.text()
  if (!raw) {
    return undefined as T
  }
  try {
    return JSON.parse(raw) as T
  } catch {
    throw new ApiError('后端返回了无法解析的数据。', response.status, 'invalid_response')
  }
}

async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: buildHeaders(init),
    })
  } catch {
    throw new ApiError('无法连接本地后端，请确认服务已经启动。', 0, 'network_error')
  }
  if (!response.ok) {
    throw await readError(response)
  }
  return response.blob()
}

function audioUrl(path: string, cacheKey?: string | null): string {
  if (!cacheKey) {
    return path
  }
  return `${path}?v=${encodeURIComponent(cacheKey)}`
}

function jsonBody(method: 'POST' | 'PATCH', payload: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

function formValue(value: string | undefined | null): string | null {
  const cleaned = value?.trim()
  return cleaned ? cleaned : null
}

export const api = {
  getSystemStatus(): Promise<SystemStatus> {
    return request<SystemStatus>('/system/status')
  },

  getPublicSettings(): Promise<PublicSettings> {
    return request<PublicSettings>('/system/settings')
  },

  listBooks(includeDeleted = false, deletedOnly = false): Promise<BookListResponse> {
    const params = new URLSearchParams()
    if (includeDeleted) params.set('include_deleted', 'true')
    if (deletedOnly) params.set('deleted_only', 'true')
    const query = params.size ? `?${params.toString()}` : ''
    return request<BookListResponse>(`/books${query}`)
  },

  createBook(payload: CreateBookPayload): Promise<BookDetail> {
    return request<BookDetail>('/books', jsonBody('POST', payload))
  },

  getBook(bookId: string): Promise<BookDetail> {
    return request<BookDetail>(`/books/${encodeURIComponent(bookId)}`)
  },

  deleteBook(bookId: string): Promise<void> {
    return request<void>(`/books/${encodeURIComponent(bookId)}`, { method: 'DELETE' })
  },

  restoreBook(bookId: string): Promise<BookDetail> {
    return request<BookDetail>(`/books/${encodeURIComponent(bookId)}/restore`, {
      method: 'POST',
    })
  },

  permanentlyDeleteBook(bookId: string): Promise<void> {
    return request<void>(
      `/books/${encodeURIComponent(bookId)}/permanent`,
      { method: 'DELETE' },
    )
  },

  listChapters(
    bookId: string,
    options: { page?: number; pageSize?: number; kind?: ChapterSummary['chapter_kind'] } = {},
  ): Promise<ChapterListResponse> {
    const params = new URLSearchParams()
    if (options.page !== undefined) params.set('page', String(options.page))
    if (options.pageSize !== undefined) params.set('page_size', String(options.pageSize))
    if (options.kind !== undefined) params.set('kind', options.kind)
    const query = params.size ? `?${params.toString()}` : ''
    return request<ChapterListResponse>(
      `/books/${encodeURIComponent(bookId)}/chapters${query}`,
    )
  },

  getChapter(chapterId: string): Promise<ChapterDetail> {
    return request<ChapterDetail>(`/chapters/${encodeURIComponent(chapterId)}`)
  },

  updateChapter(
    chapterId: string,
    payload: ChapterUpdatePayload,
  ): Promise<ChapterSummary> {
    return request<ChapterSummary>(
      `/chapters/${encodeURIComponent(chapterId)}`,
      jsonBody('PATCH', payload),
    )
  },

  splitChapter(
    chapterId: string,
    paragraphIndex: number,
    newTitle?: string,
  ): Promise<ChapterMutationResponse> {
    return request<ChapterMutationResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/split`,
      jsonBody('POST', {
        paragraph_index: paragraphIndex,
        new_title: formValue(newTitle),
      }),
    )
  },

  mergeChapters(chapterIds: string[], newTitle?: string): Promise<ChapterMutationResponse> {
    return request<ChapterMutationResponse>(
      '/chapters/merge',
      jsonBody('POST', {
        chapter_ids: chapterIds,
        new_title: formValue(newTitle),
      }),
    )
  },

  getAnalysisStatus(bookId: string): Promise<AnalysisStatusResponse> {
    return request<AnalysisStatusResponse>(
      `/books/${encodeURIComponent(bookId)}/analysis/status`,
    )
  },

  startBookAnalysis(bookId: string, chapterIds: string[] = []): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/analysis`,
      jsonBody('POST', { chapter_ids: chapterIds }),
    )
  },

  cancelBookAnalysis(bookId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/analysis/cancel`,
      { method: 'POST' },
    )
  },

  analyzeChapter(chapterId: string): Promise<ChapterAnalysisResponse> {
    return request<ChapterAnalysisResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/analysis`,
      { method: 'POST' },
    )
  },

  listCharacters(bookId: string, includeMerged = false): Promise<CharacterListResponse> {
    const query = includeMerged ? '?include_merged=true' : ''
    return request<CharacterListResponse>(
      `/books/${encodeURIComponent(bookId)}/characters${query}`,
    )
  },

  listRelationships(bookId: string): Promise<RelationshipListResponse> {
    return request<RelationshipListResponse>(
      `/books/${encodeURIComponent(bookId)}/relationships`,
    )
  },

  updateCharacter(
    characterId: string,
    payload: CharacterUpdatePayload,
  ): Promise<CharacterOut> {
    return request<CharacterOut>(
      `/characters/${encodeURIComponent(characterId)}`,
      jsonBody('PATCH', payload),
    )
  },

  mergeCharacters(
    targetCharacterId: string,
    sourceCharacterIds: string[],
  ): Promise<CharacterMergeResponse> {
    return request<CharacterMergeResponse>(
      '/characters/merge',
      jsonBody('POST', {
        target_character_id: targetCharacterId,
        source_character_ids: sourceCharacterIds,
      }),
    )
  },

  splitCharacter(
    characterId: string,
    sourceCharacterId: string,
  ): Promise<CharacterSplitResponse> {
    return request<CharacterSplitResponse>(
      '/characters/split',
      jsonBody('POST', {
        character_id: characterId,
        source_character_id: sourceCharacterId,
      }),
    )
  },

  listSegments(
    chapterId: string,
    filters: SegmentListFilters = {},
  ): Promise<SegmentListResponse> {
    const params = new URLSearchParams()
    if (filters.reviewStatus) {
      params.set('review_status', filters.reviewStatus)
    }
    if (filters.segmentType) {
      params.set('segment_type', filters.segmentType)
    }
    if (filters.speakerType) {
      params.set('speaker_type', filters.speakerType)
    }
    if (filters.lowConfidence) {
      params.set('low_confidence', 'true')
    }
    const query = params.toString()
    return request<SegmentListResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/segments${query ? `?${query}` : ''}`,
    )
  },

  updateSegment(segmentId: string, payload: SegmentUpdatePayload): Promise<SegmentOut> {
    return request<SegmentOut>(
      `/segments/${encodeURIComponent(segmentId)}`,
      jsonBody('PATCH', payload),
    )
  },

  batchUpdateSegments(
    segmentIds: string[],
    update: SegmentUpdatePayload,
  ): Promise<SegmentBatchUpdateResponse> {
    return request<SegmentBatchUpdateResponse>(
      '/segments/batch-update',
      jsonBody('POST', { segment_ids: segmentIds, update }),
    )
  },

  getVoiceLibrary(bookId: string): Promise<VoiceLibraryResponse> {
    return request<VoiceLibraryResponse>(
      `/books/${encodeURIComponent(bookId)}/voices`,
    )
  },

  designVoices(
    bookId: string,
    payload: VoiceDesignPayload,
  ): Promise<VoiceDesignResponse> {
    return request<VoiceDesignResponse>(
      `/books/${encodeURIComponent(bookId)}/voices/design`,
      jsonBody('POST', payload),
    )
  },

  selectVoice(voiceId: string): Promise<VoiceProfileOut> {
    return request<VoiceProfileOut>(
      `/voices/${encodeURIComponent(voiceId)}/select`,
      { method: 'POST' },
    )
  },

  updateVoice(
    voiceId: string,
    payload: VoiceUpdatePayload,
  ): Promise<VoiceProfileOut> {
    return request<VoiceProfileOut>(
      `/voices/${encodeURIComponent(voiceId)}`,
      jsonBody('PATCH', payload),
    )
  },

  deleteVoice(voiceId: string): Promise<void> {
    return request<void>(`/voices/${encodeURIComponent(voiceId)}`, {
      method: 'DELETE',
    })
  },

  previewVoice(voiceId: string, payload: VoicePreviewPayload): Promise<Blob> {
    return requestBlob(
      `/voices/${encodeURIComponent(voiceId)}/preview`,
      jsonBody('POST', payload),
    )
  },

  generateSegmentAudio(segmentId: string): Promise<SegmentAudioOut> {
    return request<SegmentAudioOut>(
      `/segments/${encodeURIComponent(segmentId)}/generate`,
      { method: 'POST' },
    )
  },

  listAudioVersions(segmentId: string): Promise<AudioVersionListResponse> {
    return request<AudioVersionListResponse>(
      `/segments/${encodeURIComponent(segmentId)}/audio-versions`,
    )
  },

  restoreAudioAsset(assetId: string): Promise<SegmentAudioOut> {
    return request<SegmentAudioOut>(
      `/audio-assets/${encodeURIComponent(assetId)}/restore`,
      { method: 'POST' },
    )
  },

  voiceAudioUrl(voiceId: string, cacheKey?: string | null): string {
    return audioUrl(
      `/api/v1/voices/${encodeURIComponent(voiceId)}/audio`,
      cacheKey,
    )
  },

  segmentAudioUrl(segmentId: string, cacheKey?: string | null): string {
    return audioUrl(
      `/api/v1/segments/${encodeURIComponent(segmentId)}/audio`,
      cacheKey,
    )
  },

  generateScriptUnitAudio(unitId: string): Promise<ScriptUnitAudioOut> {
    return request<ScriptUnitAudioOut>(
      `/script-units/${encodeURIComponent(unitId)}/generate`,
      { method: 'POST' },
    )
  },

  listScriptUnitAudioVersions(
    unitId: string,
  ): Promise<UnitAudioVersionListResponse> {
    return request<UnitAudioVersionListResponse>(
      `/script-units/${encodeURIComponent(unitId)}/audio-versions`,
    )
  },

  restoreScriptUnitAudioVersion(
    unitId: string,
    assetId: string,
  ): Promise<ScriptUnitAudioOut> {
    return request<ScriptUnitAudioOut>(
      `/script-units/${encodeURIComponent(unitId)}/audio-versions/${encodeURIComponent(assetId)}/restore`,
      { method: 'POST' },
    )
  },

  scriptUnitAudioUrl(unitId: string, cacheKey?: string | null): string {
    return audioUrl(
      `/api/v1/script-units/${encodeURIComponent(unitId)}/audio`,
      cacheKey,
    )
  },

  assetAudioUrl(asset: Pick<AudioAssetOut, 'id' | 'created_at'>): string {
    return audioUrl(
      `/api/v1/audio-assets/${encodeURIComponent(asset.id)}/audio`,
      asset.created_at,
    )
  },

  previewImport(file: File, options: PreviewOptions = {}): Promise<ImportPreview> {
    const form = new FormData()
    form.append('file', file, file.name)
    const encoding = formValue(options.encoding)
    if (encoding) {
      form.append('encoding', encoding)
    }
    const customRegex = formValue(options.customRegex)
    if (customRegex) {
      form.append('custom_regex', customRegex)
    }
    return request<ImportPreview>('/books/import/preview', {
      method: 'POST',
      body: form,
    })
  },

  importBook(file: File, options: ImportOptions = {}): Promise<ImportResult> {
    const form = new FormData()
    form.append('file', file, file.name)
    form.append('duplicate_policy', options.duplicatePolicy ?? 'new')
    form.append('language', options.language ?? 'zh-CN')

    const fields: Array<[string, string | undefined]> = [
      ['encoding', options.encoding],
      ['title', options.title],
      ['author', options.author],
      ['description', options.description],
      ['custom_regex', options.customRegex],
      [
        'replace_book_id',
        options.duplicatePolicy === 'replace' || options.duplicatePolicy === 'merge'
          ? options.replaceBookId
          : undefined,
      ],
    ]
    for (const [key, value] of fields) {
      const cleaned = formValue(value)
      if (cleaned) {
        form.append(key, cleaned)
      }
    }
    return request<ImportResult>('/books/import', {
      method: 'POST',
      body: form,
    })
  },

  /* ------------------------------ v0.5.0 API ------------------------------ */

  getProductionOverview(bookId: string): Promise<ProductionOverviewOut> {
    return request<ProductionOverviewOut>(
      `/books/${encodeURIComponent(bookId)}/production`,
    )
  },

  startAutoProduction(bookId: string, refreshBible = false): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/production/auto`,
      jsonBody('POST', { refresh_bible: refreshBible }),
    )
  },

  getAutoProduction(jobId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/auto-production/${encodeURIComponent(jobId)}`,
    )
  },

  cancelAutoProduction(jobId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/auto-production/${encodeURIComponent(jobId)}/cancel`,
      { method: 'POST' },
    )
  },

  listReviews(params: {
    bookId?: string | null
    status?: string | null
    severity?: string | null
    code?: string | null
    entityType?: string | null
    chapterId?: string | null
    category?: 'content' | 'system' | 'all'
    limit?: number
    offset?: number
  } = {}): Promise<ReviewListResponse> {
    const query = new URLSearchParams()
    if (params.bookId) query.set('book_id', params.bookId)
    if (params.status !== undefined && params.status !== null) {
      query.set('status', params.status)
    }
    if (params.severity) query.set('severity', params.severity)
    if (params.code) query.set('code', params.code)
    if (params.entityType) query.set('entity_type', params.entityType)
    if (params.chapterId) query.set('chapter_id', params.chapterId)
    if (params.category) query.set('category', params.category)
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    if (params.offset !== undefined) query.set('offset', String(params.offset))
    const suffix = query.toString()
    return request<ReviewListResponse>(`/reviews${suffix ? `?${suffix}` : ''}`)
  },

  resolveReview(
    reviewId: string,
    payload: ReviewResolvePayload,
  ): Promise<ReviewItemOut> {
    return request<ReviewItemOut>(
      `/reviews/${encodeURIComponent(reviewId)}/resolve`,
      jsonBody('POST', payload),
    )
  },

  batchResolveReviews(
    payload: ReviewBatchResolvePayload,
  ): Promise<ReviewBatchResolveResponse> {
    return request<ReviewBatchResolveResponse>(
      '/reviews/batch-resolve',
      jsonBody('POST', payload),
    )
  },

  startProductionBatch(
    bookId: string,
    payload: ProductionBatchRequest,
  ): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/production/batch`,
      jsonBody('POST', payload),
    )
  },

  getProductionBatch(jobId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/production-batches/${encodeURIComponent(jobId)}`,
    )
  },

  cancelProductionBatch(jobId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/production-batches/${encodeURIComponent(jobId)}/cancel`,
      { method: 'POST' },
    )
  },

  getBibleStatus(bookId: string): Promise<BibleStatusResponse> {
    return request<BibleStatusResponse>(
      `/books/${encodeURIComponent(bookId)}/bible/status`,
    )
  },

  getBible(bookId: string): Promise<BibleStateResponse> {
    return request<BibleStateResponse>(
      `/books/${encodeURIComponent(bookId)}/bible`,
    )
  },

  startBible(bookId: string, chapterIds: string[] = []): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/bible/analysis`,
      jsonBody('POST', { chapter_ids: chapterIds }),
    )
  },

  cancelBible(bookId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/bible/analysis/cancel`,
      { method: 'POST' },
    )
  },

  finalizeBible(bookId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/bible/analysis/finalize`,
      { method: 'POST' },
    )
  },

  getSceneStatus(chapterId: string): Promise<SceneStatusResponse> {
    return request<SceneStatusResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/scenes/status`,
    )
  },

  getScenes(chapterId: string): Promise<SceneStateResponse> {
    return request<SceneStateResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/scenes`,
    )
  },

  startScenes(chapterId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/chapters/${encodeURIComponent(chapterId)}/scenes`,
      { method: 'POST' },
    )
  },

  cancelScenes(chapterId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/chapters/${encodeURIComponent(chapterId)}/scenes/cancel`,
      { method: 'POST' },
    )
  },

  getScriptStatus(chapterId: string): Promise<ScriptStatusResponse> {
    return request<ScriptStatusResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/script/status`,
    )
  },

  getScript(chapterId: string): Promise<ScriptStateResponse> {
    return request<ScriptStateResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/script`,
    )
  },

  updateScriptUnitSpeaker(
    unitId: string,
    speakerType: 'character' | 'narrator' | 'unknown',
    speakerId: string | null,
  ): Promise<ScriptUnitOut> {
    return request<ScriptUnitOut>(
      `/script-units/${encodeURIComponent(unitId)}/speaker`,
      jsonBody('PATCH', { speaker_type: speakerType, speaker_id: speakerId }),
    )
  },

  listScriptVersions(chapterId: string): Promise<ScriptVersionListResponse> {
    return request<ScriptVersionListResponse>(
      `/chapters/${encodeURIComponent(chapterId)}/script/versions`,
    )
  },

  startScript(chapterId: string, retryScenes = false): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/chapters/${encodeURIComponent(chapterId)}/script`,
      jsonBody('POST', { adaptation_level: 'light', retry_scenes: retryScenes }),
    )
  },

  cancelScript(chapterId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/chapters/${encodeURIComponent(chapterId)}/script/cancel`,
      { method: 'POST' },
    )
  },

  approveScriptVersion(versionId: string): Promise<ScriptVersionOut> {
    return request<ScriptVersionOut>(
      `/script-versions/${encodeURIComponent(versionId)}/approve`,
      { method: 'POST' },
    )
  },

  rollbackScriptVersion(versionId: string): Promise<ScriptVersionOut> {
    return request<ScriptVersionOut>(
      `/script-versions/${encodeURIComponent(versionId)}/rollback`,
      { method: 'POST' },
    )
  },

  listVoiceActors(params: {
    q?: string
    favorite?: boolean
    status?: string
    scope?: 'general' | 'book'
    bookId?: string
    limit?: number
    offset?: number
  } = {}): Promise<VoiceActorListResponse> {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    if (params.favorite !== undefined) query.set('favorite', String(params.favorite))
    if (params.status) query.set('status', params.status)
    if (params.scope) query.set('scope', params.scope)
    if (params.bookId) query.set('book_id', params.bookId)
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    if (params.offset !== undefined) query.set('offset', String(params.offset))
    const suffix = query.toString()
    return request<VoiceActorListResponse>(
      `/voice-actors${suffix ? `?${suffix}` : ''}`,
    )
  },

  listGeneralVoicePresets(): Promise<GeneralVoicePresetListOut> {
    return request<GeneralVoicePresetListOut>('/voice-actors/general-presets')
  },

  generateGeneralVoicePreset(presetId: string): Promise<VoiceActorOut> {
    return request<VoiceActorOut>(
      `/voice-actors/general-presets/${encodeURIComponent(presetId)}/generate`,
      { method: 'POST' },
    )
  },

  createVoiceActor(payload: VoiceActorCreatePayload): Promise<VoiceActorOut> {
    return request<VoiceActorOut>('/voice-actors', jsonBody('POST', payload))
  },

  updateVoiceActor(
    actorId: string,
    payload: VoiceActorUpdatePayload,
  ): Promise<VoiceActorOut> {
    return request<VoiceActorOut>(
      `/voice-actors/${encodeURIComponent(actorId)}`,
      jsonBody('PATCH', payload),
    )
  },

  getBookCasts(bookId: string): Promise<BookCastsResponse> {
    return request<BookCastsResponse>(
      `/books/${encodeURIComponent(bookId)}/casts`,
    )
  },

  getCastingStatus(bookId: string): Promise<CastingStatusResponse> {
    return request<CastingStatusResponse>(
      `/books/${encodeURIComponent(bookId)}/casts/status`,
    )
  },

  startCasting(bookId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/casts/auto`,
      { method: 'POST' },
    )
  },

  cancelCasting(bookId: string): Promise<AnalysisJob> {
    return request<AnalysisJob>(
      `/books/${encodeURIComponent(bookId)}/casts/cancel`,
      { method: 'POST' },
    )
  },

  assignCast(bookId: string, payload: CastAssignPayload): Promise<BookCastsResponse> {
    return request<BookCastsResponse>(
      `/books/${encodeURIComponent(bookId)}/casts/assign`,
      jsonBody('POST', payload),
    )
  },
}