"""书籍与章节。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint("status IN ('empty','ready','deleted')", name="ck_books_status"),
        CheckConstraint(
            "bible_status IN ('not_started','running','needs_review','approved','failed')",
            name="ck_books_bible_status",
        ),
        CheckConstraint(
            "production_status IN "
            "('not_started','analyzing','scripting','casting','needs_review','ready','generating','failed')",
            name="ck_books_production_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="empty", nullable=False)
    bible_status: Mapped[str] = mapped_column(
        String(24), default="not_started", nullable=False
    )
    production_status: Mapped[str] = mapped_column(
        String(24), default="not_started", nullable=False
    )
    project_dir: Mapped[str] = mapped_column(Text, nullable=False)
    source_file_path: Mapped[str | None] = mapped_column(Text)
    source_encoding: Mapped[str | None] = mapped_column(String(32))
    source_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    import_warnings_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="Chapter.sequence",
    )


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        Index("ix_chapters_book_sequence", "book_id", "sequence", unique=True),
        CheckConstraint(
            "scene_status IN ('not_started','running','completed','needs_review','failed')",
            name="ck_chapters_scene_status",
        ),
        CheckConstraint(
            "script_status IN ('not_started','running','completed','needs_review','failed')",
            name="ck_chapters_script_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    analysis_meta_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    analysis_status: Mapped[str] = mapped_column(
        String(24), default="not_started", nullable=False
    )
    scene_status: Mapped[str] = mapped_column(
        String(24), default="not_started", nullable=False
    )
    scene_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    script_status: Mapped[str] = mapped_column(
        String(24), default="not_started", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    book: Mapped[Book] = relationship(back_populates="chapters")

    @property
    def char_count(self) -> int:
        return max(0, self.source_end_offset - self.source_start_offset)