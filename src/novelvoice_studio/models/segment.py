"""片段与音频资产。"""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now

EMOTION_VALUES = (
    "neutral",
    "happy",
    "excited",
    "angry",
    "sad",
    "fearful",
    "surprised",
    "doubtful",
    "sarcastic",
    "tender",
    "embarrassed",
    "determined",
    "tired",
)
DELIVERY_VALUES = (
    "normal",
    "whispering",
    "shouting",
    "crying",
    "laughing",
    "breathless",
    "slow",
    "hesitant",
)
_SEGMENT_TYPES = "('narration','dialogue','inner_monologue','unknown')"
_SPEAKER_TYPES = "('character','narrator','unknown')"
_AUDIO_STATUS = "('none','stale','queued','generating','ready','failed')"
_REVIEW_STATUS = "('auto','needs_review','approved')"
_EMOTIONS = "(" + ",".join(f"'{item}'" for item in EMOTION_VALUES) + ")"
_DELIVERIES = "(" + ",".join(f"'{item}'" for item in DELIVERY_VALUES) + ")"


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("chapter_id", "segment_index", name="uq_segments_chapter_index"),
        Index("ix_segments_speaker", "speaker_type", "speaker_id"),
        Index("ix_segments_book_review", "book_id", "review_status"),
        CheckConstraint(f"segment_type IN {_SEGMENT_TYPES}", name="ck_segments_type"),
        CheckConstraint(f"speaker_type IN {_SPEAKER_TYPES}", name="ck_segments_speaker_type"),
        CheckConstraint(
            f"audio_status IN {_AUDIO_STATUS}", name="ck_segments_audio_status"
        ),
        CheckConstraint(
            f"review_status IN {_REVIEW_STATUS}", name="ck_segments_review_status"
        ),
        CheckConstraint(f"emotion IN {_EMOTIONS}", name="ck_segments_emotion"),
        CheckConstraint(f"delivery IN {_DELIVERIES}", name="ck_segments_delivery"),
        CheckConstraint(
            "intensity >= 1 AND intensity <= 3", name="ck_segments_intensity"
        ),
        CheckConstraint("speed > 0 AND speed <= 3", name="ck_segments_speed"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    speech_text: Mapped[str] = mapped_column(Text, nullable=False)
    segment_type: Mapped[str] = mapped_column(
        String(24), default="unknown", nullable=False
    )
    speaker_type: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    speaker_id: Mapped[str | None] = mapped_column(String(32))
    speaker_name_snapshot: Mapped[str | None] = mapped_column(String(120))
    speaker_confidence: Mapped[float | None] = mapped_column(Float)
    voice_profile_id: Mapped[str | None] = mapped_column(String(32))
    emotion: Mapped[str] = mapped_column(String(32), default="neutral", nullable=False)
    emotion_confidence: Mapped[float | None] = mapped_column(Float)
    intensity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    delivery: Mapped[str] = mapped_column(String(24), default="normal", nullable=False)
    tts_instruction: Mapped[str | None] = mapped_column(Text)
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    pause_before_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pause_after_ms: Mapped[int] = mapped_column(Integer, default=250, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(24), default="needs_review", nullable=False
    )
    audio_status: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    current_audio_asset_id: Mapped[str | None] = mapped_column(String(32))
    user_locked_fields_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    text_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_start_offset: Mapped[int | None] = mapped_column(Integer)
    source_end_offset: Mapped[int | None] = mapped_column(Integer)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    audio_assets: Mapped[list["AudioAsset"]] = relationship(
        back_populates="segment",
        cascade="all, delete-orphan",
        order_by="AudioAsset.version",
    )


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    __table_args__ = (
        UniqueConstraint("segment_id", "version", name="uq_audio_assets_segment_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    segment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String(16), default="wav", nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sample_width_bits: Mapped[int] = mapped_column(Integer, default=16, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    tts_provider: Mapped[str | None] = mapped_column(String(64))
    tts_model: Mapped[str | None] = mapped_column(String(255))
    generation_params_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)

    segment: Mapped[Segment] = relationship(back_populates="audio_assets")