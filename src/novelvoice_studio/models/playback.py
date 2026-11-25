"""播放状态。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now


class PlaybackState(Base):
    __tablename__ = "playback_states"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(32))
    segment_id: Mapped[str | None] = mapped_column(String(32))
    position_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )