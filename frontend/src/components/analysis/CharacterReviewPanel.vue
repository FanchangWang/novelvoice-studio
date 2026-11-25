<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCheckbox,
  NInput,
  NModal,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  useMessage,
} from 'naive-ui'

import type { CharacterOut, RelationshipOut } from '@/api/types'
import CharacterEditModal from '@/components/analysis/CharacterEditModal.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { useWorkspaceStore } from '@/stores/workspace'
import {
  AGE_LABELS,
  BASIS_LABELS,
  CHARACTER_TYPE_LABELS,
  GENDER_LABELS,
  percent,
} from '@/utils/analysis'
import { errorMessage } from '@/utils/errors'

const props = defineProps<{ bookId: string }>()
const emit = defineEmits<{ (event: 'analyze'): void }>()

const analysis = useAnalysisStore()
const workspace = useWorkspaceStore()
const message = useMessage()

const search = ref('')
const pendingOnly = ref(false)
const mergeMode = ref(false)
const mergeIds = ref<string[]>([])
const editingCharacter = ref<CharacterOut | null>(null)
const showEdit = ref(false)
const showMerge = ref(false)
const targetId = ref<string | null>(null)
const merging = ref(false)
const acceptingAlias = ref<string | null>(null)
const showRelationships = ref(true)
const relationshipFilter = ref('')

const filteredCharacters = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return analysis.activeCharacters.filter((character) => {
    if (pendingOnly.value) {
      const pending =
        character.is_provisional ||
        character.alias_candidates.length > 0 ||
        character.confidence < 0.7
      if (!pending) {
        return false
      }
    }
    if (!keyword) {
      return true
    }
    const haystack = [
      character.canonical_name,
      character.identity_description ?? '',
      ...character.aliases.map((item) => item.name),
      ...character.alias_candidates.map((item) => item.name),
    ]
      .join(' ')
      .toLowerCase()
    return haystack.includes(keyword)
  })
})

const selectedCharacters = computed(() =>
  analysis.activeCharacters.filter((item) => mergeIds.value.includes(item.id)),
)

const targetOptions = computed(() =>
  selectedCharacters.value.map((item) => ({
    label: item.canonical_name,
    value: item.id,
  })),
)

const filteredRelationships = computed(() => {
  const keyword = relationshipFilter.value.trim().toLowerCase()
  if (!keyword) {
    return analysis.relationships
  }
  return analysis.relationships.filter((item) =>
    `${item.source_name ?? ''} ${item.target_name ?? ''} ${item.relationship_type} ${item.description ?? ''}`
      .toLowerCase()
      .includes(keyword),
  )
})

function firstChapterLabel(character: CharacterOut): string {
  if (!character.first_chapter_id) {
    return '—'
  }
  const chapter = workspace.chapters.find((item) => item.id === character.first_chapter_id)
  return chapter ? `第 ${chapter.sequence} 章` : '—'
}

function openEdit(character: CharacterOut): void {
  if (mergeMode.value) {
    toggleMerge(character.id)
    return
  }
  editingCharacter.value = character
  showEdit.value = true
}

function handleSaved(updated: CharacterOut): void {
  if (editingCharacter.value?.id === updated.id) {
    editingCharacter.value = updated
  }
}

function toggleMergeMode(): void {
  mergeMode.value = !mergeMode.value
  mergeIds.value = []
  targetId.value = null
}

function toggleMerge(characterId: string): void {
  mergeIds.value = mergeIds.value.includes(characterId)
    ? mergeIds.value.filter((id) => id !== characterId)
    : [...mergeIds.value, characterId]
  targetId.value = null
}

function openMerge(): void {
  if (mergeIds.value.length < 2) {
    return
  }
  targetId.value = selectedCharacters.value[0]?.id ?? null
  showMerge.value = true
}

async function confirmMerge(): Promise<void> {
  if (!targetId.value) {
    message.warning('请选择合并后的目标角色')
    return
  }
  const sources = mergeIds.value.filter((id) => id !== targetId.value)
  if (!sources.length) {
    message.warning('请至少选择一个被合并角色')
    return
  }
  merging.value = true
  try {
    const updated = await analysis.mergeCharacters(targetId.value, sources)
    message.success(`已合并，更新 ${updated} 个片段`)
    showMerge.value = false
    mergeMode.value = false
    mergeIds.value = []
    targetId.value = null
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    merging.value = false
  }
}

async function acceptCandidate(character: CharacterOut, name: string): Promise<void> {
  acceptingAlias.value = `${character.id}:${name}`
  try {
    await analysis.acceptAlias(character, name)
    message.success(`已接受「${character.canonical_name}」的别名「${name}」`)
  } catch (err) {
    message.error(errorMessage(err))
  } finally {
    acceptingAlias.value = null
  }
}

function speakerNames(item: RelationshipOut): [string, string] {
  return [item.source_name ?? '未知', item.target_name ?? '未知']
}

watch(
  () => props.bookId,
  (bookId) => {
    if (!bookId) {
      return
    }
    void Promise.all([analysis.loadCharacters(bookId), analysis.loadRelationships(bookId)]).catch(
      (err: unknown) => {
        message.error(errorMessage(err))
      },
    )
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col">
    <div class="shrink-0 border-b border-ink-700/70 bg-ink-900/30 px-4 py-3 md:px-5">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div class="flex items-center gap-2">
          <p class="text-[12px] font-medium text-fog-200">角色与关系</p>
          <NTag size="small" :bordered="false">
            {{ analysis.activeCharacters.length }} 角色
          </NTag>
          <NTag v-if="analysis.pendingCharacters" size="small" :bordered="false" type="warning">
            {{ analysis.pendingCharacters }} 待确认
          </NTag>
          <NTag size="small" :bordered="false">
            {{ analysis.relationships.length }} 关系
          </NTag>
        </div>

        <div class="ml-auto flex flex-wrap items-center gap-2">
          <NInput
            v-model:value="search"
            size="small"
            clearable
            placeholder="搜索姓名或别名"
            class="w-40"
          />
          <div class="flex items-center gap-1.5">
            <NSwitch v-model:value="pendingOnly" size="small" />
            <span class="text-[10px] text-fog-400">只看待确认</span>
          </div>
          <NButton
            size="small"
            :type="mergeMode ? 'primary' : 'default'"
            :secondary="!mergeMode"
            @click="toggleMergeMode"
          >
            {{ mergeMode ? '退出合并' : '合并角色' }}
          </NButton>
          <NButton
            size="small"
            quaternary
            :loading="analysis.loadingCharacters"
            @click="analysis.loadCharacters(props.bookId)"
          >
            刷新
          </NButton>
        </div>
      </div>

      <div v-if="mergeMode" class="mt-2.5 flex flex-wrap items-center gap-2 rounded-lg border border-brass-600/30 bg-brass-500/[0.06] px-2.5 py-1.5">
        <span class="text-[10px] text-brass-200">
          已选 {{ mergeIds.length }} 个角色，选择至少两个后合并为一个角色。
        </span>
        <div class="ml-auto flex items-center gap-2">
          <NButton size="tiny" :disabled="mergeIds.length < 2" @click="openMerge">
            合并所选
          </NButton>
          <NButton size="tiny" quaternary @click="toggleMergeMode">取消</NButton>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto">
      <div class="mx-auto w-full max-w-5xl px-4 py-4 md:px-6">
        <NAlert v-if="analysis.error" type="error" :bordered="false" class="mb-3">
          <span class="text-[11px]">{{ analysis.error }}</span>
        </NAlert>

        <div
          v-if="analysis.loadingCharacters && !analysis.characters.length"
          class="flex min-h-[280px] items-center justify-center gap-3 text-xs text-fog-400"
        >
          <NSpin size="small" />
          正在加载角色…
        </div>

        <div
          v-else-if="!analysis.activeCharacters.length"
          class="flex min-h-[320px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 px-6 text-center"
        >
          <p class="font-display text-lg text-fog-200">还没有识别到角色</p>
          <p class="mt-2 max-w-md text-[11px] leading-5 text-fog-500">
            分析章节后会自动提取角色、别名与关系。所有低置信度结果都会保留在这里等待人工确认。
          </p>
          <NButton
            class="mt-4"
            type="primary"
            size="small"
            :loading="analysis.analyzingChapter"
            @click="emit('analyze')"
          >
            分析当前章节
          </NButton>
        </div>

        <div
          v-else-if="!filteredCharacters.length"
          class="flex min-h-[240px] items-center justify-center text-xs text-fog-400"
        >
          没有匹配的角色
        </div>

        <div v-else class="grid gap-3 md:grid-cols-2">
          <article
            v-for="character in filteredCharacters"
            :key="character.id"
            class="cursor-pointer rounded-xl border bg-ink-900/45 p-3.5 transition-colors hover:bg-ink-850/70"
            :class="
              mergeIds.includes(character.id)
                ? 'border-brass-500/60'
                : character.is_provisional || character.alias_candidates.length
                  ? 'border-brass-600/25'
                  : 'border-ink-700/80'
            "
            @click="openEdit(character)"
          >
            <div class="flex items-start gap-2.5">
              <div v-if="mergeMode" class="pt-0.5" @click.stop>
                <NCheckbox
                  :checked="mergeIds.includes(character.id)"
                  @update:checked="toggleMerge(character.id)"
                />
              </div>
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-1.5">
                  <h3 class="font-display text-[15px] text-fog-100">
                    {{ character.canonical_name }}
                  </h3>
                  <NTag size="small" :bordered="false">
                    {{ GENDER_LABELS[character.gender] ?? character.gender }}
                  </NTag>
                  <NTag v-if="character.age_range !== 'unknown'" size="small" :bordered="false">
                    {{ AGE_LABELS[character.age_range] ?? character.age_range }}
                  </NTag>
                  <NTag
                    v-if="character.character_type !== 'unknown'"
                    size="small"
                    :bordered="false"
                    type="info"
                  >
                    {{ CHARACTER_TYPE_LABELS[character.character_type] ?? character.character_type }}
                  </NTag>
                  <NTag v-if="character.user_locked" size="small" :bordered="false" type="success">
                    已锁定
                  </NTag>
                  <NTag v-else-if="character.is_provisional" size="small" :bordered="false" type="warning">
                    暂定
                  </NTag>
                </div>

                <p class="mt-1 line-clamp-2 text-[10px] leading-5 text-fog-500">
                  {{ character.identity_description || '暂无身份描述' }}
                </p>

                <div v-if="character.aliases.length" class="mt-2 flex flex-wrap gap-1">
                  <span
                    v-for="alias in character.aliases.slice(0, 5)"
                    :key="alias.name"
                    class="rounded border border-ink-700 bg-ink-850/70 px-1.5 py-0.5 text-[9px] text-fog-400"
                  >
                    {{ alias.name }}
                  </span>
                  <span v-if="character.aliases.length > 5" class="px-1 py-0.5 text-[9px] text-fog-600">
                    +{{ character.aliases.length - 5 }}
                  </span>
                </div>

                <div
                  v-if="character.alias_candidates.length"
                  class="mt-2 rounded-lg border border-brass-600/25 bg-brass-500/[0.05] px-2 py-1.5"
                  @click.stop
                >
                  <p class="text-[9px] text-brass-300">待确认别名</p>
                  <div
                    v-for="candidate in character.alias_candidates"
                    :key="candidate.name"
                    class="mt-1 flex items-center justify-between gap-2"
                  >
                    <span class="min-w-0 truncate text-[10px] text-fog-300">
                      {{ candidate.name }}
                      <span class="text-fog-600">{{ percent(candidate.confidence) }}</span>
                      <span v-if="candidate.reason" class="text-fog-600"> · {{ candidate.reason }}</span>
                    </span>
                    <NButton
                      size="tiny"
                      :loading="acceptingAlias === `${character.id}:${candidate.name}`"
                      @click="acceptCandidate(character, candidate.name)"
                    >
                      接受
                    </NButton>
                  </div>
                </div>

                <p v-if="character.voice_description" class="mt-2 text-[10px] leading-5 text-lake-400">
                  声音：{{ character.voice_description }}
                </p>

                <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-fog-600">
                  <span>置信度 {{ percent(character.confidence) }}</span>
                  <span>首现 {{ firstChapterLabel(character) }}</span>
                  <span v-if="character.merged_source_ids.length">
                    已并入 {{ character.merged_source_ids.length }} 个角色
                  </span>
                </div>
              </div>
            </div>
          </article>
        </div>

        <section v-if="analysis.relationships.length" class="mt-6">
          <div class="flex flex-wrap items-center gap-2 border-b border-ink-700/70 pb-2">
            <button
              type="button"
              class="nv-focus text-[11px] text-fog-300"
              @click="showRelationships = !showRelationships"
            >
              人物关系 · {{ analysis.relationships.length }}
              <span class="text-fog-600">{{ showRelationships ? '收起' : '展开' }}</span>
            </button>
            <NInput
              v-if="showRelationships"
              v-model:value="relationshipFilter"
              size="tiny"
              clearable
              placeholder="筛选关系"
              class="ml-auto w-36"
            />
          </div>

          <div v-if="showRelationships" class="mt-3 grid gap-2 md:grid-cols-2">
            <div
              v-for="relationship in filteredRelationships"
              :key="relationship.id"
              class="rounded-lg border border-ink-700/80 bg-ink-900/40 px-3 py-2"
            >
              <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-fog-200">
                <span>{{ speakerNames(relationship)[0] }}</span>
                <span class="text-brass-400">{{ relationship.relationship_type }}</span>
                <span>{{ speakerNames(relationship)[1] }}</span>
                <NTag v-if="relationship.is_provisional" size="small" :bordered="false" type="warning">
                  暂定
                </NTag>
                <span class="ml-auto text-[9px] text-fog-600">
                  {{ percent(relationship.confidence) }}
                </span>
              </div>
              <p v-if="relationship.description" class="mt-1 text-[10px] leading-5 text-fog-500">
                {{ relationship.description }}
              </p>
              <p class="mt-0.5 text-[9px] text-fog-600">
                {{ BASIS_LABELS[relationship.is_provisional ? 'inferred' : 'explicit'] }}
              </p>
            </div>
          </div>
        </section>
      </div>
    </div>

    <CharacterEditModal
      v-model:show="showEdit"
      :character="editingCharacter"
      @saved="handleSaved"
    />

    <NModal
      v-model:show="showMerge"
      preset="card"
      title="合并角色"
      :bordered="false"
      :mask-closable="!merging"
      :style="{ width: '520px', maxWidth: 'calc(100vw - 32px)' }"
    >
      <div class="space-y-4">
        <p class="text-xs leading-5 text-fog-400">
          合并后，被合并角色的别名、关系与片段说话人都会转移到目标角色。合并记录可随时拆分恢复。
        </p>

        <label class="block">
          <span class="mb-2 block text-[11px] text-fog-300">合并后的目标角色</span>
          <NSelect
            v-model:value="targetId"
            :options="targetOptions"
            placeholder="选择目标角色"
          />
        </label>

        <div>
          <p class="mb-2 text-[11px] text-fog-300">被合并角色</p>
          <ul class="space-y-1.5">
            <li
              v-for="character in selectedCharacters"
              :key="character.id"
              class="flex items-center justify-between gap-3 rounded-lg border border-ink-700 bg-ink-900/50 px-3 py-2"
            >
              <span class="text-[11px] text-fog-200">{{ character.canonical_name }}</span>
              <NTag v-if="character.id === targetId" size="small" :bordered="false" type="success">
                目标
              </NTag>
              <span v-else class="text-[9px] text-fog-500">并入目标</span>
            </li>
          </ul>
        </div>

        <NAlert v-if="analysis.error" type="error" :bordered="false">
          <span class="text-[11px]">{{ analysis.error }}</span>
        </NAlert>
      </div>

      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <NButton :disabled="merging" @click="showMerge = false">取消</NButton>
          <NButton type="primary" :loading="merging" @click="confirmMerge">确认合并</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>