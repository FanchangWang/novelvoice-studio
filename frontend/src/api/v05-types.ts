/** v0.5.0 P2–P6 API 类型。 */

import type { AnalysisJob, RelationshipOut } from './types'

/* ------------------------------ P2：角色圣经 ------------------------------ */

export interface BibleAliasOut {
  name: string
  confidence: number
  basis: string
  chapter_id?: string | null
  evidence?: Array<Record<string, unknown>>
  source?: string
}

export interface CharacterArcOut {
  id: string
  arc_index: number
  label: string
  age_stage: string
  chapter_start_id: string | null
  chapter_end_id: string | null
  story_time_start: string | null
  story_time_end: string | null
  story_order_start: number | null
  story_order_end: number | null
  summary: string | null
  traits: string[]
  appearance: Record<string, unknown>
  voice_traits: Record<string, unknown>
  confidence: number
  evidence: Array<Record<string, unknown>>
  source: string
  created_at: string
  updated_at: string
}

export interface BibleCharacterOut {
  id: string
  canonical_name: string
  role_tier: string
  gender: string
  age_range: string
  character_type: string
  identity_description: string | null
  personality: string[]
  appearance_desc: string | null
  voice_description: string | null
  aliases: BibleAliasOut[]
  confidence: number
  is_provisional: boolean
  user_locked: boolean
  arcs: CharacterArcOut[]
  evidence: Array<Record<string, unknown>>
}

export interface TimelineChapterOut {
  chapter_id: string
  sequence: number
  title: string
  summary: string | null
  story_time_label: string | null
  story_order: number | null
  time_mode: string
  confidence: number
  characters: string[]
}

export interface BibleTimelineOut {
  narrative_mode: string
  chapters: TimelineChapterOut[]
  notes: string[]
}

export interface BibleReviewItemOut {
  id: string
  entity_type: string
  entity_id: string | null
  code: string
  severity: string
  title: string
  message: string | null
  payload: Record<string, unknown>
  status: string
  source: string
  created_at: string
  updated_at: string
}

export interface BibleOut {
  id: string
  book_id: string
  version: number
  status: string
  source: string
  prompt_version: string | null
  model: string | null
  summary: string | null
  confidence: number
  is_active: boolean
  approved_at: string | null
  stats: Record<string, unknown>
  warnings: string[]
  characters: BibleCharacterOut[]
  relationships: RelationshipOut[]
  timeline: BibleTimelineOut
  review_items: BibleReviewItemOut[]
  created_at: string
  updated_at: string
}

export interface BibleStatusResponse {
  book_id: string
  bible_status: string
  open_reviews: number
  bible_id: string | null
  version: number | null
  job: AnalysisJob | null
}

export interface BibleStateResponse extends BibleStatusResponse {
  bible: BibleOut | null
}

/* ------------------------------ P3：分场 ------------------------------ */

export interface SceneCharacterOut {
  id: string
  book_id: string
  scene_id: string
  character_id: string
  character_name: string | null
  state_phase: number
  presence: string
  age_stage: string
  physical_state: string | null
  emotional_state: string | null
  relationship_state: string | null
  knowledge_state: string | null
  disguise_state: string | null
  voice_note: string | null
  state_tags: string[]
  confidence: number
  evidence: Array<Record<string, unknown>>
  source: string
  created_at: string
  updated_at: string
}

export interface SceneOut {
  id: string
  book_id: string
  chapter_id: string
  analysis_version: number
  scene_index: number
  title: string | null
  summary: string | null
  location: string | null
  story_time_label: string | null
  story_order: number | null
  time_mode: string
  pov_character_id: string | null
  pov_character_name: string | null
  mood: string | null
  source_start_offset: number
  source_end_offset: number
  analysis_status: string
  confidence: number
  evidence: Array<Record<string, unknown>>
  meta: Record<string, unknown>
  source: string
  characters: SceneCharacterOut[]
  created_at: string
  updated_at: string
}

export interface SceneReviewItemOut {
  id: string
  entity_type: string
  entity_id: string | null
  code: string
  severity: string
  title: string
  message: string | null
  payload: Record<string, unknown>
  status: string
  source: string
  created_at: string
  updated_at: string
}

export interface SceneStatusResponse {
  chapter_id: string
  book_id: string
  scene_status: string
  scene_version: number
  open_reviews: number
  job: AnalysisJob | null
}

