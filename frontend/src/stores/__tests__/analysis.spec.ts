import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', () => ({
  api: {
    getAnalysisStatus: vi.fn(),
    startBookAnalysis: vi.fn(),
    cancelBookAnalysis: vi.fn(),
    analyzeChapter: vi.fn(),
    listCharacters: vi.fn(),
    listRelationships: vi.fn(),
    listSegments: vi.fn(),
    updateSegment: vi.fn(),
    batchUpdateSegments: vi.fn(),
    updateCharacter: vi.fn(),
    mergeCharacters: vi.fn(),
    splitCharacter: vi.fn(),
  },
}))

import { api } from '@/api/client'
import type {
  AnalysisStatusResponse,
  CharacterOut,
  SegmentOut,
} from '@/api/types'
import { useAnalysisStore } from '@/stores/analysis'

const getAnalysisStatus = vi.mocked(api.getAnalysisStatus)
const listCharacters = vi.mocked(api.listCharacters)
const listRelationships = vi.mocked(api.listRelationships)
const listSegments = vi.mocked(api.listSegments)
const updateCharacter = vi.mocked(api.updateCharacter)
const analyzeChapter = vi.mocked(api.analyzeChapter)

function makeStatus(overrides: Partial<AnalysisStatusResponse> = {}): AnalysisStatusResponse {
  return {
    book_id: 'book_1',
    job: {
      id: 'job_1',
      book_id: 'book_1',
      job_type: 'analyze_book',
      status: 'completed',
      total_items: 2,
      completed_items: 2,
      failed_items: 0,
      progress: 1,
      params: {},
      result: {},
      error_code: null,
      error_message: null,
      started_at: '2026-11-25T09:00:00Z',
      finished_at: '2026-11-25T09:01:00Z',
      created_at: '2026-11-25T09:00:00Z',
      updated_at: '2026-11-25T09:01:00Z',
    },
    total_chapters: 2,
    completed_chapters: 2,
    review_chapters: 1,
    failed_chapters: 0,
    total_segments: 2,
    review_segments: 1,
    total_characters: 2,
    chapters: [
      {
        id: 'ch_1',
        title: '第一章',
        sequence: 1,
        volume_number: 0,
        volume_title: '默认卷',
        chapter_kind: 'story',
        analysis_included: true,
        analysis_status: 'needs_review',
        has_summary: true,
        summary: '摘要',
        segment_count: 2,
        review_count: 1,
        error: null,
      },
      {
        id: 'ch_2',
        title: '第二章',
        sequence: 2,
        volume_number: 0,
        volume_title: '默认卷',
        chapter_kind: 'story',
        analysis_included: true,
        analysis_status: 'completed',
        has_summary: true,
        summary: '摘要',
        segment_count: 1,
        review_count: 0,
        error: null,
      },
    ],
    ...overrides,
  }
}

function makeCharacter(overrides: Partial<CharacterOut> = {}): CharacterOut {
  return {
    id: 'char_1',
    book_id: 'book_1',
    canonical_name: '沈秋',
    aliases: [
      {
        name: '小秋',
        confidence: 1,
        basis: 'explicit',
        chapter_id: 'ch_1',
        evidence: [],
        source: 'ai',
      },
    ],
    alias_candidates: [
      {
        name: '秋姐',
        confidence: 0.55,
        basis: 'inferred',
        chapter_id: 'ch_1',
        evidence: [],
        source: 'ai',
        reason: '出现于对话称呼',
      },
    ],
    gender: 'female',
    gender_basis: 'explicit',
    age_range: 'young_adult',
    age_basis: 'inferred',
    character_type: 'human',
    identity_description: '茶馆掌柜',
    personality: ['沉稳'],
    personality_desc: null,
    appearance_desc: null,
    voice_description: null,
    first_chapter_id: 'ch_1',
    evidence: [],
    confidence: 0.8,
    is_provisional: false,
    user_locked: false,
    merged_into_id: null,
    merge_log: [],
    merged_source_ids: [],
    created_at: '2026-11-25T09:00:00Z',
    updated_at: '2026-11-25T09:00:00Z',
    ...overrides,
  }
}

function makeSegment(overrides: Partial<SegmentOut> = {}): SegmentOut {
  return {
    id: 'seg_1',
    book_id: 'book_1',
    chapter_id: 'ch_1',
    segment_index: 0,
    source_text: '“你来了。”',
    speech_text: '“你来了。”',
    segment_type: 'dialogue',
    speaker_type: 'unknown',
    speaker_id: null,
    speaker_name_snapshot: null,
    speaker_confidence: 0.3,
    emotion: 'neutral',
    emotion_confidence: 0.6,
    intensity: 1,
    delivery: 'normal',
    tts_instruction: null,
    speed: 1,
    pause_before_ms: 0,
    pause_after_ms: 0,
    review_status: 'needs_review',
    audio_status: 'none',
    voice_profile_id: null,
    current_audio_asset_id: null,
    user_locked_fields: [],
    text_version: 1,
    source_start_offset: 0,
    source_end_offset: 6,
    evidence: [],
    low_speaker_confidence: true,
    low_emotion_confidence: false,
    created_at: '2026-11-25T09:00:00Z',
    updated_at: '2026-11-25T09:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  getAnalysisStatus.mockResolvedValue(makeStatus())
  listCharacters.mockResolvedValue({ items: [makeCharacter()], total: 1 })
  listRelationships.mockResolvedValue({ items: [], total: 0 })
  listSegments.mockResolvedValue({
    items: [makeSegment()],
    total: 1,
    chapter_id: 'ch_1',
  })
})

describe('analysis store', () => {
  it('loads status, characters and segments with derived counters', async () => {
    const store = useAnalysisStore()
    await store.loadStatus('book_1')
    await store.loadCharacters('book_1')
    await store.loadSegments('ch_1')

    expect(store.status?.review_segments).toBe(1)
    expect(store.currentChapterStatus?.segment_count).toBe(2)
    expect(store.chapterStatus.get('ch_2')?.analysis_status).toBe('completed')
    expect(store.activeCharacters).toHaveLength(1)
    expect(store.pendingCharacters).toBe(1)
    expect(store.reviewSegmentCount).toBe(1)
    expect(store.speakerUnknownCount).toBe(1)
    expect(store.characterName('char_1')).toBe('沈秋')
    expect(store.jobActive).toBe(false)
  })

  it('reports a finished job exactly once', async () => {
    vi.useFakeTimers()
    const store = useAnalysisStore()
    getAnalysisStatus.mockResolvedValueOnce(
      makeStatus({
        job: { ...makeStatus().job!, status: 'running', completed_items: 1, progress: 0.5 },
      }),
    )
    await store.loadStatus('book_1')
    expect(store.jobActive).toBe(true)

    await store.loadStatus('book_1')
    expect(store.jobActive).toBe(false)
    expect(store.jobFinishedTick).toBe(1)

    await store.loadStatus('book_1')
    expect(store.jobFinishedTick).toBe(1)

    store.reset()
    vi.useRealTimers()
  })

  it('analyzes a chapter then refreshes status and characters', async () => {
    const store = useAnalysisStore()
    await store.loadStatus('book_1')
    analyzeChapter.mockResolvedValue({
      chapter: makeStatus().chapters[0]!,
      characters_added: 1,
      relationships_added: 1,
      segments_written: 3,
      meta: {},
    })

    const result = await store.analyzeChapter('ch_1')

    expect(analyzeChapter).toHaveBeenCalledWith('ch_1')
    expect(result.segments_written).toBe(3)
    expect(getAnalysisStatus).toHaveBeenCalledTimes(2)
    expect(listCharacters).toHaveBeenCalledWith('book_1', true)
    expect(listRelationships).toHaveBeenCalledWith('book_1')
  })

  it('accepts an alias candidate while keeping existing aliases', async () => {
    const store = useAnalysisStore()
    await store.loadCharacters('book_1')
    const character = store.activeCharacters[0]!
    const updated = makeCharacter({
      aliases: [
        ...character.aliases,
        {
          name: '秋姐',
          confidence: 1,
          basis: 'explicit',
          chapter_id: 'ch_1',
          evidence: [],
          source: 'manual',
        },
      ],
      alias_candidates: [],
    })
    updateCharacter.mockResolvedValue(updated)

    await store.acceptAlias(character, '秋姐')

    expect(updateCharacter).toHaveBeenCalledWith('char_1', {
      aliases: ['小秋', '秋姐'],
    })
    expect(store.activeCharacters[0]?.alias_candidates).toEqual([])
    expect(store.activeCharacters[0]?.aliases.map((item) => item.name)).toEqual(['小秋', '秋姐'])
  })
})