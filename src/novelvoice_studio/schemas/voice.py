"""阶段 3：音色库、试听与单段音频契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from novelvoice_studio.schemas.analysis import DeliveryValue, EmotionValue

OwnerTypeValue = Literal["character", "narrator"]


class VoiceProfileOut(BaseModel):
    id: str
    owner_type: str
    owner_id: str
    owner_name: str
    name: str
    description: str | None = None
    reference_text: str | None = None
    instruct_text: str | None = None
    seed: int | None = None
    status: str
    is_selected: bool
    is_mock: bool
    sample_rate: int | None = None
    duration_ms: int | None = None
    generation_params: dict[str, Any] = Field(default_factory=dict)
    audio_url: str
    created_at: datetime
    updated_at: datetime


class VoiceOwnerOut(BaseModel):
    owner_type: str
    owner_id: str
    name: str
    description: str
    gender: str | None = None
    age_range: str | None = None
    has_selected: bool = False
    selected_profile_id: str | None = None
    profile_count: int = 0
    profiles: list[VoiceProfileOut] = Field(default_factory=list)


class VoiceLibraryResponse(BaseModel):
    book_id: str
    narrator: VoiceOwnerOut
    characters: list[VoiceOwnerOut] = Field(default_factory=list)
    total_profiles: int = 0
    selected_owners: int = 0
    characters_without_voice: list[str] = Field(default_factory=list)


class VoiceDesignRequest(BaseModel):
    owner_type: OwnerTypeValue
    owner_id: str = Field(min_length=3, max_length=32)
    description: str | None = Field(default=None, max_length=2000)
    reference_text: str | None = Field(default=None, max_length=500)
    candidates: int | None = Field(default=None, ge=1, le=5)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    language: str = Field(default="zh-CN", max_length=32)

    @field_validator("reference_text", "description")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class VoiceDesignError(BaseModel):
    index: int
    message: str


class VoiceDesignResponse(BaseModel):
    items: list[VoiceProfileOut] = Field(default_factory=list)
    errors: list[VoiceDesignError] = Field(default_factory=list)
    requested: int
    book_id: str


class VoiceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    user_locked: bool | None = None

    @field_validator("name", "description")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空字符串")
        return cleaned


class VoicePreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    emotion: EmotionValue = "neutral"
    intensity: int = Field(default=1, ge=1, le=3)
    delivery: DeliveryValue = "normal"
    instruction: str | None = Field(default=None, max_length=500)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("试听文本不能为空")
        return cleaned


class AudioAssetOut(BaseModel):
    id: str
    segment_id: str
    version: int
    format: str
    sample_rate: int
    channels: int
    sample_width_bits: int
    duration_ms: int
    file_size_bytes: int
    tts_provider: str | None = None
    tts_model: str | None = None
    is_valid: bool
    is_current: bool = False
    audio_url: str
    created_at: datetime


class SegmentAudioOut(BaseModel):
    segment_id: str
    audio_status: str
    voice_profile_id: str | None = None
    asset: AudioAssetOut | None = None


class AudioVersionListResponse(BaseModel):
    segment_id: str
    current_asset_id: str | None = None
    items: list[AudioAssetOut] = Field(default_factory=list)
    total: int = 0