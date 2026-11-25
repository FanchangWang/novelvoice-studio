"""片段人工审核：筛选、编辑、批量修改与字段锁定。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import NotFoundError, ValidationError
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Book, Chapter, Character, Segment
from novelvoice_studio.schemas.analysis import (
    SegmentBatchUpdate,
    SegmentBatchUpdateResponse,
    SegmentListResponse,
    SegmentOut,
    SegmentUpdate,
)
from novelvoice_studio.services.analysis_serializers import to_segment_out
from novelvoice_studio.services.paths import validate_id

LOCKABLE_FIELDS = (
    "speech_text",
    "segment_type",
    "speaker_type",
    "speaker_id",
    "emotion",
    "intensity",
    "delivery",
    "tts_instruction",
    "speed",
    "pause_before_ms",
    "pause_after_ms",
)
_AUDIO_AFFECTING = {
    "speech_text",
    "speaker_type",
    "speaker_id",
    "emotion",
    "intensity",
    "delivery",
    "tts_instruction",
    "speed",
}


class SegmentService:
    def __init__(self, settings: Settings, session: Session) -> None:
        self.settings = settings
        self.session = session
        self.speaker_threshold = settings.analysis.speaker_confidence_threshold
        self.emotion_threshold = settings.analysis.emotion_confidence_threshold

    # -------------------------------------------------------------- helpers

    def _segment(self, segment_id: str) -> Segment:
        validate_id(segment_id, "seg")
        segment = self.session.get(Segment, segment_id)
        if segment is None:
            raise NotFoundError("片段不存在")
        return segment

    def _chapter(self, chapter_id: str) -> tuple[Chapter, Book]:
        validate_id(chapter_id, "ch")
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise NotFoundError("章节不存在")
        book = self.session.get(Book, chapter.book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("章节所属书籍不存在或已在回收站")
        return chapter, book

    # ----------------------------------------------------------------- read

    def list_for_chapter(
        self,
        chapter_id: str,
        *,
        review_status: str | None = None,
        segment_type: str | None = None,
        speaker_type: str | None = None,
        low_confidence: bool = False,
    ) -> SegmentListResponse:
        _chapter, _book = self._chapter(chapter_id)
        stmt = (
            select(Segment)
            .where(Segment.chapter_id == chapter_id)
            .order_by(Segment.segment_index.asc())
        )
        if review_status:
            stmt = stmt.where(Segment.review_status == review_status)
        if segment_type:
            stmt = stmt.where(Segment.segment_type == segment_type)
        if speaker_type:
            stmt = stmt.where(Segment.speaker_type == speaker_type)
        if low_confidence:
            stmt = stmt.where(Segment.speaker_confidence < self.speaker_threshold)
        rows = list(self.session.scalars(stmt))
        items = [
            to_segment_out(
                item,
                speaker_threshold=self.speaker_threshold,
                emotion_threshold=self.emotion_threshold,
            )
            for item in rows
        ]
        return SegmentListResponse(items=items, total=len(items), chapter_id=chapter_id)

    def get_segment(self, segment_id: str) -> SegmentOut:
        segment = self._segment(segment_id)
        self._chapter(segment.chapter_id)
        return to_segment_out(
            segment,
            speaker_threshold=self.speaker_threshold,
            emotion_threshold=self.emotion_threshold,
        )

    # --------------------------------------------------------------- update

    def _apply_update(self, segment: Segment, payload: SegmentUpdate) -> tuple[Segment, list[str]]:
        fields = payload.model_fields_set
        locks = {
            str(item) for item in (segment.user_locked_fields_json or [])
        }
        unknown_unlocks = set(payload.unlock_fields) - set(LOCKABLE_FIELDS)
        if unknown_unlocks:
            raise ValidationError(f"不可解锁的字段：{sorted(unknown_unlocks)}")
        locks -= set(payload.unlock_fields)
        content_fields = fields - {"unlock_fields", "review_status"}
        speaker_changed = False

        if "speaker_id" in content_fields and payload.speaker_id:
            character = self.session.get(Character, payload.speaker_id)
            if character is None or character.book_id != segment.book_id:
                raise ValidationError("说话角色不属于当前书籍")
            if character.merged_into_id:
                raise ValidationError("该角色已合并，请选择合并后的角色")
            segment.speaker_type = "character"
            segment.speaker_id = character.id
            segment.speaker_name_snapshot = character.canonical_name
            segment.speaker_confidence = 1.0
            locks.update({"speaker_type", "speaker_id"})
            speaker_changed = True

        if "speaker_type" in content_fields and payload.speaker_type:
            if payload.speaker_type == "character":
                if not payload.speaker_id and segment.speaker_type != "character":
                    raise ValidationError("选择角色说话时必须提供 speaker_id")
            else:
                segment.speaker_type = payload.speaker_type
                segment.speaker_id = None
                segment.speaker_name_snapshot = None
                segment.speaker_confidence = 1.0
            locks.update({"speaker_type", "speaker_id"})
            speaker_changed = True

        if speaker_changed:
            # 换说话人后，旧音色绑定不再可信；下次生成会重新选择当前 owner 的音色。
            segment.voice_profile_id = None

        if "speech_text" in content_fields and payload.speech_text is not None:
            text = payload.speech_text.strip()
            if not text:
                raise ValidationError("片段文本不能为空")
            if text != segment.speech_text:
                segment.speech_text = text
                segment.text_version += 1
                locks.add("speech_text")

        if "segment_type" in content_fields and payload.segment_type is not None:
            segment.segment_type = payload.segment_type
            locks.add("segment_type")

        if "emotion" in content_fields and payload.emotion is not None:
            segment.emotion = payload.emotion
            segment.emotion_confidence = 1.0
            locks.add("emotion")

        for field_name in ("intensity", "delivery", "tts_instruction", "speed"):
            if field_name in content_fields:
                value = getattr(payload, field_name)
                if value is not None:
                    setattr(segment, field_name, value)
                    locks.add(field_name)

        for field_name in ("pause_before_ms", "pause_after_ms"):
            if field_name in content_fields and getattr(payload, field_name) is not None:
                setattr(segment, field_name, getattr(payload, field_name))
                locks.add(field_name)

        if "review_status" in fields and payload.review_status is not None:
            segment.review_status = payload.review_status
            if payload.review_status == "approved":
                # 通过审核意味着当前所有表演字段都被人工确认。
                locks.update(LOCKABLE_FIELDS)

        if content_fields & _AUDIO_AFFECTING and segment.audio_status not in {"none"}:
            segment.audio_status = "stale"

        segment.user_locked_fields_json = sorted(locks)
        segment.updated_at = utc_now()
        return segment, sorted(locks)

    def update_segment(self, segment_id: str, payload: SegmentUpdate) -> SegmentOut:
        segment = self._segment(segment_id)
        self._chapter(segment.chapter_id)
        self._apply_update(segment, payload)
        self.session.commit()
        self.session.refresh(segment)
        return to_segment_out(
            segment,
            speaker_threshold=self.speaker_threshold,
            emotion_threshold=self.emotion_threshold,
        )

    def batch_update(self, payload: SegmentBatchUpdate) -> SegmentBatchUpdateResponse:
        unique_ids: list[str] = []
        for segment_id in payload.segment_ids:
            validate_id(segment_id, "seg")
            if segment_id not in unique_ids:
                unique_ids.append(segment_id)
        rows = list(
            self.session.scalars(select(Segment).where(Segment.id.in_(unique_ids)))
        )
        if len(rows) != len(unique_ids):
            raise NotFoundError("部分片段不存在")
        chapter_ids = {item.chapter_id for item in rows}
        if len(chapter_ids) != 1:
            raise ValidationError("批量修改仅支持同一章节内的片段")
        _chapter, _book = self._chapter(next(iter(chapter_ids)))
        for segment in sorted(rows, key=lambda item: item.segment_index):
            self._apply_update(segment, payload.update)
        self.session.commit()
        return SegmentBatchUpdateResponse(
            updated_count=len(rows),
            affected_segment_ids=[item.id for item in rows],
        )


def segment_counts(session: Session, chapter_id: str) -> tuple[int, int]:
    total = int(
        session.scalar(select(func.count(Segment.id)).where(Segment.chapter_id == chapter_id))
        or 0
    )
    review = int(
        session.scalar(
            select(func.count(Segment.id)).where(
                Segment.chapter_id == chapter_id,
                Segment.review_status == "needs_review",
            )
        )
        or 0
    )
    return total, review


def book_segment_counts(session: Session, book_id: str) -> tuple[int, int]:
    total = int(
        session.scalar(select(func.count(Segment.id)).where(Segment.book_id == book_id)) or 0
    )
    review = int(
        session.scalar(
            select(func.count(Segment.id)).where(
                Segment.book_id == book_id,
                Segment.review_status == "needs_review",
            )
        )
        or 0
    )
    return total, review