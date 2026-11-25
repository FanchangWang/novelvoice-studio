"""全书角色圣经与人物弧。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now
from novelvoice_studio.models.character import AGE_RANGE_VALUES

_BIBLE_STATUS = "('draft','running','needs_review','approved','superseded','failed')"
_SOURCE_VALUES = "('ai','legacy','user','system')"
_AGE_STAGES = "(" + ",".join(f"'{item}'" for item in AGE_RANGE_VALUES) + ")"
_SOURCE_SQL = _SOURCE_VALUES


class BookBible(Base):
    __tablename__ = "book_bibles"
    __table_args__ = (
        UniqueConstraint("book_id", "version", name="uq_book_bibles_book_version"),
        Index(
            "uq_book_bibles_active",
            "book_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        CheckConstraint(f"status IN {_BIBLE_STATUS}", name="ck_book_bibles_status"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_book_bibles_source"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_book_bibles_confidence"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    timeline_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class CharacterArc(Base):
    __tablename__ = "character_arcs"
    __table_args__ = (
        UniqueConstraint(
            "bible_id", "character_id", "arc_index", name="uq_character_arcs_bible_character_index"
        ),
        Index("ix_character_arcs_character_index", "character_id", "arc_index"),
        CheckConstraint(f"age_stage IN {_AGE_STAGES}", name="ck_character_arcs_age_stage"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_character_arcs_source"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_character_arcs_confidence"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bible_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("book_bibles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    character_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    arc_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="未分阶段", nullable=False)
    age_stage: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    chapter_start_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="SET NULL")
    )
    chapter_end_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="SET NULL")
    )
    story_time_start: Mapped[str | None] = mapped_column(String(120))
    story_time_end: Mapped[str | None] = mapped_column(String(120))
    story_order_start: Mapped[int | None] = mapped_column(Integer)
    story_order_end: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    traits_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    appearance_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    voice_traits_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )