"""音色档案。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (
        CheckConstraint(
            "owner_type IN ('character','narrator')", name="ck_voice_profiles_owner_type"
        ),
        CheckConstraint(
            "status IN ('draft','ready','failed')", name="ck_voice_profiles_status"
        ),
        Index(
            "uq_voice_profiles_migrated_actor",
            "migrated_voice_actor_id",
            unique=True,
            sqlite_where=text("migrated_voice_actor_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    reference_text: Mapped[str | None] = mapped_column(Text)
    reference_audio_path: Mapped[str | None] = mapped_column(Text)
    instruct_text: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(Integer)
    generation_params_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float)
    user_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    migrated_voice_actor_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )