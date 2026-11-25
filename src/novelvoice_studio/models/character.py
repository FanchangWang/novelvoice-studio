"""角色与角色关系。"""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now

GENDER_VALUES = ("male", "female", "other", "unknown")
BASIS_VALUES = ("explicit", "inferred", "unknown")
AGE_RANGE_VALUES = (
    "child",
    "teenager",
    "young_adult",
    "adult",
    "middle_aged",
    "elderly",
    "unknown",
)
CHARACTER_TYPE_VALUES = ("human", "animal", "non_human", "unknown")


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        Index("ix_characters_book_name", "book_id", "canonical_name"),
        CheckConstraint(
            "gender IN ('male','female','other','unknown')", name="ck_characters_gender"
        ),
        CheckConstraint(
            "gender_basis IN ('explicit','inferred','unknown')",
            name="ck_characters_gender_basis",
        ),
        CheckConstraint(
            "age_range IN ('child','teenager','young_adult','adult','middle_aged','elderly','unknown')",
            name="ck_characters_age_range",
        ),
        CheckConstraint(
            "age_basis IN ('explicit','inferred','unknown')",
            name="ck_characters_age_basis",
        ),
        CheckConstraint(
            "character_type IN ('human','animal','non_human','unknown')",
            name="ck_characters_type",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_characters_confidence"
        ),
        CheckConstraint(
            "role_tier IN ('protagonist','major','minor','extra','unknown')",
            name="ck_characters_role_tier",
        ),
        CheckConstraint(
            "source IN ('ai','legacy','user')", name="ck_characters_source"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    canonical_name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    alias_candidates_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    gender: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    gender_basis: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    age_range: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    age_basis: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    character_type: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False
    )
    identity_description: Mapped[str | None] = mapped_column(Text)
    personality_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    personality_desc: Mapped[str | None] = mapped_column(Text)
    appearance_desc: Mapped[str | None] = mapped_column(Text)
    voice_description: Mapped[str | None] = mapped_column(Text)
    first_chapter_id: Mapped[str | None] = mapped_column(String(32))
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    role_tier: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    arc_summary: Mapped[str | None] = mapped_column(Text)
    story_time_start: Mapped[str | None] = mapped_column(String(120))
    story_time_end: Mapped[str | None] = mapped_column(String(120))
    story_order_start: Mapped[int | None] = mapped_column(Integer)
    story_order_end: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    user_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    merged_into_id: Mapped[str | None] = mapped_column(String(32), index=True)
    merge_log_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class CharacterRelationship(Base):
    __tablename__ = "character_relationships"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "source_character_id", "target_character_id",
            name="uq_relationship_pair",
        ),
        CheckConstraint(
            "source_character_id != target_character_id",
            name="ck_relationship_distinct",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_character_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    target_character_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )