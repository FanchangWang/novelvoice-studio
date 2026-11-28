"""章节读取、重命名、拆分与合并。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from novelvoice_studio.models import Book, Chapter, Segment
from novelvoice_studio.schemas.chapters import ChapterDetail, ChapterSummary
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.chapters import split_paragraphs
from novelvoice_studio.services.paths import new_id, validate_id
from novelvoice_studio.services.serializers import to_chapter_detail, to_chapter_summary


class ChapterService:
    def __init__(self, settings: Settings, session: Session) -> None:
        self.settings = settings
        self.session = session
        self.books = BookService(settings, session)

    # ---------- read ----------

    def list_for_book(self, book: Book) -> list[Chapter]:
        return list(
            self.session.scalars(
                select(Chapter)
                .where(Chapter.book_id == book.id)
                .order_by(Chapter.sequence.asc())
            )
        )

    def summaries_for_book(self, book: Book) -> list[ChapterSummary]:
        return [to_chapter_summary(item) for item in self.list_for_book(book)]

    def page_for_book(
        self,
        book: Book,
        *,
        page: int = 1,
        page_size: int = 20,
        kind: str | None = None,
    ) -> tuple[list[ChapterSummary], int]:
        conditions = [Chapter.book_id == book.id]
        if kind is not None:
            if kind not in {"story", "extra", "author_note", "unclassified"}:
                raise ValidationError("章节分类筛选无效")
            conditions.append(Chapter.chapter_kind == kind)
        total = int(
            self.session.scalar(select(func.count(Chapter.id)).where(*conditions)) or 0
        )
        rows = self.session.scalars(
            select(Chapter)
            .where(*conditions)
            .order_by(Chapter.sequence.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [to_chapter_summary(item) for item in rows], total

    def get_with_book(self, chapter_id: str) -> tuple[Chapter, Book]:
        validate_id(chapter_id, "ch")
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise NotFoundError("章节不存在")
        book = self.session.get(Book, chapter.book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("章节所属书籍不存在或已在回收站")
        return chapter, book

    def detail(self, chapter: Chapter, book: Book) -> ChapterDetail:
        text = self.books.read_source(book)
        start, end = chapter.source_start_offset, chapter.source_end_offset
        if start < 0 or end < start or end > len(text):
            raise AppError(
                "章节偏移与源文本不一致，可能文件已被修改",
                status_code=500,
                code="source_offset_mismatch",
                details={
                    "chapter_id": chapter.id,
                    "start": start,
                    "end": end,
                    "source_length": len(text),
                },
            )
        paragraphs = split_paragraphs(text, start, end)
        return to_chapter_detail(chapter, text[start:end], paragraphs)

    # ---------- write ----------

    def update_title(self, chapter_id: str, title: str | None = None,
                     chapter_kind: str | None = None,
                     analysis_included: bool | None = None,
                     classification_status: str | None = None) -> ChapterSummary:
        chapter, _book = self.get_with_book(chapter_id)
        if title is not None:
            cleaned = title.strip()
            if not cleaned:
                raise ValidationError("章节标题不能为空")
            chapter.title = cleaned[:255]
        if chapter_kind is not None:
            if chapter_kind not in {"story", "extra", "author_note", "unclassified"}:
                raise ValidationError("章节分类无效")
            chapter.chapter_kind = chapter_kind
            chapter.classification_status = "confirmed"
            if chapter_kind == "unclassified":
                chapter.analysis_included = False
        if classification_status is not None:
            if classification_status not in {"ai_suggested", "confirmed", "unclassified"}:
                raise ValidationError("章节分类状态无效")
            chapter.classification_status = classification_status
            if classification_status == "unclassified":
                chapter.chapter_kind = "unclassified"
                chapter.analysis_included = False
        if analysis_included is not None:
            chapter.analysis_included = bool(analysis_included)
            if chapter.analysis_included and chapter.chapter_kind == "unclassified":
                raise ValidationError("未分类章节不能纳入分析，请先确认章节类型")
        self.session.commit()
        self.session.refresh(chapter)
        return to_chapter_summary(chapter)

    def split(
        self,
        chapter_id: str,
        paragraph_index: int,
        new_title: str | None = None,
    ) -> tuple[Chapter, list[ChapterSummary]]:
        chapter, book = self.get_with_book(chapter_id)
        self._ensure_no_segments([chapter.id])
        text = self.books.read_source(book)
        if chapter.source_end_offset > len(text):
            raise AppError("章节偏移越界，无法拆分", status_code=500, code="source_offset_mismatch")
        paragraphs = split_paragraphs(
            text, chapter.source_start_offset, chapter.source_end_offset
        )
        if paragraph_index < 1 or paragraph_index >= len(paragraphs):
            raise ValidationError(
                "拆分段落位置无效",
                code="invalid_split_index",
                details={"paragraph_count": len(paragraphs)},
            )
        split_offset = paragraphs[paragraph_index].start
        old_end = chapter.source_end_offset
        if split_offset <= chapter.source_start_offset or split_offset >= old_end:
            raise ValidationError("拆分位置无效")

        later = list(
            self.session.scalars(
                select(Chapter)
                .where(Chapter.book_id == book.id, Chapter.sequence > chapter.sequence)
                .order_by(Chapter.sequence.desc())
            )
        )
        self._shift_sequences(later, 1)

        chapter.source_end_offset = split_offset
        chapter.source_hash = None
        chapter.analysis_status = "not_started"
        chapter.summary = None
        chapter.summary_json = {}
        chapter.analysis_meta_json = {}
        title = (new_title or f"{chapter.title}（续）").strip()[:255]
        created = Chapter(
            id=new_id("ch"),
            book_id=book.id,
            title=title,
            sequence=chapter.sequence + 1,
            volume_number=chapter.volume_number,
            volume_title=chapter.volume_title,
            chapter_kind=chapter.chapter_kind,
            classification_status=chapter.classification_status,
            analysis_included=chapter.analysis_included,
            source_start_offset=split_offset,
            source_end_offset=old_end,
            analysis_status="not_started",
        )
        self.session.add(created)
        self.session.commit()
        self.session.refresh(chapter)
        self.session.refresh(created)
        return created, self.summaries_for_book(book)

    def merge(
        self,
        book: Book,
        chapter_ids: list[str],
        new_title: str | None = None,
    ) -> tuple[Chapter, list[str], list[ChapterSummary]]:
        unique_ids: list[str] = []
        for chapter_id in chapter_ids:
            validate_id(chapter_id, "ch")
            if chapter_id not in unique_ids:
                unique_ids.append(chapter_id)
        if len(unique_ids) < 2:
            raise ValidationError("至少选择两个不同章节进行合并")

        chapters = list(
            self.session.scalars(
                select(Chapter).where(Chapter.id.in_(unique_ids))
            )
        )
        if len(chapters) != len(unique_ids):
            raise NotFoundError("部分章节不存在")
        if any(item.book_id != book.id for item in chapters):
            raise ValidationError("只能合并同一本书的章节")
        chapters.sort(key=lambda item: item.sequence)
        sequences = [item.sequence for item in chapters]
        if sequences != list(range(sequences[0], sequences[0] + len(sequences))):
            raise ValidationError("只能合并相邻章节")
        self._ensure_no_segments([item.id for item in chapters])

        target = chapters[0]
        last = chapters[-1]
        removed_ids = [item.id for item in chapters[1:]]
        for item in chapters[1:]:
            self.session.delete(item)
        self.session.flush()

        target.source_end_offset = last.source_end_offset
        target.source_hash = None
        target.title = (new_title or target.title).strip()[:255]
        target.analysis_status = "not_started"
        target.summary = None
        target.summary_json = {}
        target.analysis_meta_json = {}

        subsequent = list(
            self.session.scalars(
                select(Chapter)
                .where(Chapter.book_id == book.id, Chapter.sequence > last.sequence)
                .order_by(Chapter.sequence.asc())
            )
        )
        shift = len(chapters) - 1
        self._shift_sequences(subsequent, -shift)
        self.session.commit()
        self.session.refresh(target)
        return target, removed_ids, self.summaries_for_book(book)

    def _shift_sequences(self, chapters: list[Chapter], delta: int) -> None:
        """安全调整序号。

        SQLite 的唯一约束是立即检查的，而 SQLAlchemy 批量 UPDATE 不保证按
        我们期望的顺序执行；直接逐条 +1/-1 可能撞上尚未更新的相邻序号。
        先把整组序号抬到书本尾部之上的临时区间，再落到最终值，可避免中间态冲突。
        """
        if not chapters or delta == 0:
            return
        max_sequence = max(item.sequence for item in chapters)
        offset = max_sequence + len(chapters) + 2
        for item in chapters:
            item.sequence += offset
        self.session.flush()
        for item in chapters:
            item.sequence += delta - offset
        self.session.flush()

    def _ensure_no_segments(self, chapter_ids: list[str]) -> None:
        count = self.session.scalar(
            select(func.count(Segment.id)).where(Segment.chapter_id.in_(chapter_ids))
        )
        if count:
            raise ConflictError(
                "章节已生成片段或音频，暂不支持拆分/合并；请先删除相关片段。",
                code="chapter_has_segments",
                details={"segments": int(count)},
            )