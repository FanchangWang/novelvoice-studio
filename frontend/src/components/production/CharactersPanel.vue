<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NPagination,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'

import { api } from '@/api/client'
import type {
  BibleCharacterOut,
  BookCastsResponse,
  CastCharacterPlanOut,
  CastStagePlanOut,
  VoiceCastOut,
} from '@/api/types'
import AssignCastModal from '@/components/production/AssignCastModal.vue'
import CharacterVoiceBindings from '@/components/production/CharacterVoiceBindings.vue'
import { useProductionStore } from '@/stores/production'
import { AGE_LABELS, GENDER_LABELS, isJobActive, labelOf, percent } from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'
import { jobStatusLabel, jobTypeLabel, ROLE_TIER_LABELS } from '@/utils/production'

const props = defineProps<{ bookId: string; bibleReady: boolean; focusCharacterId?: string | null }>()
const emit = defineEmits<{ (event: 'changed'): void }>()
const production = useProductionStore()
const dialog = useDialog()
const message = useMessage()

const casts = ref<BookCastsResponse | null>(null)
const characters = ref<BibleCharacterOut[]>([])
const loading = ref(false)
const actionLoading = ref(false)
const error = ref<string | null>(null)
const expanded = ref<Set<string>>(new Set())
const ensemblePage = ref(1)
const ensemblePageSize = 20
const showAssign = ref(false)
const assignScope = ref<'narrator' | 'character' | 'character_stage'>('narrator')
const assignCharacter = ref<CastCharacterPlanOut | null>(null)
const assignStage = ref<CastStagePlanOut | null>(null)
const currentActorId = ref<string | null>(null)
const characterElements = new Map<string, HTMLElement>()
let pollTimer: ReturnType<typeof setTimeout> | null = null
let lastJobActive = false

const job = computed(() => casts.value?.job ?? null)
const jobActive = computed(() => isJobActive(job.value?.status))
const details = computed(() => new Map(characters.value.map((item) => [item.id, item])))
const groups = computed(() => {
  const rows = casts.value?.characters ?? []
  return [
    { key: 'protagonist', title: '主角', rows: rows.filter((item) => item.role_tier === 'protagonist') },
    { key: 'major', title: '重要配角', rows: rows.filter((item) => item.role_tier === 'major') },
    {
      key: 'ensemble',
      title: '群演与其他角色',
      rows: rows.filter((item) => !['protagonist', 'major'].includes(item.role_tier)),
    },
  ].filter((group) => group.rows.length)
})
const ensemblePages = computed(() => Math.max(1, Math.ceil((groups.value.find((group) => group.key === 'ensemble')?.rows.length ?? 0) / ensemblePageSize)))

function groupRows(rows: CastCharacterPlanOut[], key: string): CastCharacterPlanOut[] {
  if (key !== 'ensemble') return rows
  const start = (ensemblePage.value - 1) * ensemblePageSize
  return rows.slice(start, start + ensemblePageSize)
}

