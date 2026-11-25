import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api/client'
import type { PublicSettings, SystemStatus } from '@/api/types'
import { errorMessage } from '@/utils/errors'

const CACHE_MS = 20_000

export const useSystemStore = defineStore('system', () => {
  const status = ref<SystemStatus | null>(null)
  const settings = ref<PublicSettings | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  let lastLoadedAt = 0
  let inflight: Promise<void> | null = null

  const mockMode = computed<boolean | null>(() => {
    if (status.value) {
      return status.value.mock_mode
    }
    const configured = settings.value?.providers?.is_mock
    return typeof configured === 'boolean' ? configured : null
  })

  const version = computed(() => status.value?.version ?? '0.4.0')
  const providersMode = computed(() => {
    const mode = status.value?.providers_mode ?? settings.value?.providers?.mode
    return typeof mode === 'string' && mode ? mode : 'unknown'
  })
  const overallOk = computed(() => status.value?.overall_ok ?? null)

  async function refresh(force = false): Promise<void> {
    if (inflight) {
      return inflight
    }
    if (!force && status.value && Date.now() - lastLoadedAt < CACHE_MS) {
      return
    }

    loading.value = true
    error.value = null
    inflight = Promise.all([api.getSystemStatus(), api.getPublicSettings()])
      .then(([statusResponse, settingsResponse]) => {
        status.value = statusResponse
        settings.value = settingsResponse
        lastLoadedAt = Date.now()
      })
      .catch((err: unknown) => {
        error.value = errorMessage(err)
      })
      .finally(() => {
        loading.value = false
        inflight = null
      })

    return inflight
  }

  return {
    status,
    settings,
    loading,
    error,
    mockMode,
    version,
    providersMode,
    overallOk,
    refresh,
  }
})