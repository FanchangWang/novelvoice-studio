"""v0.5.0 P4：有声剧本响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from novelvoice_studio.schemas.analysis import AnalysisJobOut


class ScriptVersionOut(BaseModel):
    id: str
    book_id: str
    chapter_id: str
    version: int
    status: str
    adaptation_level: str
    source_scene_version: int
    source_hash: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = False
    approved_at: datetime | None = None
    unit_count: int = 0
    created_at: datetime
    updated_at: datetime


class ScriptUnitOut(BaseModel):
    id: str
    book_id: str
    chapter_id: str
    script_version_id: str
    scene_id: str | None = None
    unit_index: int
    unit_type: str
    speaker_type: str = "unknown"
    speaker_id: str | None = None
    speaker_name: str | None = None
    character_state_id: str | None = None
    voice_cast_id: str | None = None
    source_text: str
    text: str
    display_text: str | None = None
    source_start_offset: int
    source_end_offset: int
    emotion: str = "neutral"
    intensity: int = 1
    delivery: str = "normal"
    tts_instruction: str | None = None
    speed: float = 1.0
    pause_before_ms: int = 0
    pause_after_ms: int = 250
    sfx: dict[str, Any] = Field(default_factory=dict)
    review_status: str = "auto"
    confidence: float = 0.0
    audio_status: str = "none"
    current_audio_asset_id: str | None = None
    render_hash: str | None = None
    user_locked_fields: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "ai"
    created_at: datetime
    updated_at: datetime


class ScriptReviewItemOut(BaseModel):
    id: str
    entity_type: str
    entity_id: str | None = None
    code: str
    severity: str
    title: str
    message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    source: str
    created_at: datetime
    updated_at: datetime


class ScriptStatusResponse(BaseModel):
    chapter_id: str
    book_id: str
    script_status: str
    script_version: int = 0
    adaptation_level: str | None = None
    open_reviews: int = 0
    job: AnalysisJobOut | None = None


class ScriptStateResponse(ScriptStatusResponse):
    version: ScriptVersionOut | None = None
    units: list[ScriptUnitOut] = Field(default_factory=list)
    review_items: list[ScriptReviewItemOut] = Field(default_factory=list)


class ScriptVersionListResponse(BaseModel):
    chapter_id: str
    items: list[ScriptVersionOut] = Field(default_factory=list)
    total: int = 0