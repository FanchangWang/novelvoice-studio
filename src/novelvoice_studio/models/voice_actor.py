"""全局配音员库与书内选角。"""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now
from novelvoice_studio.models.character import AGE_RANGE_VALUES

_GENDER = "('male','female','neutral','unknown')"
_AGE = "(" + ",".join(f"'{item}'" for item in AGE_RANGE_VALUES) + ")"
_STATUS = "('draft','ready','failed','archived')"
_CAST_SCOPE = "('narrator','character','character_stage')"
_CAST_SOURCE = "('auto','manual','legacy')"
_SOURCE_SQL = "('ai','legacy','user')"


class VoiceActor(Base):
    __tablename__ = "voice_actors"
    __table_args__ = (
        Index(
            "uq_voice_actors_legacy_profile",
            "legacy_voice_profile_id",
            unique=True,
            sqlite_where=text("legacy_voice_profile_id IS NOT NULL"),
        ),
        Index("ix_voice_actors_status_language", "status", "language"),
        Index("ix_voice_actors_age_gender", "age_impression", "gender_presentation"),
        CheckConstraint(f"gender_presentation IN {_GENDER}", name="ck_voice_actors_gender"),
        CheckConstraint(f"age_impression IN {_AGE}", name="ck_voice_actors_age"),
        CheckConstraint(f"status IN {_STATUS}", name="ck_voice_actors_status"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_voice_actors_source"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    gender_presentation: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False
    )
    age_impression: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN", nullable=False)
    timbre_tags_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    personality_tags_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reference_text: Mapped[str | None] = mapped_column(Text)
    reference_audio_path: Mapped[str | None] = mapped_column(Text)
    instruct_text: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    generation_params_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    source_book_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="SET NULL")
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    legacy_voice_profile_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class VoiceCast(Base):
    __tablename__ = "voice_casts"
    __table_args__ = (
        Index(
            "uq_voice_casts_narrator_active",
            "book_id",
            unique=True,
            sqlite_where=text("scope = 'narrator' AND is_active = 1"),
        ),
        Index(
            "uq_voice_casts_character_active",
            "book_id",
            "character_id",
            unique=True,
            sqlite_where=text("scope = 'character' AND is_active = 1"),
        ),
        Index(
            "uq_voice_casts_stage_active",
            "book_id",
            "character_id",
            "age_stage_condition",
            unique=True,
            sqlite_where=text("scope = 'character_stage' AND is_active = 1"),
        ),
        CheckConstraint(f"scope IN {_CAST_SCOPE}", name="ck_voice_casts_scope"),
        CheckConstraint(f"source IN {_CAST_SOURCE}", name="ck_voice_casts_source"),
        CheckConstraint(
            "(scope = 'narrator' AND character_id IS NULL AND age_stage_condition IS NULL) "
            "OR (scope = 'character' AND character_id IS NOT NULL AND age_stage_condition IS NULL) "
            "OR (scope = 'character_stage' AND character_id IS NOT NULL AND age_stage_condition IS NOT NULL)",
            name="ck_voice_casts_owner",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_voice_casts_confidence"),
        CheckConstraint(
            f"age_stage_condition IS NULL OR age_stage_condition IN {_AGE}",
            name="ck_voice_casts_age_condition",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    character_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    voice_actor_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("voice_actors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    age_stage_condition: Mapped[str | None] = mapped_column(String(24))
    state_condition: Mapped[str | None] = mapped_column(String(255))
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    legacy_voice_profile_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )