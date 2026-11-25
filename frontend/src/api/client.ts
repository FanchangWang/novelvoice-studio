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

  listBooks(includeDeleted = false): Promise<BookListResponse> {
    const query = includeDeleted ? '?include_deleted=true' : ''
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

  listChapters(bookId: string): Promise<ChapterListResponse> {
    return request<ChapterListResponse>(
      `/books/${encodeURIComponent(bookId)}/chapters`,
    )
  },

  getChapter(chapterId: string): Promise<ChapterDetail> {
    return request<ChapterDetail>(`/chapters/${encodeURIComponent(chapterId)}`)
  },

  updateChapter(chapterId: string, title: string): Promise<ChapterSummary> {
    return request<ChapterSummary>(
      `/chapters/${encodeURIComponent(chapterId)}`,
      jsonBody('PATCH', { title }),
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
        options.duplicatePolicy === 'replace' ? options.replaceBookId : undefined,
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
}