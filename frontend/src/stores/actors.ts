import { ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type {
  VoiceActorCreatePayload,
  VoiceActorOut,
  VoiceActorUpdatePayload,
} from '@/api/types'
import { errorMessage } from '@/utils/errors'

export interface ActorQuery {
  q?: string
  favorite?: boolean
  status?: string
  limit?: number
  offset?: number
}

export const useActorStore = defineStore('actors', () => {
  const items = ref<VoiceActorOut[]>([])
  const total = ref(0)
  const loading = ref(false)
  const creating = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)
  const query = ref<ActorQuery>({ limit: 100, offset: 0 })

  let loadToken = 0

  async function load(overrides: ActorQuery = {}): Promise<void> {
    const token = ++loadToken
    const next = { ...query.value, ...overrides }
    query.value = next
    loading.value = true
    error.value = null
    try {
      const response = await api.listVoiceActors({
        q: next.q?.trim() || undefined,
        favorite: next.favorite,
        status: next.status,
        limit: next.limit ?? 100,
        offset: next.offset ?? 0,
      })
      if (token !== loadToken) {
        return
      }
      items.value = response.items
      total.value = response.total
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

  async function create(payload: VoiceActorCreatePayload): Promise<VoiceActorOut> {
    creating.value = true
    error.value = null
    try {
      const actor = await api.createVoiceActor(payload)
      await load()
      return actor
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      creating.value = false
    }
  }

  async function update(
    actorId: string,
    payload: VoiceActorUpdatePayload,
  ): Promise<VoiceActorOut> {
    saving.value = true
    error.value = null
    try {
      const actor = await api.updateVoiceActor(actorId, payload)
      const index = items.value.findIndex((item) => item.id === actor.id)
      if (index >= 0 && actor.status !== 'archived') {
        items.value[index] = actor
      } else {
        await load()
      }
      return actor
    } catch (err) {
      error.value = errorMessage(err)
      throw err
    } finally {
      saving.value = false
    }
  }

  async function toggleFavorite(actor: VoiceActorOut): Promise<VoiceActorOut> {
    return update(actor.id, { is_favorite: !actor.is_favorite })
  }

  function reset(): void {
    loadToken += 1
    items.value = []
    total.value = 0
    loading.value = false
    creating.value = false
    saving.value = false
    error.value = null
    query.value = { limit: 100, offset: 0 }
  }

  return {
    items,
    total,
    loading,
    creating,
    saving,
    error,
    query,
    load,
    create,
    update,
    toggleFavorite,
    reset,
  }
})