export interface SceneStateResponse extends SceneStatusResponse {
  scenes: SceneOut[]
  review_items: SceneReviewItemOut[]
}

/* ------------------------------ P4：剧本 ------------------------------ */

export interface ScriptVersionOut {
  id: string
  book_id: string
  chapter_id: string
  version: number
  status: string
  adaptation_level: string
  source_scene_version: number
  source_hash: string | null
  prompt_version: string | null
  model: string | null
  summary: Record<string, unknown>
  is_active: boolean
  approved_at: string | null
  unit_count: number
  created_at: string
  updated_at: string
}

export interface ScriptUnitOut {
  id: string
  book_id: string
  chapter_id: string
  script_version_id: string
  scene_id: string | null
  unit_index: number
  unit_type: string
  speaker_type: string
  speaker_id: string | null
  speaker_name: string | null
  character_state_id: string | null
  voice_cast_id: string | null
  voice_actor_id: string | null
  voice_actor_name: string | null
  source_text: string
  text: string
  display_text: string | null
  source_start_offset: number
  source_end_offset: number
  emotion: string
  intensity: number
  delivery: string
  tts_instruction: string | null
  speed: number
  pause_before_ms: number
  pause_after_ms: number
  sfx: Record<string, unknown>
  review_status: string
  confidence: number
  audio_status: string
  current_audio_asset_id: string | null
  render_hash: string | null
  user_locked_fields: string[]
  evidence: Array<Record<string, unknown>>
  source: string
  created_at: string
  updated_at: string
}

export interface ScriptReviewItemOut {
  id: string
  entity_type: string
  entity_id: string | null
  code: string
  severity: string
  title: string
  message: string | null
  payload: Record<string, unknown>
  status: string
  source: string
  created_at: string
  updated_at: string
}

export interface ScriptStatusResponse {
  chapter_id: string
  book_id: string
  script_status: string
  script_version: number
  adaptation_level: string | null
  open_reviews: number
  job: AnalysisJob | null
}

export interface ScriptStateResponse extends ScriptStatusResponse {
  version: ScriptVersionOut | null
  units: ScriptUnitOut[]
  review_items: ScriptReviewItemOut[]
}

export interface ScriptVersionListResponse {
  chapter_id: string
  items: ScriptVersionOut[]
  total: number
}

/* ------------------------------ P7：单元音频 ------------------------------ */

export interface UnitAudioAssetOut {
  id: string
  script_unit_id: string
  voice_actor_id: string | null
  voice_actor_name: string | null
  version: number
  format: string
  sample_rate: number
  channels: number
  sample_width_bits: number
  duration_ms: number
  file_size_bytes: number
  tts_provider: string | null
  tts_model: string | null
  is_valid: boolean
  is_current: boolean
  audio_url: string
  created_at: string
}

export interface ScriptUnitAudioOut {
  unit_id: string
  audio_status: string
  voice_actor_id: string | null
  voice_actor_name: string | null
  render_hash: string | null
  asset: UnitAudioAssetOut | null
}

export interface UnitAudioVersionListResponse {
  unit_id: string
  current_asset_id: string | null
  items: UnitAudioAssetOut[]
  total: number
}

/* ------------------------------ P5：配音员与选角 ------------------------------ */

export interface VoiceActorUsage {
  active_cast_count: number
  book_count: number
  last_used_at: string | null
}

export interface VoiceActorOut {
  id: string
  display_name: string
  description: string | null
  gender_presentation: string
  age_impression: string
  language: string
  timbre_tags: string[]
  personality_tags: string[]
  reference_text: string | null
  instruct_text: string | null
  seed: number | null
  provider: string | null
  model: string | null
  generation_params: Record<string, unknown>
  sample_rate: number | null
  duration_ms: number | null
  status: string
  source: string
  source_book_id: string | null
  is_favorite: boolean
  is_mock: boolean
  audio_url: string | null
  usage: VoiceActorUsage
  created_at: string
  updated_at: string
}

export interface VoiceActorListResponse {
  items: VoiceActorOut[]
  total: number
  limit: number
  offset: number
}

