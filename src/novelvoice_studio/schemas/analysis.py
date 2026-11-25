"""阶段 2：章节分析、角色审核与片段审核模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

GenderValue = Literal["male", "female", "other", "unknown"]
BasisValue = Literal["explicit", "inferred", "unknown"]
AgeRangeValue = Literal[
    "child", "teenager", "young_adult", "adult", "middle_aged", "elderly", "unknown"
]
CharacterTypeValue = Literal["human", "animal", "non_human", "unknown"]
EmotionValue = Literal[
    "neutral",
    "happy",
    "excited",
    "angry",
    "sad",
    "fearful",
    "surprised",
    "doubtful",
    "sarcastic",
    "tender",
    "embarrassed",
    "determined",
    "tired",
]
DeliveryValue = Literal[
    "normal", "whispering", "shouting", "crying", "laughing", "breathless", "slow", "hesitant"
]
SegmentTypeValue = Literal["narration", "dialogue", "inner_monologue", "unknown"]
SpeakerTypeValue = Literal["character", "narrator", "unknown"]
ReviewStatusValue = Literal["auto", "needs_review", "approved"]


class AliasOut(BaseModel):
    name: str
    confidence: float = 0.0
    basis: str = "inferred"
    chapter_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "ai"
    conflict_character_id: str | None = None
    reason: str | None = None


class RelationshipOut(BaseModel):
    id: str
    book_id: str
    source_character_id: str
    target_character_id: str
    source_name: str | None = None
    target_name: str | None = None
    relationship_type: str
    description: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float
    is_provisional: bool
    created_at: datetime
    updated_at: datetime


class CharacterOut(BaseModel):
    id: str
    book_id: str
    canonical_name: str
    aliases: list[AliasOut] = Field(default_factory=list)
    alias_candidates: list[AliasOut] = Field(default_factory=list)
    gender: str
    gender_basis: str
    age_range: str
    age_basis: str
    character_type: str
    identity_description: str | None = None
    personality: list[str] = Field(default_factory=list)
    personality_desc: str | None = None
    appearance_desc: str | None = None
    voice_description: str | None = None
    first_chapter_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float
    is_provisional: bool
    user_locked: bool
    merged_into_id: str | None = None
    merge_log: list[dict[str, Any]] = Field(default_factory=list)
    merged_source_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CharacterListResponse(BaseModel):
    items: list[CharacterOut]
    total: int


class RelationshipListResponse(BaseModel):
    items: list[RelationshipOut]
    total: int


class CharacterUpdate(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=120)
    aliases: list[str] | None = None
    gender: GenderValue | None = None
    gender_basis: BasisValue | None = None
    age_range: AgeRangeValue | None = None
    age_basis: BasisValue | None = None
    character_type: CharacterTypeValue | None = None
    identity_description: str | None = None
    personality: list[str] | None = None
    personality_desc: str | None = None
    appearance_desc: str | None = None
    voice_description: str | None = None
    is_provisional: bool | None = None
    user_locked: bool | None = None


class CharacterMergeRequest(BaseModel):
    target_character_id: str
    source_character_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct(self) -> "CharacterMergeRequest":
        if self.target_character_id in self.source_character_ids:
            raise ValueError("目标角色不能同时作为被合并角色")
        if len(set(self.source_character_ids)) != len(self.source_character_ids):
            raise ValueError("被合并角色不能重复")
        return self


class CharacterMergeResponse(BaseModel):
    target: CharacterOut
    merged_source_ids: list[str]
    updated_segments: int


class CharacterSplitRequest(BaseModel):
    character_id: str
    source_character_id: str

    @model_validator(mode="after")
    def _distinct(self) -> "CharacterSplitRequest":
        if self.character_id == self.source_character_id:
            raise ValueError("当前角色与被拆分角色不能相同")
        return self


class CharacterSplitResponse(BaseModel):
    character: CharacterOut
    restored: CharacterOut
    updated_segments: int


class SegmentOut(BaseModel):
    id: str
    book_id: str
    chapter_id: str
    segment_index: int
    source_text: str
    speech_text: str
    segment_type: str
    speaker_type: str
    speaker_id: str | None = None
    speaker_name_snapshot: str | None = None
    speaker_confidence: float | None = None
    emotion: str
    emotion_confidence: float | None = None
    intensity: int
    delivery: str
    tts_instruction: str | None = None
    speed: float
    pause_before_ms: int
    pause_after_ms: int
    review_status: str
    audio_status: str
    voice_profile_id: str | None = None
    current_audio_asset_id: str | None = None
    user_locked_fields: list[str] = Field(default_factory=list)
    text_version: int
    source_start_offset: int | None = None
    source_end_offset: int | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    low_speaker_confidence: bool = False
    low_emotion_confidence: bool = False
    created_at: datetime
    updated_at: datetime


class SegmentListResponse(BaseModel):
    items: list[SegmentOut]
    total: int
    chapter_id: str


class SegmentUpdate(BaseModel):
    speech_text: str | None = Field(default=None, min_length=1)
    segment_type: SegmentTypeValue | None = None
    speaker_type: SpeakerTypeValue | None = None
    speaker_id: str | None = None
    emotion: EmotionValue | None = None
    intensity: int | None = Field(default=None, ge=1, le=3)
    delivery: DeliveryValue | None = None
    tts_instruction: str | None = None
    speed: float | None = Field(default=None, gt=0, le=2.0)
    pause_before_ms: int | None = Field(default=None, ge=0, le=10000)
    pause_after_ms: int | None = Field(default=None, ge=0, le=10000)
    review_status: ReviewStatusValue | None = None
    unlock_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _speaker_consistency(self) -> "SegmentUpdate":
        if self.speaker_type == "character" and not self.speaker_id:
            raise ValueError("speaker_type=character 时必须提供 speaker_id")
        if self.speaker_type in {"narrator", "unknown"} and self.speaker_id:
            raise ValueError("旁白或未知说话人不能绑定角色")
        return self


class SegmentBatchUpdate(BaseModel):
    segment_ids: list[str] = Field(min_length=1, max_length=500)
    update: SegmentUpdate


class SegmentBatchUpdateResponse(BaseModel):
    updated_count: int
    affected_segment_ids: list[str]


class AnalysisRequest(BaseModel):
    chapter_ids: list[str] = Field(default_factory=list)


class AnalysisJobOut(BaseModel):
    id: str
    book_id: str
    job_type: str
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    progress: float
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AnalysisChapterStatus(BaseModel):
    id: str
    title: str
    sequence: int
    analysis_status: str
    has_summary: bool
    summary: str | None = None
    segment_count: int = 0
    review_count: int = 0
    error: str | None = None


class AnalysisStatusResponse(BaseModel):
    book_id: str
    job: AnalysisJobOut | None = None
    total_chapters: int
    completed_chapters: int
    review_chapters: int
    failed_chapters: int
    total_segments: int
    review_segments: int
    total_characters: int
    chapters: list[AnalysisChapterStatus] = Field(default_factory=list)


class ChapterAnalysisResponse(BaseModel):
    chapter: AnalysisChapterStatus
    characters_added: int
    relationships_added: int
    segments_written: int
    meta: dict[str, Any] = Field(default_factory=dict)