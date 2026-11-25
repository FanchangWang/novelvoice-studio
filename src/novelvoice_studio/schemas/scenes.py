"""v0.5.0 P3：分场与场景内角色状态响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from novelvoice_studio.schemas.analysis import AnalysisJobOut


class SceneCharacterOut(BaseModel):
    id: str
    book_id: str
    scene_id: str
    character_id: str
    character_name: str | None = None
    state_phase: int = 0
    presence: str = "present"
    age_stage: str = "unknown"
    physical_state: str | None = None
    emotional_state: str | None = None
    relationship_state: str | None = None
    knowledge_state: str | None = None
    disguise_state: str | None = None
    voice_note: str | None = None
    state_tags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "ai"
    created_at: datetime
    updated_at: datetime


class SceneOut(BaseModel):
    id: str
    book_id: str
    chapter_id: str
    analysis_version: int
    scene_index: int
    title: str | None = None
    summary: str | None = None
    location: str | None = None
    story_time_label: str | None = None
    story_order: int | None = None
    time_mode: str = "unknown"
    pov_character_id: str | None = None
    pov_character_name: str | None = None
    mood: str | None = None
    source_start_offset: int
    source_end_offset: int
    analysis_status: str = "draft"
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    source: str = "ai"
    characters: list[SceneCharacterOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SceneReviewItemOut(BaseModel):
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


class SceneStatusResponse(BaseModel):
    chapter_id: str
    book_id: str
    scene_status: str
    scene_version: int = 0
    open_reviews: int = 0
    job: AnalysisJobOut | None = None


class SceneStateResponse(SceneStatusResponse):
    scenes: list[SceneOut] = Field(default_factory=list)
    review_items: list[SceneReviewItemOut] = Field(default_factory=list)