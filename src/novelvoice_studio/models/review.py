"""异常收件箱。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from novelvoice_studio.db.base import Base
from novelvoice_studio.db.types import UTCDateTime, utc_now

_SEVERITY = "('blocking','warning','info')"
_STATUS = "('open','resolved','ignored')"
_SOURCE_SQL = "('ai','legacy','user','system')"


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index("ix_review_items_book_status", "book_id", "status", "severity"),
        Index("ix_review_items_entity", "entity_type", "entity_id"),
        Index(
            "uq_review_items_open",
            "book_id",
            "entity_type",
            "entity_id",
            "code",
            unique=True,
            sqlite_where=text("status = 'open'"),
        ),
        CheckConstraint(f"severity IN {_SEVERITY}", name="ck_review_items_severity"),
        CheckConstraint(f"status IN {_STATUS}", name="ck_review_items_status"),
        CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_review_items_source"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )