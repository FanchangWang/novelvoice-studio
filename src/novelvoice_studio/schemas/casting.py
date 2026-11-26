"""v0.5.0 P5：全局配音员库与选角响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.voice import VoicePreviewRequest

GenderValue = Literal["male", "female", "neutral", "unknown"]
AgeValue = Literal[
    "child",
    "teenager",
    "young_adult",
    "adult",
    "middle_aged",
    "elderly",
    "unknown",
]
ActorStatusValue = Literal["draft", "ready", "failed", "archived"]
CastScopeValue = Literal["narrator", "character", "character_stage"]


class VoiceActorUsage(BaseModel):
    active_cast_count: int = 0
    book_count: int = 0
    last_used_at: datetime | None = None


class VoiceActorOut(BaseModel):
    id: str
    display_name: str
    description: str | None = None
    gender_presentation: str
    age_impression: str
    language: str
    timbre_tags: list[str] = Field(default_factory=list)
    personality_tags: list[str] = Field(default_factory=list)
    reference_text: str | None = None
    instruct_text: str | None = None
    seed: int | None = None
    provider: str | None = None
    model: str | None = None
    generation_params: dict[str, Any] = Field(default_factory=dict)
    sample_rate: int | None = None
    duration_ms: int | None = None
    status: str
    source: str
    source_book_id: str | None = None
    is_favorite: bool
    is_mock: bool = False
    audio_url: str | None = None
    usage: VoiceActorUsage = Field(default_factory=VoiceActorUsage)
    created_at: datetime
    updated_at: datetime


class VoiceActorListResponse(BaseModel):
    items: list[VoiceActorOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class VoiceActorCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    gender_presentation: GenderValue = "unknown"
    age_impression: AgeValue = "adult"
    language: str = Field(default="zh-CN", max_length=32)
    timbre_tags: list[str] = Field(default_factory=list, max_length=20)
    personality_tags: list[str] = Field(default_factory=list, max_length=20)
    reference_text: str | None = Field(default=None, max_length=500)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    is_favorite: bool = False
    source_book_id: str | None = Field(default=None, max_length=32)

    @field_validator("display_name", "description")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("timbre_tags", "personality_tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(cleaned))[:20]

    @field_validator("reference_text")
    @classmethod
    def _strip_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class VoiceActorUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    gender_presentation: GenderValue | None = None
    age_impression: AgeValue | None = None
    timbre_tags: list[str] | None = Field(default=None, max_length=20)
    personality_tags: list[str] | None = Field(default=None, max_length=20)
    is_favorite: bool | None = None
    status: Literal["ready", "archived"] | None = None

    @field_validator("display_name", "description")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("timbre_tags", "personality_tags")
    @classmethod
    def _clean_optional_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return list(dict.fromkeys(cleaned))[:20]


class CastActorOut(BaseModel):
    id: str
    display_name: str
    gender_presentation: str
    age_impression: str
    language: str
    timbre_tags: list[str] = Field(default_factory=list)
    personality_tags: list[str] = Field(default_factory=list)
    is_favorite: bool = False
    is_mock: bool = False
    audio_url: str | None = None


class VoiceCastOut(BaseModel):
    id: str
    book_id: str
    character_id: str | None = None
    character_name: str | None = None
    voice_actor_id: str
    scope: str
    age_stage_condition: str | None = None
    state_condition: str | None = None
    priority: int = 0
    source: str
    confidence: float = 0.0
    is_active: bool = True
    reason: str | None = None
    actor: CastActorOut | None = None
    created_at: datetime
    updated_at: datetime


class CastStagePlanOut(BaseModel):
    age_stage: str
    label: str
    cast: VoiceCastOut | None = None


class CastCharacterPlanOut(BaseModel):
    character_id: str
    name: str
    gender: str
    age_range: str
    role_tier: str
    cast_required: bool = True
    base_cast: VoiceCastOut | None = None
    stages: list[CastStagePlanOut] = Field(default_factory=list)
    missing: bool = False


class BookCastsResponse(BaseModel):
    book_id: str
    bible_ready: bool = False
    narrator: VoiceCastOut | None = None
    characters: list[CastCharacterPlanOut] = Field(default_factory=list)
    cast_count: int = 0
    missing_roles: list[str] = Field(default_factory=list)
    open_reviews: int = 0
    ready: bool = False
    job: AnalysisJobOut | None = None


class CastingStatusResponse(BaseModel):
    book_id: str
    production_status: str
    running: bool = False
    cast_count: int = 0
    missing_roles: list[str] = Field(default_factory=list)
    open_reviews: int = 0
    job: AnalysisJobOut | None = None


class CastAssignRequest(BaseModel):
    scope: CastScopeValue
    voice_actor_id: str = Field(min_length=3, max_length=32)
    character_id: str | None = Field(default=None, max_length=32)
    age_stage_condition: AgeValue | None = None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("character_id")
    @classmethod
    def _strip_character(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CastPreviewRequest(VoicePreviewRequest):
    pass