export interface VoiceActorCreatePayload {
  display_name: string
  description: string
  gender_presentation?: string
  age_impression?: string
  language?: string
  timbre_tags?: string[]
  personality_tags?: string[]
  reference_text?: string | null
  seed?: number | null
  is_favorite?: boolean
  source_book_id?: string | null
}

export interface VoiceActorUpdatePayload {
  display_name?: string
  description?: string
  gender_presentation?: string
  age_impression?: string
  timbre_tags?: string[]
  personality_tags?: string[]
  is_favorite?: boolean
  status?: 'ready' | 'archived'
}

export interface CastActorOut {
  id: string
  display_name: string
  gender_presentation: string
  age_impression: string
  language: string
  timbre_tags: string[]
  personality_tags: string[]
  is_favorite: boolean
  is_mock: boolean
  audio_url: string | null
}

export interface VoiceCastOut {
  id: string
  book_id: string
  character_id: string | null
  character_name: string | null
  voice_actor_id: string
  scope: string
  age_stage_condition: string | null
  state_condition: string | null
  priority: number
  source: string
  confidence: number
  is_active: boolean
  reason: string | null
  actor: CastActorOut | null
  created_at: string
  updated_at: string
}

export interface CastStagePlanOut {
  age_stage: string
  label: string
  cast: VoiceCastOut | null
}

export interface CastCharacterPlanOut {
  character_id: string
  name: string
  gender: string
  age_range: string
  role_tier: string
  cast_required: boolean
  base_cast: VoiceCastOut | null
  stages: CastStagePlanOut[]
  missing: boolean
}

export interface BookCastsResponse {
  book_id: string
  bible_ready: boolean
  narrator: VoiceCastOut | null
  characters: CastCharacterPlanOut[]
  cast_count: number
  missing_roles: string[]
  open_reviews: number
  ready: boolean
  job: AnalysisJob | null
}

export interface CastingStatusResponse {
  book_id: string
  production_status: string
  running: boolean
  cast_count: number
  missing_roles: string[]
  open_reviews: number
  job: AnalysisJob | null
}

export interface CastAssignPayload {
  scope: 'narrator' | 'character' | 'character_stage'
  voice_actor_id: string
  character_id?: string | null
  age_stage_condition?: string | null
  reason?: string | null
}

/* ------------------------------ P6：制作台与收件箱 ------------------------------ */

export interface ProductionChapterOut {
  id: string
  title: string
  sequence: number
  char_count: number
  scene_status: string
  scene_version: number
  scene_count: number
  script_status: string
  script_version: number
  unit_count: number
  tts_unit_count: number
  cue_count: number
  audio_ready_units: number
  audio_stale_units: number
  review_count: number
  summary: string | null
}

export interface ProductionOverviewOut {
  book_id: string
  title: string
  production_status: string
  derived_status: string
  bible_status: string
  bible_version: number | null
  bible_ready: boolean
  bible_summary: string | null
  character_count: number
  arc_count: number
  chapter_count: number
  chapters_with_scenes: number
  chapters_with_scripts: number
  scene_count: number
  script_unit_count: number
  tts_unit_count: number
  cue_count: number
  audio_ready_units: number
  audio_stale_units: number
  cast_count: number
  narrator_ready: boolean
  open_reviews: number
  blocking_reviews: number
  warning_reviews: number
  info_reviews: number
  next_action: string | null
  job: AnalysisJob | null
  chapters: ProductionChapterOut[]
}

export interface ReviewItemOut {
  id: string
  book_id: string
  book_title: string | null
  entity_type: string
  entity_id: string | null
  code: string
  severity: string
  title: string
  message: string | null
  payload: Record<string, unknown>
  status: string
  source: string
  resolution_note: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
}

export interface ReviewCountsOut {
  open: number
  blocking: number
  warning: number
  info: number
  resolved: number
  ignored: number
}

export interface ReviewListResponse {
  items: ReviewItemOut[]
  total: number
  limit: number
  offset: number
  counts: ReviewCountsOut
}

export interface ReviewResolvePayload {
  action: 'accept' | 'ignore'
  note?: string | null
}

export interface ReviewBatchResolvePayload extends ReviewResolvePayload {
  review_ids: string[]
}

export interface ReviewBatchResolveResponse {
  action: string
  resolved: number
  ignored: number
  skipped: number
  review_ids: string[]
  book_ids: string[]
  codes: string[]
}