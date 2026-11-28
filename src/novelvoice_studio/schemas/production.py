"""v0.5.0 P6：制作台总览与异常收件箱响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from novelvoice_studio.schemas.analysis import AnalysisJobOut

ReviewAction = Literal["accept", "ignore"]


class ReviewItemOut(BaseModel):
    id: str
    book_id: str
    book_title: str | None = None
    entity_type: str
    entity_id: str | None = None
    chapter_id: str | None = None
    chapter_title: str | None = None
    code: str
    category: str = "content"
    actionable: bool = True
    severity: str
    title: str
    message: str | None = None
    location: dict[str, Any] = Field(default_factory=dict)
    impact: str = ""
    recommended_action: str = ""
    action_type: str = "chapter"
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    source: str
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReviewCountsOut(BaseModel):
    open: int = 0
    blocking: int = 0
    warning: int = 0
    info: int = 0
    resolved: int = 0
    ignored: int = 0


class ReviewListResponse(BaseModel):
    items: list[ReviewItemOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    offset: int = 0
    counts: ReviewCountsOut = Field(default_factory=ReviewCountsOut)


class ReviewResolveRequest(BaseModel):
    action: ReviewAction
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_ignore_note(self) -> "ReviewResolveRequest":
        if self.action == "ignore" and not (self.note or "").strip():
            raise ValueError("忽略异常必须填写原因")
        return self


class ReviewBatchResolveRequest(BaseModel):
    review_ids: list[str] = Field(min_length=1, max_length=250)
    action: ReviewAction
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_ignore_note(self) -> "ReviewBatchResolveRequest":
        if self.action == "ignore" and not (self.note or "").strip():
            raise ValueError("忽略异常必须填写原因")
        return self


class ReviewBatchResolveResponse(BaseModel):
    action: str
    resolved: int = 0
    ignored: int = 0
    skipped: int = 0
    review_ids: list[str] = Field(default_factory=list)
    book_ids: list[str] = Field(default_factory=list)
    codes: list[str] = Field(default_factory=list)


class AutoProductionRequest(BaseModel):
    refresh_bible: bool = False


class ProductionBatchRequest(BaseModel):
    stages: list[Literal["scenes", "script"]] = Field(
        default_factory=lambda: ["scenes", "script"], min_length=1, max_length=2
    )
    volume_number: int | None = Field(default=None, ge=0)
    chapter_ids: list[str] = Field(default_factory=list, max_length=500)
    only_missing: bool = True
    adaptation_level: Literal["light", "standard", "strong"] = "light"

    @model_validator(mode="after")
    def _distinct(self) -> "ProductionBatchRequest":
        self.stages = list(dict.fromkeys(self.stages))
        self.chapter_ids = list(dict.fromkeys(self.chapter_ids))
        return self


class ProductionChapterOut(BaseModel):
    id: str
    title: str
    sequence: int
    volume_number: int = 0
    volume_title: str = "默认卷"
    chapter_kind: str = "story"
    analysis_included: bool = True
    char_count: int
    scene_status: str = "not_started"
    scene_version: int = 0
    scene_count: int = 0
    script_status: str = "not_started"
    script_version: int = 0
    unit_count: int = 0
    tts_unit_count: int = 0
    cue_count: int = 0
    audio_ready_units: int = 0
    audio_stale_units: int = 0
    review_count: int = 0
    summary: str | None = None


class ProductionOverviewOut(BaseModel):
    book_id: str
    title: str
    production_status: str
    derived_status: str
    bible_status: str
    bible_version: int | None = None
    bible_ready: bool = False
    bible_summary: str | None = None
    character_count: int = 0
    arc_count: int = 0
    chapter_count: int = 0
    chapters_with_scenes: int = 0
    chapters_with_scripts: int = 0
    scene_count: int = 0
    script_unit_count: int = 0
    tts_unit_count: int = 0
    cue_count: int = 0
    audio_ready_units: int = 0
    audio_stale_units: int = 0
    cast_count: int = 0
    narrator_ready: bool = False
    open_reviews: int = 0
    blocking_reviews: int = 0
    warning_reviews: int = 0
    info_reviews: int = 0
    next_action: str | None = None
    job: AnalysisJobOut | None = None
    chapters: list[ProductionChapterOut] = Field(default_factory=list)