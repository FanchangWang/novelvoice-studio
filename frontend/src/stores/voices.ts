import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type {
  VoiceDesignPayload,
  VoiceDesignResponse,
  VoiceLibraryResponse,
  VoiceOwnerOut,
  VoiceProfileOut,
  VoiceUpdatePayload,
} from '@/api/types'
import { errorMessage } from '@/utils/errors'

export const useVoiceStore = defineStore('voices', () => {
  const library = ref<VoiceLibraryResponse | null>(null)
  const loading = ref(false)
  const designing = ref(false)
  const error = ref<string | null>(null)

  let loadToken = 0

  const narrator = computed<VoiceOwnerOut | null>(() => library.value?.narrator ?? null)
  const characters = computed(() => library.value?.characters ?? [])
  const selectedOwners = computed(() => library.value?.selected_owners ?? 0)
  const owners = computed<VoiceOwnerOut[]>(() => {
    const list: VoiceOwnerOut[] = []
    if (narrator.value) {
      list.push(narrator.value)
    }
    return [...list, ...characters.value]
  })

  function ownerFor(ownerType: string, ownerId: string | null): VoiceOwnerOut | null {
    if (!ownerId) {
      return null
    }
    return (
      owners.value.find(
        (item) => item.owner_type === ownerType && item.owner_id === ownerId,
      ) ?? null
    )
  }

  function selectedProfile(ownerType: string, ownerId: string | null): VoiceProfileOut | null {
    const owner = ownerFor(ownerType, ownerId)
    if (!owner || !owner.selected_profile_id) {
      return null
    }
    return owner.profiles.find((item) => item.id === owner.selected_profile_id) ?? null
  }

  async function load(bookId: string, silent = false): Promise<void> {
    const token = ++loadToken
    if (!silent) {
      loading.value = true
    }
    error.value = null
    try {
      const response = await api.getVoiceLibrary(bookId)
      if (token === loadToken) {
        library.value = response
      }
    } catch (err) {
      if (token === loadToken) {
        error.value = errorMessage(err)
        library.value = null
      }
      throw err
    } finally {
      if (token === loadToken) {
        loading.value = false
      }
    }
  }

  async function design(
    bookId: string,
    payload: VoiceDesignPayload,
  ): Promise<VoiceDesignResponse> {
    designing.value = true
    error.value = null
    try {
      const response = await api.designVoices(bookId, payload)
      await load(bookId, true)
      return response
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      designing.value = false
    }
  }

  async function select(voiceId: string): Promise<VoiceProfileOut> {
    const profile = await api.selectVoice(voiceId)
    if (library.value) {
      await load(library.value.book_id, true)
    }
    return profile
  }

  async function update(
    voiceId: string,
    payload: VoiceUpdatePayload,
  ): Promise<VoiceProfileOut> {
    const profile = await api.updateVoice(voiceId, payload)
    if (library.value) {
      await load(library.value.book_id, true)
    }
    return profile
  }

  async function remove(voiceId: string): Promise<void> {
    await api.deleteVoice(voiceId)
    if (library.value) {
      await load(library.value.book_id, true)
    }
  }

  function reset(): void {
    loadToken += 1
    library.value = null
    loading.value = false
    designing.value = false
    error.value = null
  }

  return {
    library,
    loading,
    designing,
    error,
    narrator,
    characters,
    owners,
    selectedOwners,
    ownerFor,
    selectedProfile,
    load,
    design,
    select,
    update,
    remove,
    reset,
  }
})