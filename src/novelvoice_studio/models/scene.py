"""分场与场景内角色状态快照。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now
from novelvoice_studio.models.character import AGE_RANGE_VALUES

_TIME_MODES = "('present','flashback','flashforward','dream','unknown')"
_ANALYSIS_STATUS = "('draft','running','completed','needs_review','failed')"
_PRESENCE_VALUES = "('speaker','present','mentioned')"
_AGE_STAGES = "(" + ",".join(f"'{item}'" for item in AGE_RANGE_VALUES) + ")"
_SOURCE_SQL = "('ai','legacy','user','system')"


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint(
            "chapter_id", "analysis_version", "scene_index", name="uq_scenes_chapter_version_index"
        ),
        CheckConstraint(f"time_mode IN {_TIME_MODES}", name="ck_scenes_time_mode"),
        CheckConstraint(f"analysis_status IN {_ANALYSIS_STATUS}", name="ck_scenes_status"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_scenes_source"),
        CheckConstraint(
            "source_start_offset >= 0 AND source_end_offset > source_start_offset",
            name="ck_scenes_offsets",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_scenes_confidence"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    story_time_label: Mapped[str | None] = mapped_column(String(120))
    story_order: Mapped[int | None] = mapped_column(Integer)
    time_mode: Mapped[str] = mapped_column(String(24), default="present", nullable=False)
    pov_character_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("characters.id", ondelete="SET NULL")
    )
    mood: Mapped[str | None] = mapped_column(String(64))
    source_start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_status: Mapped[str] = mapped_column(
        String(24), default="draft", nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    meta_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class SceneCharacter(Base):
    __tablename__ = "scene_characters"
    __table_args__ = (
        UniqueConstraint(
            "scene_id", "character_id", "state_phase", name="uq_scene_characters_scene_character_phase"
        ),
        CheckConstraint(f"presence IN {_PRESENCE_VALUES}", name="ck_scene_characters_presence"),
        CheckConstraint(f"age_stage IN {_AGE_STAGES}", name="ck_scene_characters_age_stage"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_scene_characters_source"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_scene_characters_confidence"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    character_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state_phase: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    presence: Mapped[str] = mapped_column(String(16), default="present", nullable=False)
    age_stage: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    physical_state: Mapped[str | None] = mapped_column(String(255))
    emotional_state: Mapped[str | None] = mapped_column(String(255))
    relationship_state: Mapped[str | None] = mapped_column(String(255))
    knowledge_state: Mapped[str | None] = mapped_column(Text)
    disguise_state: Mapped[str | None] = mapped_column(String(255))
    voice_note: Mapped[str | None] = mapped_column(String(255))
    state_tags_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )