"""书籍相关请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DuplicatePolicy = Literal["new", "reject", "replace", "merge"]


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    language: str = Field(default="zh-CN", max_length=16)


class BookUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    language: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _require_any_field(self) -> "BookUpdate":
        if not self.model_fields_set:
            raise ValueError("至少需要修改一个字段")
        return self


class BookSummary(BaseModel):
    id: str
    title: str
    author: str | None
    description: str | None
    language: str
    status: str
    bible_status: str = "not_started"
    production_status: str = "not_started"
    source_encoding: str | None
    source_char_count: int
    chapter_count: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class BookDetail(BookSummary):
    project_dir: str
    source_file_path: str | None
    import_warnings: list[str] = Field(default_factory=list)


class DuplicateBookInfo(BaseModel):
    id: str
    title: str
    created_at: datetime


class EncodingCandidateOut(BaseModel):
    encoding: str
    label: str
    confidence: float
    reason: str


class BookMetadataOut(BaseModel):
    title: str | None = None
    author: str | None = None
    description: str | None = None
    title_source: str = "filename"
    author_source: str | None = None
    description_source: str | None = None
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class ImportPreviewResponse(BaseModel):
    filename: str
    size_bytes: int
    sha256: str
    encoding: str
    encoding_label: str
    encoding_confidence: float
    needs_encoding_confirmation: bool
    encoding_candidates: list[EncodingCandidateOut]
    sample_text: str
    total_chars: int
    line_count: int
    chapter_count: int
    chapter_titles: list[str]
    chapters_truncated: bool
    structure_source: Literal["rules", "ai", "custom"] = "rules"
    ai_assisted: bool = False
    warnings: list[str]
    duplicate: DuplicateBookInfo | None = None
    metadata: BookMetadataOut = Field(default_factory=BookMetadataOut)


class ImportResult(BaseModel):
    book: BookDetail
    chapters_created: int
    chapters_updated: int = 0
    chapters_unchanged: int = 0
    chapters_changed: int = 0
    encoding: str
    warnings: list[str]
    replaced: bool = False
    mode: Literal["new", "replace", "merge"] = "new"
    structure_source: Literal["rules", "ai", "custom"] = "rules"
    ai_assisted: bool = False


class BookListResponse(BaseModel):
    items: list[BookSummary]
    total: int