"""v0.5.0 P2：全书角色圣经、人物弧与剧情时间线响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from novelvoice_studio.schemas.analysis import AnalysisJobOut, RelationshipOut


class BibleAliasOut(BaseModel):
    name: str
    confidence: float = 0.0
    basis: str = "unknown"
    chapter_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "ai"


class CharacterArcOut(BaseModel):
    id: str
    arc_index: int
    label: str
    age_stage: str
    chapter_start_id: str | None = None
    chapter_end_id: str | None = None
    story_time_start: str | None = None
    story_time_end: str | None = None
    story_order_start: int | None = None
    story_order_end: int | None = None
    summary: str | None = None
    traits: list[str] = Field(default_factory=list)
    appearance: dict[str, Any] = Field(default_factory=dict)
    voice_traits: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "ai"
    created_at: datetime
    updated_at: datetime


class BibleCharacterOut(BaseModel):
    id: str
    canonical_name: str
    role_tier: str = "unknown"
    gender: str = "unknown"
    age_range: str = "unknown"
    character_type: str = "unknown"
    identity_description: str | None = None
    personality: list[str] = Field(default_factory=list)
    appearance_desc: str | None = None
    voice_description: str | None = None
    aliases: list[BibleAliasOut] = Field(default_factory=list)
    confidence: float = 0.0
    is_provisional: bool = True
    user_locked: bool = False
    arcs: list[CharacterArcOut] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class TimelineChapterOut(BaseModel):
    chapter_id: str
    sequence: int
    title: str
    summary: str | None = None
    story_time_label: str | None = None
    story_order: int | None = None
    time_mode: str = "unknown"
    confidence: float = 0.0
    characters: list[str] = Field(default_factory=list)


class BibleTimelineOut(BaseModel):
    narrative_mode: str = "unknown"
    chapters: list[TimelineChapterOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BibleReviewItemOut(BaseModel):
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


class BibleOut(BaseModel):
    id: str
    book_id: str
    version: int
    status: str
    source: str
    prompt_version: str | None = None
    model: str | None = None
    summary: str | None = None
    confidence: float = 0.0
    is_active: bool = False
    approved_at: datetime | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    characters: list[BibleCharacterOut] = Field(default_factory=list)
    relationships: list[RelationshipOut] = Field(default_factory=list)
    timeline: BibleTimelineOut = Field(default_factory=BibleTimelineOut)
    review_items: list[BibleReviewItemOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class BibleStatusResponse(BaseModel):
    book_id: str
    bible_status: str
    open_reviews: int = 0
    bible_id: str | None = None
    version: int | None = None
    job: AnalysisJobOut | None = None


class BibleAnalysisRequest(BaseModel):
    """为空时全书生成；指定章节时仅重跑这些章节并复用其余章节观察。"""

    chapter_ids: list[str] = Field(default_factory=list, max_length=2000)


class BibleStateResponse(BaseModel):
    book_id: str
    bible_status: str
    open_reviews: int = 0
    bible: BibleOut | None = None
    job: AnalysisJobOut | None = None