function toggle(id: string): void {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

function stopPolling(): void {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

function schedulePoll(): void {
  if (!jobActive.value || pollTimer) return
  pollTimer = setTimeout(async () => {
    pollTimer = null
    await load(true).catch(() => undefined)
    schedulePoll()
  }, 1800)
}

async function load(silent = false): Promise<void> {
  if (!silent) loading.value = true
  error.value = null
  try {
    const [castResult, bibleResult] = await Promise.all([
      api.getBookCasts(props.bookId),
      api.getBible(props.bookId),
    ])
    casts.value = castResult
    characters.value = bibleResult.bible?.characters ?? []
    if (ensemblePage.value > ensemblePages.value) ensemblePage.value = ensemblePages.value
    const active = isJobActive(castResult.job?.status)
    if (active) schedulePoll()
    else {
      stopPolling()
      if (lastJobActive) {
        emit('changed')
        await production.refresh()
      }
    }
    lastJobActive = active
  } catch (err) {
    error.value = errorMessage(err)
    throw err
  } finally {
    if (!silent) loading.value = false
  }
}

async function startCasting(): Promise<void> {
  actionLoading.value = true
  error.value = null
  try {
    await api.startCasting(props.bookId)
    message.success('已开始角色整理与自动选角')
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
    message.warning('请先完成全书分析')
    return
  }
  if (!casts.value?.cast_count) {
    void startCasting()
    return
  }
  dialog.warning({
    title: '重新自动选角',
    content: '会补齐缺失的角色声线，保留已有绑定和手动指定。通用声线优先，没有适配项时才生成本书专属声线。',
    positiveText: '继续',
    negativeText: '取消',
    onPositiveClick: () => startCasting(),
  })
}

async function cancelCasting(): Promise<void> {
  actionLoading.value = true
  try {
    await api.cancelCasting(props.bookId)
    message.success('已请求取消，已分配的声音会保留')
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
  if (!cast?.actor) return ''
  return `${labelOf(GENDER_LABELS, cast.actor.gender_presentation)} · ${labelOf(AGE_LABELS, cast.actor.age_impression)}`
}

function baseVoice(character: CastCharacterPlanOut): string {
  if (character.base_cast?.actor) return character.base_cast.actor.display_name
  return character.cast_required ? '待分配' : '无需单独配音'
}

function setCharacterElement(id: string, element: unknown): void {
  if (element instanceof HTMLElement) characterElements.set(id, element)
  else characterElements.delete(id)
}

async function focusCharacter(): Promise<void> {
  const id = props.focusCharacterId
  if (!id || !casts.value) return
  const group = groups.value.find((item) => item.rows.some((row) => row.character_id === id))
  if (!group) return
  if (group.key === 'ensemble') {
    const index = group.rows.findIndex((row) => row.character_id === id)
    ensemblePage.value = Math.floor(index / ensemblePageSize) + 1
  }
  expanded.value = new Set([...expanded.value, id])
  await nextTick()
  characterElements.get(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

watch(
  () => [props.focusCharacterId, groups.value],
  () => void focusCharacter(),
  { immediate: true },
)

onMounted(() => { void load().catch(() => undefined) })
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="space-y-4">
    <section class="nv-card rounded-xl px-5 py-4">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="eyebrow">CHARACTERS / 角色与声音</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <h2 class="font-display text-xl text-fog-100">角色分组与配音分配</h2>
            <NTag v-if="casts" size="small" :bordered="false" :type="casts.ready ? 'success' : 'default'">
              {{ casts.ready ? '已就绪' : '待补齐' }}
            </NTag>
          </div>
          <p class="mt-2 max-w-2xl text-[11px] leading-6 text-fog-500">
            主角、重要配角与群演分别展示。展开角色可查看简介、经历和年龄阶段声线；一个角色始终只占一行。
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <NButton size="small" quaternary :loading="loading" @click="load()">刷新</NButton>
          <NButton v-if="jobActive" size="small" secondary :loading="actionLoading" @click="cancelCasting">取消</NButton>
          <NButton v-else size="small" type="primary" :disabled="!bibleReady" :loading="actionLoading" @click="confirmAutoCast">
            {{ casts?.cast_count ? '补齐自动选角' : '自动选角' }}
          </NButton>
        </div>
      </div>
      <p v-if="!bibleReady" class="mt-3 rounded-lg border border-brass-600/25 bg-brass-500/[0.05] px-3 py-2 text-[10px] text-brass-300">先完成全书分析，系统才能整理角色并分配声音。</p>
      <div v-if="job" class="mt-3 rounded-lg border border-ink-700 bg-ink-900/50 px-3.5 py-2.5">
        <div class="flex flex-wrap items-center gap-3 text-[10px]">
          <span class="h-1.5 w-1.5 rounded-full" :class="jobActive ? 'animate-pulse bg-brass-400' : job.status === 'failed' ? 'bg-rust-500' : 'bg-moss-400'" />
          <span class="text-fog-300">{{ jobTypeLabel(job.job_type) }} · {{ jobStatusLabel(job.status) }} · {{ Math.round((job.progress || 0) * 100) }}%</span>
          <span class="font-mono text-fog-500">{{ job.completed_items }} / {{ job.total_items }}</span>
          <span v-if="job.error_message" class="text-rust-400">{{ job.error_message }}</span>
        </div>
      </div>
      <NAlert v-if="error" type="error" :bordered="false" class="mt-3">{{ error }}</NAlert>
    </section>

    <div v-if="loading && !casts" class="flex justify-center py-20"><NSpin size="small" class="mr-2" />正在读取角色…</div>
    <div v-else-if="!casts || (!casts.narrator && !casts.characters.length)" class="rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-8 py-16 text-center">
      <h3 class="font-display text-lg text-fog-200">还没有角色资料</h3>
      <p class="mt-2 text-[11px] text-fog-500">全书分析完成后，角色会按层级出现在这里。</p>
      <NButton class="mt-4" type="primary" :disabled="!bibleReady" :loading="actionLoading" @click="startCasting">整理角色并选角</NButton>
    </div>

    <template v-else>
      <section class="nv-card rounded-xl px-4 py-3.5">
        <div class="flex flex-wrap items-start gap-3">
          <div class="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-brass-600/50 bg-brass-500/10 font-display text-base text-brass-300">旁</div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="font-display text-base text-fog-100">全书旁白</h3>
              <NTag v-if="casts.narrator?.actor" size="tiny" :bordered="false" :type="casts.narrator.actor.scope === 'general' ? 'info' : 'warning'">
                {{ casts.narrator.actor.scope === 'general' ? '通用声线' : '本书专属' }}
              </NTag>
            </div>
            <p v-if="casts.narrator?.actor" class="mt-1 text-[12px] text-fog-200">
              {{ casts.narrator.actor.display_name }} <span class="ml-2 text-[10px] text-fog-500">{{ castMeta(casts.narrator) }}</span>
            </p>
            <p v-else class="mt-1 text-[10px] text-fog-500">旁白尚未分配声音。</p>
            <audio v-if="casts.narrator?.actor?.audio_url" :src="casts.narrator.actor.audio_url" controls preload="none" class="mt-1.5 h-8 w-full max-w-md" aria-label="全书旁白试听" />
          </div>
          <NButton size="small" secondary @click="openNarrator">{{ casts.narrator ? '更换旁白' : '选择旁白' }}</NButton>
        </div>
      </section>

      <NAlert v-if="casts.missing_roles.length" type="warning" :bordered="false">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-[11px]">仍缺少声音：{{ casts.missing_roles.join('、') }}</span>
          <NButton size="tiny" quaternary class="ml-auto" :loading="actionLoading" @click="startCasting">继续补齐</NButton>
        </div>
      </NAlert>

      <section v-for="group in groups" :key="group.key">
        <div class="flex items-center justify-between gap-3">
          <h3 class="font-display text-base text-fog-100">{{ group.title }} <span class="ml-1 font-mono text-[11px] text-fog-500">{{ group.rows.length }}</span></h3>
          <span v-if="group.key === 'ensemble' && group.rows.length > ensemblePageSize" class="text-[10px] text-fog-500">每页 {{ ensemblePageSize }} 位</span>
        </div>
        <div class="mt-2 space-y-2">
          <article
            v-for="character in groupRows(group.rows, group.key)"
            :key="character.character_id"
            :ref="(element) => setCharacterElement(character.character_id, element)"
            class="nv-card scroll-mt-24 overflow-hidden rounded-xl"
            :class="focusCharacterId === character.character_id ? 'ring-1 ring-brass-400/60' : ''"
          >
            <div class="flex flex-wrap items-center gap-3 px-4 py-3">
              <button type="button" class="nv-focus grid h-8 w-8 shrink-0 place-items-center rounded-full border border-ink-600 bg-ink-800 font-display text-xs text-fog-200" :aria-expanded="expanded.has(character.character_id)" :aria-label="`${expanded.has(character.character_id) ? '收起' : '展开'}${character.name}`" @click="toggle(character.character_id)">
                {{ expanded.has(character.character_id) ? '−' : '+' }}
              </button>
              <button type="button" class="nv-focus min-w-0 flex-1 text-left" @click="toggle(character.character_id)">
                <span class="flex flex-wrap items-center gap-1.5">
                  <span class="font-display text-sm text-fog-100">{{ character.name }}</span>
                  <NTag size="tiny" :bordered="false">{{ ROLE_TIER_LABELS[character.role_tier] ?? '其他' }}</NTag>
                  <span class="text-[10px] text-fog-500">{{ labelOf(GENDER_LABELS, character.gender) }} · {{ labelOf(AGE_LABELS, character.age_range) }}</span>
                  <NTag v-if="character.missing" size="tiny" :bordered="false" type="warning">缺少声音</NTag>
                </span>
                <span class="mt-1 block truncate text-[10px] text-fog-400">
                  基础声线：{{ baseVoice(character) }}
                  <template v-if="character.stages.length"> · {{ character.stages.length }} 个年龄阶段</template>
                </span>
              </button>
              <NButton size="tiny" quaternary @click="openCharacter(character)">{{ character.base_cast ? '更换基础' : '选择基础' }}</NButton>
            </div>
            <div v-if="expanded.has(character.character_id)" class="border-t border-ink-700/70 bg-ink-900/30 px-4 py-3">
              <div v-if="details.get(character.character_id)" class="grid gap-4 lg:grid-cols-2">
                <div>
                  <p class="text-[10px] text-fog-500">角色简介</p>
                  <p class="mt-1 text-[11px] leading-6 text-fog-300">{{ details.get(character.character_id)?.identity_description || '暂无角色简介。' }}</p>
                  <p v-if="details.get(character.character_id)?.voice_description" class="mt-2 text-[10px] leading-5 text-fog-500">声音特征：{{ details.get(character.character_id)?.voice_description }}</p>
                  <div v-if="details.get(character.character_id)?.personality.length" class="mt-2 flex flex-wrap gap-1">
                    <span v-for="tag in details.get(character.character_id)?.personality" :key="tag" class="rounded bg-ink-800 px-1.5 py-0.5 text-[9px] text-fog-400">{{ tag }}</span>
                  </div>
                  <p v-if="details.get(character.character_id)?.aliases.length" class="mt-2 text-[9px] text-fog-600">别名：{{ details.get(character.character_id)?.aliases.map((alias) => alias.name).join('、') }}</p>
                </div>
                <div>
                  <p class="text-[10px] text-fog-500">基本经历</p>
                  <ol v-if="details.get(character.character_id)?.arcs.length" class="mt-1 space-y-2">
                    <li v-for="arc in details.get(character.character_id)?.arcs" :key="arc.id" class="flex gap-2">
                      <span class="mt-0.5 font-mono text-[9px] text-brass-400">{{ String(arc.arc_index + 1).padStart(2, '0') }}</span>
                      <div><p class="text-[10px] text-fog-300">{{ arc.label || `${labelOf(AGE_LABELS, arc.age_stage)}阶段` }}</p><p v-if="arc.summary" class="mt-0.5 text-[10px] leading-5 text-fog-500">{{ arc.summary }}</p></div>
                    </li>
                  </ol>
                  <p v-else class="mt-1 text-[10px] text-fog-600">暂无经历摘要。</p>
                </div>
              </div>
              <CharacterVoiceBindings :character="character" editable @assign-base="openCharacter(character)" @assign-stage="openStage(character, $event)" />
            </div>
          </article>
        </div>
        <div v-if="group.key === 'ensemble' && group.rows.length > ensemblePageSize" class="mt-3 flex justify-center">
          <NPagination v-model:page="ensemblePage" :page-count="ensemblePages" :page-size="ensemblePageSize" size="small" show-quick-jumper />
        </div>
      </section>
      <p class="pb-2 text-[10px] leading-5 text-fog-600">自动选角优先复用通用声线；特殊角色才生成本书专属声线。专属声线不会分配给其他书籍。</p>
    </template>

    <AssignCastModal v-model:show="showAssign" :book-id="bookId" :scope="assignScope" :character="assignCharacter" :stage="assignStage" :current-actor-id="currentActorId" @assigned="onAssigned" />
  </div>
</template>
