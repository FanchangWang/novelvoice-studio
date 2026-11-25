/** 与后端 Pydantic 响应保持一致的类型定义。 */

export type DuplicatePolicy = 'new' | 'reject' | 'replace'

export interface BookSummary {
  id: string
  title: string
  author: string | null
  description: string | null
  language: string
  status: string
  source_encoding: string | null
  source_char_count: number
  chapter_count: number
  created_at: string
  updated_at: string
}

export interface BookDetail extends BookSummary {
  project_dir: string
  source_file_path: string | null
  import_warnings: string[]
  deleted_at: string | null
}

export interface DuplicateBookInfo {
  id: string
  title: string
  created_at: string
}

export interface EncodingCandidate {
  encoding: string
  label: string
  confidence: number
  reason: string
}

export interface ImportPreview {
  filename: string
  size_bytes: number
  sha256: string
  encoding: string
  encoding_label: string
  encoding_confidence: number
  needs_encoding_confirmation: boolean
  encoding_candidates: EncodingCandidate[]
  sample_text: string
  total_chars: number
  line_count: number
  chapter_count: number
  chapter_titles: string[]
  chapters_truncated: boolean
  warnings: string[]
  duplicate: DuplicateBookInfo | null
}

export interface ImportResult {
  book: BookDetail
  chapters_created: number
  encoding: string
  warnings: string[]
  replaced: boolean
}

export interface BookListResponse {
  items: BookSummary[]
  total: number
}

export interface Paragraph {
  index: number
  start_offset: number
  end_offset: number
  text: string
}

export interface ChapterSummary {
  id: string
  book_id: string
  title: string
  sequence: number
  char_count: number
  source_start_offset: number
  source_end_offset: number
  analysis_status: string
  created_at: string
  updated_at: string
}

export interface ChapterDetail extends ChapterSummary {
  text: string
  paragraphs: Paragraph[]
}

export interface ChapterListResponse {
  items: ChapterSummary[]
  total: number
}

export interface ChapterMutationResponse {
  chapter: ChapterSummary
  affected_chapter_ids: string[]
  items: ChapterSummary[]
}

export interface ComponentStatus {
  id: string
  label: string
  ok: boolean
  critical: boolean
  detail: string
  meta: Record<string, unknown>
}

export interface SystemStatus {
  checked_at: string
  overall_ok: boolean
  mock_mode: boolean
  providers_mode: string
  version: string
  data_dir: string
  components: ComponentStatus[]
}

export type PublicSettingsSection = Record<string, unknown>

export interface PublicSettings {
  app: PublicSettingsSection
  llm: PublicSettingsSection
  voice_design: PublicSettingsSection
  tts: PublicSettingsSection
  jobs: PublicSettingsSection
  export: PublicSettingsSection
  providers: PublicSettingsSection
  read_only: boolean
}

export interface ApiErrorPayload {
  error: {
    code: string
    message: string
    details?: unknown
  }
}

export interface CreateBookPayload {
  title: string
  author?: string | null
  description?: string | null
  language?: string
}

export interface ImportOptions {
  encoding?: string
  title?: string
  author?: string
  description?: string
  language?: string
  customRegex?: string
  duplicatePolicy?: DuplicatePolicy
  replaceBookId?: string
}

export interface PreviewOptions {
  encoding?: string
  customRegex?: string
}

/* ------------------------------ 阶段 2：分析 ------------------------------ */

export type AnalysisStatusValue =
  | 'not_started'
  | 'running'
  | 'completed'
  | 'needs_review'
  | 'failed'
  | string

export type CharacterGender = 'male' | 'female' | 'other' | 'unknown'
export type Basis = 'explicit' | 'inferred' | 'unknown'
export type AgeRange =
  | 'child'
  | 'teenager'
  | 'young_adult'
  | 'adult'
  | 'middle_aged'
  | 'elderly'
  | 'unknown'
export type CharacterType = 'human' | 'animal' | 'non_human' | 'unknown'
export type Emotion =
  | 'neutral'
  | 'happy'
  | 'excited'
  | 'angry'
  | 'sad'
  | 'fearful'
  | 'surprised'
  | 'doubtful'
  | 'sarcastic'
  | 'tender'
  | 'embarrassed'
  | 'determined'
  | 'tired'
export type Delivery =
  | 'normal'
  | 'whispering'
  | 'shouting'
  | 'crying'
  | 'laughing'
  | 'breathless'
  | 'slow'
  | 'hesitant'
export type SegmentType = 'narration' | 'dialogue' | 'inner_monologue' | 'unknown'
export type SpeakerType = 'character' | 'narrator' | 'unknown'
export type ReviewStatus = 'auto' | 'needs_review' | 'approved'

export interface AliasOut {
  name: string
  confidence: number
  basis: string
  chapter_id?: string | null
  evidence?: Array<Record<string, unknown>>
  source?: string
  conflict_character_id?: string | null
  reason?: string | null
}

export interface RelationshipOut {
  id: string
  book_id: string
  source_character_id: string
  target_character_id: string
  source_name: string | null
  target_name: string | null
  relationship_type: string
  description: string | null
  evidence: Array<Record<string, unknown>>
  confidence: number
  is_provisional: boolean
  created_at: string
  updated_at: string
}

export interface CharacterOut {
  id: string
  book_id: string
  canonical_name: string
  aliases: AliasOut[]
  alias_candidates: AliasOut[]
  gender: string
  gender_basis: string
  age_range: string
  age_basis: string
  character_type: string
  identity_description: string | null
  personality: string[]
  personality_desc: string | null
  appearance_desc: string | null
  voice_description: string | null
  first_chapter_id: string | null
  evidence: Array<Record<string, unknown>>
  confidence: number
  is_provisional: boolean
  user_locked: boolean
  merged_into_id: string | null
  merge_log: Array<Record<string, unknown>>
  merged_source_ids: string[]
  created_at: string
  updated_at: string
}

export interface CharacterListResponse {
  items: CharacterOut[]
  total: number
}

export interface RelationshipListResponse {
  items: RelationshipOut[]
  total: number
}

export interface CharacterUpdatePayload {
  canonical_name?: string
  aliases?: string[]
  gender?: CharacterGender
  age_range?: AgeRange
  character_type?: CharacterType
  identity_description?: string | null
  personality?: string[]
  personality_desc?: string | null
  appearance_desc?: string | null
  voice_description?: string | null
  is_provisional?: boolean
  user_locked?: boolean
}

export interface CharacterMergeResponse {
  target: CharacterOut
  merged_source_ids: string[]
  updated_segments: number
}

export interface CharacterSplitResponse {
  character: CharacterOut
  restored: CharacterOut
  updated_segments: number
}

export interface SegmentOut {
  id: string
  book_id: string
  chapter_id: string
  segment_index: number
  source_text: string
  speech_text: string
  segment_type: string
  speaker_type: string
  speaker_id: string | null
  speaker_name_snapshot: string | null
  speaker_confidence: number | null
  emotion: string
  emotion_confidence: number | null
  intensity: number
  delivery: string
  tts_instruction: string | null
  speed: number
  pause_before_ms: number
  pause_after_ms: number
  review_status: string
  audio_status: string
  voice_profile_id: string | null
  current_audio_asset_id: string | null
  user_locked_fields: string[]
  text_version: number
  source_start_offset: number | null
  source_end_offset: number | null
  evidence: Array<Record<string, unknown>>
  low_speaker_confidence: boolean
  low_emotion_confidence: boolean
  created_at: string
  updated_at: string
}

export interface SegmentListResponse {
  items: SegmentOut[]
  total: number
  chapter_id: string
}

export interface SegmentListFilters {
  reviewStatus?: string
  segmentType?: string
  speakerType?: string
  lowConfidence?: boolean
}

export interface SegmentUpdatePayload {
  speech_text?: string
  segment_type?: SegmentType
  speaker_type?: SpeakerType
  speaker_id?: string | null
  emotion?: Emotion
  intensity?: number
  delivery?: Delivery
  tts_instruction?: string | null
  speed?: number
  pause_before_ms?: number
  pause_after_ms?: number
  review_status?: ReviewStatus
  unlock_fields?: string[]
}

export interface SegmentBatchUpdateResponse {
  updated_count: number
  affected_segment_ids: string[]
}

export interface AnalysisJob {
  id: string
  book_id: string
  job_type: string
  status: string
  total_items: number
  completed_items: number
  failed_items: number
  progress: number
  params: Record<string, unknown>
  result: Record<string, unknown>
  error_code: string | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export interface AnalysisChapterStatus {
  id: string
  title: string
  sequence: number
  analysis_status: string
  has_summary: boolean
  summary: string | null
  segment_count: number
  review_count: number
  error: string | null
}

export interface AnalysisStatusResponse {
  book_id: string
  job: AnalysisJob | null
  total_chapters: number
  completed_chapters: number
  review_chapters: number
  failed_chapters: number
  total_segments: number
  review_segments: number
  total_characters: number
  chapters: AnalysisChapterStatus[]
}

export interface ChapterAnalysisResponse {
  chapter: AnalysisChapterStatus
  characters_added: number
  relationships_added: number
  segments_written: number
  meta: Record<string, unknown>
}

/* ------------------------------ 阶段 3：声音 ------------------------------ */

export type VoiceOwnerType = 'narrator' | 'character'

export interface VoiceProfileOut {
  id: string
  owner_type: VoiceOwnerType
  owner_id: string
  owner_name: string
  name: string
  description: string | null
  reference_text: string | null
  instruct_text: string | null
  seed: number | null
  status: string
  is_selected: boolean
  is_mock: boolean
  sample_rate: number | null
  duration_ms: number | null
  generation_params: Record<string, unknown>
  audio_url: string
  created_at: string
  updated_at: string
}

export interface VoiceOwnerOut {
  owner_type: VoiceOwnerType
  owner_id: string
  name: string
  description: string
  gender: string | null
  age_range: string | null
  has_selected: boolean
  selected_profile_id: string | null
  profile_count: number
  profiles: VoiceProfileOut[]
}

export interface VoiceLibraryResponse {
  book_id: string
  narrator: VoiceOwnerOut
  characters: VoiceOwnerOut[]
  total_profiles: number
  selected_owners: number
  characters_without_voice: string[]
}

export interface VoiceDesignPayload {
  owner_type: VoiceOwnerType
  owner_id: string
  description?: string | null
  reference_text?: string | null
  candidates?: number | null
  seed?: number | null
  language?: string
}

export interface VoiceDesignError {
  index: number
  message: string
}

export interface VoiceDesignResponse {
  items: VoiceProfileOut[]
  errors: VoiceDesignError[]
  requested: number
  book_id: string
}

export interface VoiceUpdatePayload {
  name?: string
  description?: string
  user_locked?: boolean
}

export interface VoicePreviewPayload {
  text: string
  emotion?: Emotion
  intensity?: number
  delivery?: Delivery
  instruction?: string | null
  speed?: number
}

export interface AudioAssetOut {
  id: string
  segment_id: string
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

export interface SegmentAudioOut {
  segment_id: string
  audio_status: string
  voice_profile_id: string | null
  asset: AudioAssetOut | null
}

export interface AudioVersionListResponse {
  segment_id: string
  current_asset_id: string | null
  items: AudioAssetOut[]
  total: number
}