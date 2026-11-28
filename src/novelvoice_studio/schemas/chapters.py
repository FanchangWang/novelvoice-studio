"""章节相关请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ParagraphOut(BaseModel):
    index: int
    start_offset: int
    end_offset: int
    text: str


class ChapterSummary(BaseModel):
    id: str
    book_id: str
    title: str
    sequence: int
    volume_number: int = 0
    volume_title: str = "默认卷"
    chapter_kind: str = "story"
    classification_status: str = "ai_suggested"
    analysis_included: bool = True
    char_count: int
    source_start_offset: int
    source_end_offset: int
    analysis_status: str
    scene_status: str = "not_started"
    scene_version: int = 0
    script_status: str = "not_started"
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


class ChapterDetail(ChapterSummary):
    text: str
    paragraphs: list[ParagraphOut]
    summary_json: dict[str, Any] = Field(default_factory=dict)
    analysis_meta: dict[str, Any] = Field(default_factory=dict)


class ChapterListResponse(BaseModel):
    items: list[ChapterSummary]
    total: int
    page: int = 1
    page_size: int = 20


class ChapterUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    chapter_kind: Literal["story", "extra", "author_note", "unclassified"] | None = None
    classification_status: Literal["ai_suggested", "confirmed", "unclassified"] | None = None
    analysis_included: bool | None = None

    @model_validator(mode="after")
    def _require_any_field(self) -> "ChapterUpdate":
        if not self.model_fields_set:
            raise ValueError("至少需要修改一个字段")
        return self


class ChapterSplitRequest(BaseModel):
    paragraph_index: int = Field(ge=1, description="从第 N 个段落开始成为新章节（0 基）")
    new_title: str | None = Field(default=None, min_length=1, max_length=255)


class ChapterMergeRequest(BaseModel):
    chapter_ids: list[str] = Field(min_length=2)
    new_title: str | None = Field(default=None, min_length=1, max_length=255)


class ChapterMutationResponse(BaseModel):
    chapter: ChapterSummary
    affected_chapter_ids: list[str]
    items: list[ChapterSummary] = Field(default_factory=list)