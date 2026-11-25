"""ORM -> API 模型的轻量序列化，避免路由里重复拼装。"""

from __future__ import annotations

from novelvoice_studio.models import Book, Chapter
from novelvoice_studio.schemas.books import BookDetail, BookSummary
from novelvoice_studio.schemas.chapters import ChapterDetail, ChapterSummary, ParagraphOut
from novelvoice_studio.services.chapters import ParagraphSlice


def to_book_summary(book: Book, chapter_count: int) -> BookSummary:
    return BookSummary(
        id=book.id,
        title=book.title,
        author=book.author,
        description=book.description,
        language=book.language,
        status=book.status,
        source_encoding=book.source_encoding,
        source_char_count=book.source_char_count,
        chapter_count=chapter_count,
        created_at=book.created_at,
        updated_at=book.updated_at,
    )


def to_book_detail(book: Book, chapter_count: int) -> BookDetail:
    return BookDetail(
        **to_book_summary(book, chapter_count).model_dump(),
        project_dir=book.project_dir,
        source_file_path=book.source_file_path,
        import_warnings=[str(item) for item in (book.import_warnings_json or [])],
        deleted_at=book.deleted_at,
    )


def to_chapter_summary(chapter: Chapter) -> ChapterSummary:
    return ChapterSummary(
        id=chapter.id,
        book_id=chapter.book_id,
        title=chapter.title,
        sequence=chapter.sequence,
        char_count=chapter.char_count,
        source_start_offset=chapter.source_start_offset,
        source_end_offset=chapter.source_end_offset,
        analysis_status=chapter.analysis_status,
        summary=chapter.summary,
        created_at=chapter.created_at,
        updated_at=chapter.updated_at,
    )


def to_chapter_detail(chapter: Chapter, text: str, paragraphs: list[ParagraphSlice]) -> ChapterDetail:
    return ChapterDetail(
        **to_chapter_summary(chapter).model_dump(),
        text=text,
        paragraphs=[
            ParagraphOut(
                index=item.index,
                start_offset=item.start,
                end_offset=item.end,
                text=item.text,
            )
            for item in paragraphs
        ],
        summary_json=chapter.summary_json or {},
        analysis_meta=chapter.analysis_meta_json or {},
    )