"""有声剧本版本与剧本单元。"""

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
from novelvoice_studio.models.segment import DELIVERY_VALUES, EMOTION_VALUES

_SCRIPT_STATUS = "('draft','running','needs_review','approved','superseded','failed')"
_ADAPTATION = "('light','standard','strong','legacy')"
_UNIT_TYPES = "('narration','dialogue','inner_monologue','sfx','ambience','pause','unknown')"
_SPEAKER_TYPES = "('narrator','character','unknown','none')"
_REVIEW_STATUS = "('auto','needs_review','approved')"
_AUDIO_STATUS = "('not_applicable','none','stale','queued','generating','ready','failed')"
_SOURCE_SQL = "('ai','legacy','user','system')"
_EMOTIONS = "(" + ",".join(f"'{item}'" for item in EMOTION_VALUES) + ")"
_DELIVERIES = "(" + ",".join(f"'{item}'" for item in DELIVERY_VALUES) + ")"
_TTS_UNIT_SQL = "('narration','dialogue','inner_monologue')"
_CUE_UNIT_SQL = "('sfx','ambience','pause')"


class ScriptVersion(Base):
    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("chapter_id", "version", name="uq_script_versions_chapter_version"),
        Index(
            "uq_script_versions_active",
            "chapter_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        CheckConstraint(f"status IN {_SCRIPT_STATUS}", name="ck_script_versions_status"),
        CheckConstraint(
            f"adaptation_level IN {_ADAPTATION}", name="ck_script_versions_adaptation"
        ),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_script_versions_source"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    adaptation_level: Mapped[str] = mapped_column(
        String(16), default="light", nullable=False
    )
    source_scene_version: Mapped[int | None] = mapped_column(Integer)
    source_hash: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class ScriptUnit(Base):
    __tablename__ = "script_units"
    __table_args__ = (
        UniqueConstraint("script_version_id", "unit_index", name="uq_script_units_version_index"),
        Index("ix_script_units_version_type", "script_version_id", "unit_type"),
        Index("ix_script_units_book_audio", "book_id", "audio_status"),
        CheckConstraint(f"unit_type IN {_UNIT_TYPES}", name="ck_script_units_type"),
        CheckConstraint(f"speaker_type IN {_SPEAKER_TYPES}", name="ck_script_units_speaker_type"),
        CheckConstraint(f"review_status IN {_REVIEW_STATUS}", name="ck_script_units_review_status"),
        CheckConstraint(f"audio_status IN {_AUDIO_STATUS}", name="ck_script_units_audio_status"),
        CheckConstraint(f"emotion IN {_EMOTIONS}", name="ck_script_units_emotion"),
        CheckConstraint(f"delivery IN {_DELIVERIES}", name="ck_script_units_delivery"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_script_units_source"),
        CheckConstraint("intensity >= 1 AND intensity <= 3", name="ck_script_units_intensity"),
        CheckConstraint("speed > 0 AND speed <= 3", name="ck_script_units_speed"),
        CheckConstraint(
            "source_start_offset >= 0 AND source_end_offset > source_start_offset",
            name="ck_script_units_offsets",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_script_units_confidence"),
        CheckConstraint(
            f"unit_type NOT IN {_TTS_UNIT_SQL} OR length(trim(text)) > 0",
            name="ck_script_units_tts_text",
        ),
        CheckConstraint(
            f"unit_type NOT IN {_CUE_UNIT_SQL} OR audio_status = 'not_applicable'",
            name="ck_script_units_cue_audio",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_version_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("script_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("scenes.id", ondelete="SET NULL"), index=True
    )
    unit_index: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_type: Mapped[str] = mapped_column(String(24), nullable=False)
    speaker_type: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    speaker_id: Mapped[str | None] = mapped_column(String(32), index=True)
    speaker_name_snapshot: Mapped[str | None] = mapped_column(String(120))
    character_state_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("scene_characters.id", ondelete="SET NULL")
    )
    voice_cast_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("voice_casts.id", ondelete="SET NULL"), index=True
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    display_text: Mapped[str | None] = mapped_column(Text)
    source_start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    emotion: Mapped[str] = mapped_column(String(32), default="neutral", nullable=False)
    intensity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    delivery: Mapped[str] = mapped_column(String(24), default="normal", nullable=False)
    tts_instruction: Mapped[str | None] = mapped_column(Text)
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    pause_before_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pause_after_ms: Mapped[int] = mapped_column(Integer, default=250, nullable=False)
    sfx_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), default="auto", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    audio_status: Mapped[str] = mapped_column(String(24), default="none", nullable=False)
    current_audio_asset_id: Mapped[str | None] = mapped_column(String(32))
    render_hash: Mapped[str | None] = mapped_column(String(64))
    user_locked_fields_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )