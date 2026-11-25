import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getVoiceLibrary: vi.fn(),
    designVoices: vi.fn(),
    selectVoice: vi.fn(),
    updateVoice: vi.fn(),
    deleteVoice: vi.fn(),
  },
}))

import { api } from '@/api/client'
import type {
  VoiceDesignResponse,
  VoiceLibraryResponse,
  VoiceProfileOut,
} from '@/api/types'
import { useVoiceStore } from '@/stores/voices'

const getVoiceLibrary = vi.mocked(api.getVoiceLibrary)
const designVoices = vi.mocked(api.designVoices)
const selectVoice = vi.mocked(api.selectVoice)
const deleteVoice = vi.mocked(api.deleteVoice)

function makeVoice(overrides: Partial<VoiceProfileOut> = {}): VoiceProfileOut {
  return {
    id: 'voice_1',
    owner_type: 'narrator',
    owner_id: 'book_1',
    owner_name: '旁白',
    name: '旁白 · 候选 01',
    description: '低沉温和',
    reference_text: '参考文本',
    instruct_text: null,
    seed: 42,
    status: 'draft',
    is_selected: false,
    is_mock: true,
    sample_rate: 24000,
    duration_ms: 1200,
    generation_params: { candidate_index: 0, reference_processed: false },
    audio_url: '/api/v1/voices/voice_1/audio',
    created_at: '2026-11-26T09:00:00Z',
    updated_at: '2026-11-26T09:00:00Z',
    ...overrides,
  }
}

function makeLibrary(overrides: Partial<VoiceLibraryResponse> = {}): VoiceLibraryResponse {
  const narratorVoice = makeVoice()
  const characterVoice = makeVoice({
    id: 'voice_2',
    owner_type: 'character',
    owner_id: 'char_1',
    owner_name: '沈秋',
    name: '沈秋 · 候选 01',
    is_selected: true,
    status: 'ready',
  })
  return {
    book_id: 'book_1',
    narrator: {
      owner_type: 'narrator',
      owner_id: 'book_1',
      name: '旁白',
      description: '温和旁白',
      gender: null,
      age_range: null,
      has_selected: false,
      selected_profile_id: null,
      profile_count: 1,
      profiles: [narratorVoice],
    },
    characters: [
      {
        owner_type: 'character',
        owner_id: 'char_1',
        name: '沈秋',
        description: '女声',
        gender: 'female',
        age_range: 'young_adult',
        has_selected: true,
        selected_profile_id: 'voice_2',
        profile_count: 1,
        profiles: [characterVoice],
      },
    ],
    total_profiles: 2,
    selected_owners: 1,
    characters_without_voice: [],
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  getVoiceLibrary.mockResolvedValue(makeLibrary())
})

describe('voice store', () => {
  it('loads owners and resolves selected profiles', async () => {
    const store = useVoiceStore()
    await store.load('book_1')

    expect(store.owners).toHaveLength(2)
    expect(store.narrator?.name).toBe('旁白')
    expect(store.selectedOwners).toBe(1)
    expect(store.ownerFor('character', 'char_1')?.name).toBe('沈秋')
    expect(store.selectedProfile('character', 'char_1')?.id).toBe('voice_2')
    expect(store.selectedProfile('narrator', 'book_1')).toBeNull()
    expect(store.selectedProfile('character', null)).toBeNull()
  })

  it('designs candidates then refreshes the library', async () => {
    const store = useVoiceStore()
    await store.load('book_1')
    const response: VoiceDesignResponse = {
      items: [makeVoice()],
      errors: [{ index: 1, message: '超时' }],
      requested: 2,
      book_id: 'book_1',
    }
    designVoices.mockResolvedValue(response)

    const result = await store.design('book_1', {
      owner_type: 'narrator',
      owner_id: 'book_1',
      candidates: 2,
    })

    expect(result).toBe(response)
    expect(designVoices).toHaveBeenCalledWith('book_1', {
      owner_type: 'narrator',
      owner_id: 'book_1',
      candidates: 2,
    })
    expect(getVoiceLibrary).toHaveBeenCalledTimes(2)
  })

  it('selects and deletes voices, refreshing the library', async () => {
    const store = useVoiceStore()
    await store.load('book_1')
    const selected = makeVoice({ is_selected: true, status: 'ready' })
    selectVoice.mockResolvedValue(selected)

    await store.select('voice_1')
    expect(selectVoice).toHaveBeenCalledWith('voice_1')
    expect(getVoiceLibrary).toHaveBeenCalledTimes(2)

    deleteVoice.mockResolvedValue()
    await store.remove('voice_1')
    expect(deleteVoice).toHaveBeenCalledWith('voice_1')
    expect(getVoiceLibrary).toHaveBeenCalledTimes(3)
  })

  it('records load errors and clears stale library', async () => {
    const store = useVoiceStore()
    getVoiceLibrary.mockRejectedValue(new Error('书籍不存在'))

    await expect(store.load('missing')).rejects.toThrow('书籍不存在')
    expect(store.library).toBeNull()
    expect(store.error).toBe('书籍不存在')
  })
})