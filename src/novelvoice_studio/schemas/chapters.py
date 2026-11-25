"""章节相关请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    char_count: int
    source_start_offset: int
    source_end_offset: int
    analysis_status: str
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


class ChapterUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


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