<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type {
  BookCastsResponse,
  CastCharacterPlanOut,
  CastStagePlanOut,
  VoiceCastOut,
} from '@/api/types'
import AssignCastModal from '@/components/production/AssignCastModal.vue'
import { useProductionStore } from '@/stores/production'
import { AGE_LABELS, GENDER_LABELS, isJobActive, labelOf, percent } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { ROLE_TIER_LABELS, statusTagType } from '@/utils/production'

const props = defineProps<{
  bookId: string
  bibleReady: boolean
}>()

const emit = defineEmits<{
  (event: 'changed'): void
  (event: 'open-reviews'): void
}>()

const production = useProductionStore()
const dialog = useDialog()
const message = useMessage()

const casts = ref<BookCastsResponse | null>(null)
const loading = ref(false)
const actionLoading = ref(false)
const error = ref<string | null>(null)

const showAssign = ref(false)
const assignScope = ref<'narrator' | 'character' | 'character_stage'>('narrator')
const assignCharacter = ref<CastCharacterPlanOut | null>(null)
const assignStage = ref<CastStagePlanOut | null>(null)
const currentActorId = ref<string | null>(null)

let pollTimer: ReturnType<typeof setTimeout> | null = null
let lastJobActive = false

const job = computed(() => casts.value?.job ?? null)
const jobActive = computed(() => isJobActive(job.value?.status))
const missingRoles = computed(() => casts.value?.missing_roles ?? [])

