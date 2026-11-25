"""书籍级业务操作。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import AppError, NotFoundError, ValidationError
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Book, Chapter
from novelvoice_studio.schemas.books import BookDetail, BookSummary, BookUpdate
from novelvoice_studio.services.paths import resolve_relative, validate_id
from novelvoice_studio.services.serializers import to_book_detail, to_book_summary


class BookService:
    def __init__(self, settings: Settings, session: Session) -> None:
        self.settings = settings
        self.session = session

    def list_books(self, *, include_deleted: bool = False) -> tuple[list[BookSummary], int]:
        stmt = (
            select(Book, func.count(Chapter.id))
            .outerjoin(Chapter, Chapter.book_id == Book.id)
            .group_by(Book.id)
            .order_by(Book.updated_at.desc())
        )
        if not include_deleted:
            stmt = stmt.where(Book.deleted_at.is_(None))
        rows = self.session.execute(stmt).all()
        items = [to_book_summary(book, int(count)) for book, count in rows]
        return items, len(items)

    def get_book(self, book_id: str, *, include_deleted: bool = False) -> Book:
        validate_id(book_id, "book")
        book = self.session.get(Book, book_id)
        if book is None:
            raise NotFoundError("书籍不存在")
        if book.deleted_at is not None and not include_deleted:
            raise NotFoundError("书籍已在回收站")
        return book

    def chapter_count(self, book_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count(Chapter.id)).where(Chapter.book_id == book_id)
            )
            or 0
        )

    def detail(self, book: Book) -> BookDetail:
        return to_book_detail(book, self.chapter_count(book.id))

    def update(self, book_id: str, payload: BookUpdate) -> BookDetail:
        book = self.get_book(book_id)
        fields = payload.model_fields_set
        if "title" in fields:
            if payload.title is None:
                raise ValidationError("书名不能为空")
            book.title = payload.title.strip()
        if "author" in fields:
            book.author = payload.author.strip() if payload.author else None
        if "description" in fields:
            book.description = payload.description.strip() if payload.description else None
        if "language" in fields:
            if not payload.language:
                raise ValidationError("语言不能为空")
            book.language = payload.language
        book.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(book)
        return self.detail(book)

    def soft_delete(self, book_id: str) -> None:
        book = self.get_book(book_id)
        if book.deleted_at is not None:
            return
        book.deleted_at = utc_now()
        book.status = "deleted"
        book.updated_at = utc_now()
        self.session.commit()

    def restore(self, book_id: str) -> BookDetail:
        book = self.get_book(book_id, include_deleted=True)
        if book.deleted_at is not None:
            book.deleted_at = None
            book.status = "ready" if self.chapter_count(book.id) else "empty"
            book.updated_at = utc_now()
            self.session.commit()
            self.session.refresh(book)
        return self.detail(book)

    def read_source(self, book: Book) -> str:
        if not book.source_file_path:
            if book.status == "empty":
                return ""
            raise AppError(
                "书籍缺少源文本文件",
                status_code=500,
                code="source_file_missing",
                details={"book_id": book.id},
            )
        path = resolve_relative(self.settings.data_dir, book.source_file_path)
        normalized = path.with_name("normalized.txt")
        if normalized.is_file():
            path = normalized
        elif not path.is_file():
            raise AppError(
                "书籍源文本文件不存在，可能已被移动或删除",
                status_code=500,
                code="source_file_missing",
                details={"book_id": book.id},
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(
                "源文本不是 UTF-8，可能缺少 normalized.txt",
                status_code=500,
                code="source_encoding_invalid",
                details={"book_id": book.id},
            ) from exc