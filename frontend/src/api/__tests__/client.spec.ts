import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '@/api/client'
import type { ApiErrorPayload, BookListResponse } from '@/api/types'

function fakeResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as Response
}

const fetchMock = vi.fn<typeof fetch>()

function installFetch(): void {
  vi.stubGlobal('fetch', fetchMock)
}

afterEach(() => {
  vi.unstubAllGlobals()
  fetchMock.mockReset()
})

describe('api client', () => {
  it('passes the include_deleted flag and Accept header', async () => {
    installFetch()
    const payload: BookListResponse = { items: [], total: 0 }
    fetchMock.mockResolvedValue(fakeResponse(payload))

    const result = await api.listBooks(true)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/books?include_deleted=true')
    expect((init?.headers as Headers).get('Accept')).toBe('application/json')
  })

  it('queries only deleted books for the recycle bin', async () => {
    installFetch()
    fetchMock.mockResolvedValue(fakeResponse({ items: [], total: 0 }))

    await api.listBooks(false, true)

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/books?deleted_only=true')
  })

  it('maps the unified backend error payload to ApiError', async () => {
    installFetch()
    const payload: ApiErrorPayload = {
      error: {
        code: 'duplicate_book',
        message: '检测到重复导入的书籍',
        details: { book_id: 'book_123' },
      },
    }
    fetchMock.mockResolvedValue(fakeResponse(payload, 409))

    await expect(api.deleteBook('book_123')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      code: 'duplicate_book',
      message: '检测到重复导入的书籍',
      details: { book_id: 'book_123' },
    })
  })

  it('turns network failures into a friendly ApiError', async () => {
    installFetch()
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = (await api.getBook('book_1').catch((err: unknown) => err)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.code).toBe('network_error')
    expect(error.message).toContain('无法连接本地后端')
  })

  it('builds multipart import fields without sending undefined values', async () => {
    installFetch()
    fetchMock.mockResolvedValue(fakeResponse({}, 201))
    const file = new File(['第一回'], 'novel.txt', { type: 'text/plain' })

    await api.importBook(file, {
      encoding: 'gb18030',
      duplicatePolicy: 'replace',
      replaceBookId: 'book_old',
      author: '  ',
    })

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/books/import')
    const form = init?.body as FormData
    expect(form).toBeInstanceOf(FormData)
    expect((form.get('file') as File).name).toBe('novel.txt')
    expect(form.get('encoding')).toBe('gb18030')
    expect(form.get('duplicate_policy')).toBe('replace')
    expect(form.get('replace_book_id')).toBe('book_old')
    expect(form.get('author')).toBeNull()
    expect(form.get('language')).toBe('zh-CN')
  })

  it('builds segment filters and escapes the chapter id', async () => {
    installFetch()
    fetchMock.mockResolvedValue(fakeResponse({ items: [], total: 0, chapter_id: 'ch 1' }))

    await api.listSegments('ch 1', {
      reviewStatus: 'needs_review',
      segmentType: 'dialogue',
      speakerType: 'unknown',
      lowConfidence: true,
    })

    const [url] = fetchMock.mock.calls[0]!
    expect(url).toBe(
      '/api/v1/chapters/ch%201/segments?review_status=needs_review&segment_type=dialogue&speaker_type=unknown&low_confidence=true',
    )
  })

  it('posts analysis requests including selected chapters', async () => {
    installFetch()
    fetchMock.mockResolvedValue(fakeResponse({ id: 'job_1' }, 202))

    await api.startBookAnalysis('book_1', ['ch_2'])

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/v1/books/book_1/analysis')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ chapter_ids: ['ch_2'] })
  })

  it('posts voice design and preview payloads, and builds audio urls', async () => {
    installFetch()
    fetchMock.mockResolvedValue(fakeResponse({ items: [], errors: [], requested: 2 }))
    await api.designVoices('book_1', {
      owner_type: 'character',
      owner_id: 'char_1',
      description: '青年男性',
      candidates: 2,
      seed: 42,
    })

    const designCall = fetchMock.mock.calls[0]!
    expect(designCall[0]).toBe('/api/v1/books/book_1/voices/design')
    expect(designCall[1]?.method).toBe('POST')
    expect(JSON.parse(String(designCall[1]?.body))).toEqual({
      owner_type: 'character',
      owner_id: 'char_1',
      description: '青年男性',
      candidates: 2,
      seed: 42,
    })

    const blob = new Blob([new Uint8Array([82, 73, 70, 70])], { type: 'audio/wav' })
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => blob,
    } as Response)
    const result = await api.previewVoice('voice 1', {
      text: '你来了。',
      emotion: 'sad',
      intensity: 2,
    })
    expect(result).toBe(blob)
    const previewCall = fetchMock.mock.calls[1]!
    expect(previewCall[0]).toBe('/api/v1/voices/voice%201/preview')
    expect(previewCall[1]?.method).toBe('POST')

    expect(api.voiceAudioUrl('voice 1', '2026-11-26')).toBe(
      '/api/v1/voices/voice%201/audio?v=2026-11-26',
    )
    expect(api.segmentAudioUrl('seg_1')).toBe('/api/v1/segments/seg_1/audio')
    expect(
      api.assetAudioUrl({ id: 'asset 1', created_at: '2026-11-26T10:00:00Z' }),
    ).toBe('/api/v1/audio-assets/asset%201/audio?v=2026-11-26T10%3A00%3A00Z')
  })

  it('maps voice preview HTTP errors', async () => {
    installFetch()
    fetchMock.mockResolvedValue(
      fakeResponse(
        {
          error: {
            code: 'dependency_unavailable',
            message: '语音引擎服务未启动',
            details: {},
          },
        },
        503,
      ),
    )

    await expect(api.previewVoice('voice_1', { text: '测试' })).rejects.toMatchObject({
      status: 503,
      code: 'dependency_unavailable',
    })
  })

  it('sends character merge and segment batch payloads', async () => {
    installFetch()
    fetchMock.mockResolvedValue(
      fakeResponse({ target: {}, merged_source_ids: [], updated_segments: 0 }),
    )
    await api.mergeCharacters('char_target', ['char_a', 'char_b'])

    fetchMock.mockResolvedValue(fakeResponse({ updated_count: 2, affected_segment_ids: [] }))
    await api.batchUpdateSegments(['seg_1', 'seg_2'], {
      review_status: 'approved',
      unlock_fields: ['emotion'],
    })

    const mergeCall = fetchMock.mock.calls[0]!
    expect(mergeCall[0]).toBe('/api/v1/characters/merge')
    expect(JSON.parse(String(mergeCall[1]?.body))).toEqual({
      target_character_id: 'char_target',
      source_character_ids: ['char_a', 'char_b'],
    })

    const batchCall = fetchMock.mock.calls[1]!
    expect(batchCall[0]).toBe('/api/v1/segments/batch-update')
    expect(JSON.parse(String(batchCall[1]?.body))).toEqual({
      segment_ids: ['seg_1', 'seg_2'],
      update: { review_status: 'approved', unlock_fields: ['emotion'] },
    })
  })

  it('drives selected script unit audio and builds its urls', async () => {
    installFetch()
    fetchMock.mockResolvedValue(
      fakeResponse({ unit_id: 'unit 1', audio_status: 'ready', asset: null }),
    )

    await api.generateScriptUnitAudio('unit 1')
    await api.listScriptUnitAudioVersions('unit 1')
    await api.restoreScriptUnitAudioVersion('unit 1', 'asset 1')

    const [generateCall, versionsCall, restoreCall] = fetchMock.mock.calls
    expect(generateCall![0]).toBe('/api/v1/script-units/unit%201/generate')
    expect(generateCall![1]?.method).toBe('POST')
    expect(versionsCall![0]).toBe('/api/v1/script-units/unit%201/audio-versions')
    expect(restoreCall![0]).toBe(
      '/api/v1/script-units/unit%201/audio-versions/asset%201/restore',
    )
    expect(restoreCall![1]?.method).toBe('POST')
    expect(api.scriptUnitAudioUrl('unit 1', 'asset_1')).toBe(
      '/api/v1/script-units/unit%201/audio?v=asset_1',
    )
  })
})