function stopPolling(): void {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll(): void {
  if (!jobActive.value || pollTimer) {
    return
  }
  pollTimer = setTimeout(async () => {
    pollTimer = null
    await load(true).catch(() => undefined)
    schedulePoll()
  }, 1800)
}

async function load(silent = false): Promise<void> {
  if (!silent) {
    loading.value = true
  }
  error.value = null
  try {
    const response = await api.getBookCasts(props.bookId)
    casts.value = response
    const active = isJobActive(response.job?.status)
    if (active) {
      schedulePoll()
    } else {
      stopPolling()
      if (lastJobActive) {
        emit('changed')
        await production.refresh()
      } else if (response.job?.status === 'failed' && response.job.error_message) {
        error.value = response.job.error_message
      }
    }
    lastJobActive = active
  } catch (err) {
    error.value = errorMessage(err)
    throw err
  } finally {
    loading.value = false
  }
}

async function startCasting(): Promise<void> {
  actionLoading.value = true
  error.value = null
  try {
    await api.startCasting(props.bookId)
    message.success('已开始自动选角')
    await load(true)
    await production.refresh()
  } catch (err) {
    error.value = errorMessage(err)
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function confirmAutoCast(): void {
  if (!props.bibleReady) {
    message.warning('请先生成并确认设定集')
    return
  }
  if (!casts.value?.cast_count) {
    void startCasting()
    return
  }
  dialog.warning({
    title: '重新自动选角',
    content:
      '自动选角会替换当前由 AI 生成的绑定，并保留手动指定的选角。继续后会重新计算缺失角色。',
    positiveText: '重新选角',
    negativeText: '取消',
    onPositiveClick: () => startCasting(),
  })
}

async function cancelCasting(): Promise<void> {
  actionLoading.value = true
  try {
    await api.cancelCasting(props.bookId)
    message.success('已请求取消选角任务')
    await load(true)
    await production.refresh()
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    actionLoading.value = false
  }
}

function openNarrator(): void {
  assignScope.value = 'narrator'
  assignCharacter.value = null
  assignStage.value = null
  currentActorId.value = casts.value?.narrator?.actor?.id ?? null
  showAssign.value = true
}

function openCharacter(character: CastCharacterPlanOut): void {
  assignScope.value = 'character'
  assignCharacter.value = character
  assignStage.value = null
  currentActorId.value = character.base_cast?.actor?.id ?? null
  showAssign.value = true
}

function openStage(character: CastCharacterPlanOut, stage: CastStagePlanOut): void {
  assignScope.value = 'character_stage'
  assignCharacter.value = character
  assignStage.value = stage
  currentActorId.value = stage.cast?.actor?.id ?? null
  showAssign.value = true
}

function onAssigned(response: BookCastsResponse): void {
  casts.value = response
  void production.refresh()
  emit('changed')
}

function castMeta(cast: VoiceCastOut | null): string {
  if (!cast?.actor) {
    return ''
  }
  return `${labelOf(GENDER_LABELS, cast.actor.gender_presentation)} · ${labelOf(
    AGE_LABELS,
    cast.actor.age_impression,
  )}`
}

onMounted(() => {
  void load().catch(() => undefined)
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="space-y-4">
    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="eyebrow">CASTING / 选角</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <h2 class="font-display text-xl text-fog-100">旁白与角色配音</h2>
            <NTag v-if="casts" size="small" :bordered="false" :type="statusTagType(casts.ready ? 'completed' : 'not_started')">
              {{ casts.ready ? '选角已就绪' : '尚未完成' }}
            </NTag>
            <NTag v-if="casts" size="small" :bordered="false">
              {{ casts.cast_count }} 个绑定
            </NTag>
            <NTag v-if="casts?.open_reviews" size="small" :bordered="false" type="warning">
              异常 {{ casts.open_reviews }}
            </NTag>
          </div>
          <p class="mt-2 max-w-2xl text-[11px] leading-5 text-fog-500">
            从全局配音员库为旁白和角色绑定声音。角色跨年龄阶段时，可以为基础形态和特定阶段分别选角。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <NButton size="small" quaternary :loading="loading" @click="load()">刷新</NButton>
          <NButton
            v-if="jobActive"
            size="small"
            secondary
            :loading="actionLoading"
            @click="cancelCasting"
          >
            取消
          </NButton>
          <NButton
            v-else
            size="small"
            type="primary"
            :disabled="!bibleReady"
            :loading="actionLoading"
            @click="confirmAutoCast"
          >
            {{ casts?.cast_count ? '重新自动选角' : '自动选角' }}
          </NButton>
        </div>
      </div>

      <p v-if="!bibleReady" class="mt-3 rounded-lg border border-brass-600/25 bg-brass-500/[0.05] px-3 py-2 text-[10px] text-brass-300">
        先完成并确认全书设定集，才能自动选角。
      </p>

      <div v-if="job" class="mt-3 rounded-lg border border-ink-700 bg-ink-900/50 px-3.5 py-2.5">
        <div class="flex flex-wrap items-center gap-3">
          <span
            class="h-1.5 w-1.5 rounded-full"
            :class="jobActive ? 'animate-pulse bg-brass-400' : job.status === 'failed' ? 'bg-rust-500' : 'bg-moss-400'"
          />
          <span class="text-[11px] text-fog-300">
            {{ job.job_type }} · {{ job.status }} · {{ Math.round((job.progress || 0) * 100) }}%
          </span>
          <span class="text-[10px] text-fog-500">{{ job.completed_items }} / {{ job.total_items }}</span>
          <span v-if="job.error_message" class="text-[10px] text-rust-400">{{ job.error_message }}</span>
        </div>
        <div v-if="jobActive" class="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-700">
          <div
            class="h-full rounded-full bg-brass-400 transition-all"
            :style="{ width: `${Math.round((job.progress || 0) * 100)}%` }"
          />
        </div>
      </div>

      <NAlert v-if="error" type="error" :bordered="false" class="mt-3">{{ error }}</NAlert>
    </section>

    <div v-if="loading && !casts" class="flex items-center justify-center py-24 text-xs text-fog-400">
      <NSpin size="small" class="mr-3" />
      正在读取选角…
    </div>

    <div
      v-else-if="!casts || (!casts.narrator && !casts.characters.length)"
      class="flex min-h-[260px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 text-center"
    >
      <h3 class="font-display text-lg text-fog-200">还没有可以选角的角色</h3>
      <p class="mt-2 max-w-md text-[11px] leading-6 text-fog-500">
        选角依赖已确认的全书设定集。先完成设定集，再回到这里自动或手动绑定声音。
      </p>
      <NButton
        class="mt-4"
        type="primary"
        :disabled="!bibleReady"
        :loading="actionLoading"
        @click="startCasting"
      >
        自动选角
      </NButton>
    </div>

    <template v-else>
      <NAlert v-if="missingRoles.length" type="warning" :bordered="false">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-[11px]">仍缺少选角：{{ missingRoles.join('、') }}</span>
          <NButton size="tiny" quaternary class="ml-auto" @click="emit('open-reviews')">
            查看选角异常
          </NButton>
        </div>
      </NAlert>

      <section class="nv-card rounded-xl px-4 py-3.5">
        <div class="flex flex-wrap items-start gap-3">
          <div class="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-brass-600/50 bg-brass-500/10 font-display text-base text-brass-300">
            旁
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="font-display text-base text-fog-100">全书旁白</h3>
              <NTag v-if="casts.narrator" size="tiny" :bordered="false" type="success">
                已绑定
              </NTag>
              <NTag v-else size="tiny" :bordered="false" type="warning">未绑定</NTag>
            </div>
            <div v-if="casts.narrator?.actor" class="mt-1">
              <p class="text-[12px] text-fog-200">
                {{ casts.narrator.actor.display_name }}
                <span class="ml-2 text-[10px] text-fog-500">{{ castMeta(casts.narrator) }}</span>
              </p>
              <p class="mt-0.5 text-[10px] text-fog-500">
                {{ casts.narrator.reason || '自动选角' }} · 置信度 {{ percent(casts.narrator.confidence) }}
              </p>
              <audio
                v-if="casts.narrator.actor.audio_url"
                :src="casts.narrator.actor.audio_url"
                controls
                preload="none"
                class="mt-1.5 h-8 w-full max-w-md"
              />
            </div>
            <p v-else class="mt-1 text-[10px] text-fog-500">
              旁白未绑定，剧本中的旁白单元无法生成音频。
            </p>
          </div>
          <NButton size="small" secondary @click="openNarrator">
            {{ casts.narrator ? '更换旁白' : '选择旁白' }}
          </NButton>
        </div>
      </section>

      <section>
        <div class="flex items-center justify-between">
          <h3 class="font-display text-base text-fog-100">角色选角</h3>
          <span class="text-[10px] text-fog-500">
            {{ casts.characters.length }} 个角色 · 缺失 {{ missingRoles.length }} 个
          </span>
        </div>

        <div class="mt-2 space-y-2.5">
          <article
            v-for="character in casts.characters"
            :key="character.character_id"
            class="nv-card rounded-xl px-4 py-3.5"
          >
            <div class="flex flex-wrap items-start gap-3">
              <div class="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-ink-600 bg-ink-800 font-display text-sm text-fog-200">
                {{ character.name.slice(0, 1) }}
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <h4 class="font-display text-base text-fog-100">{{ character.name }}</h4>
                  <NTag size="tiny" :bordered="false">
                    {{ ROLE_TIER_LABELS[character.role_tier] ?? character.role_tier }}
                  </NTag>
                  <NTag size="tiny" :bordered="false" type="info">
                    {{ labelOf(GENDER_LABELS, character.gender) }} ·
                    {{ labelOf(AGE_LABELS, character.age_range) }}
                  </NTag>
                  <NTag v-if="character.missing" size="tiny" :bordered="false" type="warning">
                    缺少选角
                  </NTag>
                  <NTag v-else-if="!character.cast_required" size="tiny" :bordered="false">
                    无需单独配音
                  </NTag>
                </div>

                <div class="mt-1.5 flex flex-wrap items-center gap-2 text-[10px]">
                  <template v-if="character.base_cast?.actor">
                    <span class="text-fog-200">{{ character.base_cast.actor.display_name }}</span>
                    <span class="text-fog-500">{{ castMeta(character.base_cast) }}</span>
                    <span class="text-fog-600">基础</span>
                    <span class="font-mono text-[9px] text-fog-600">
                      {{ percent(character.base_cast.confidence) }}
                    </span>
                  </template>
                  <span v-else class="text-fog-500">
                    {{ character.cast_required ? '尚未绑定基础配音员' : '该角色不需要单独配音' }}
                  </span>
                  <NButton
                    v-if="character.cast_required || character.base_cast"
                    size="tiny"
                    quaternary
                    class="ml-auto"
                    @click="openCharacter(character)"
                  >
                    {{ character.base_cast ? '更换基础' : '选择基础' }}
                  </NButton>
                </div>

                <audio
                  v-if="character.base_cast?.actor?.audio_url"
                  :src="character.base_cast.actor.audio_url"
                  controls
                  preload="none"
                  class="mt-1.5 h-8 w-full max-w-md"
                />
              </div>
            </div>

            <div v-if="character.stages.length" class="mt-2.5 space-y-1.5 border-t border-ink-700/70 pt-2.5">
              <p class="eyebrow">阶段选角</p>
              <div
                v-for="stage in character.stages"
                :key="stage.age_stage"
                class="flex flex-wrap items-center gap-2 rounded-lg bg-ink-900/60 px-2.5 py-2 text-[10px]"
              >
                <span class="w-24 shrink-0 text-fog-300">{{ stage.label }}</span>
                <span v-if="stage.cast?.actor" class="text-fog-200">
                  {{ stage.cast.actor.display_name }}
                  <span class="ml-1 text-fog-500">{{ castMeta(stage.cast) }}</span>
                </span>
                <span v-else class="text-fog-500">继承基础选角</span>
                <NButton
                  size="tiny"
                  quaternary
                  class="ml-auto"
                  @click="openStage(character, stage)"
                >
                  {{ stage.cast ? '更换阶段' : '阶段指定' }}
                </NButton>
              </div>
            </div>
          </article>
        </div>
      </section>

      <p class="pb-2 text-[10px] leading-5 text-fog-600">
        自动选角只决定绑定关系，不会生成新的配音员。需要新的声音时，请先在声音库创建配音员。
      </p>
    </template>

    <AssignCastModal
      v-model:show="showAssign"
      :book-id="bookId"
      :scope="assignScope"
      :character="assignCharacter"
      :stage="assignStage"
      :current-actor-id="currentActorId"
      @assigned="onAssigned"
    />
  </div>
</template>