"""书籍级业务操作。"""

from __future__ import annotations

import shutil

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    AudioAsset,
    Book,
    Chapter,
    Character,
    GenerationJob,
    ScriptUnit,
    Segment,
    VoiceActor,
    VoiceCast,
    VoiceProfile,
)
from novelvoice_studio.schemas.books import BookDetail, BookSummary, BookUpdate
from novelvoice_studio.services.paths import resolve_relative, validate_id
from novelvoice_studio.services.serializers import to_book_detail, to_book_summary


class BookService:
    def __init__(self, settings: Settings, session: Session) -> None:
        self.settings = settings
        self.session = session

    def list_books(
        self,
        *,
        include_deleted: bool = False,
        deleted_only: bool = False,
    ) -> tuple[list[BookSummary], int]:
        stmt = (
            select(Book, func.count(Chapter.id))
            .outerjoin(Chapter, Chapter.book_id == Book.id)
            .group_by(Book.id)
            .order_by(Book.updated_at.desc())
        )
        if deleted_only:
            stmt = stmt.where(Book.deleted_at.is_not(None))
        elif not include_deleted:
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

    def permanent_delete(self, book_id: str) -> None:
        """彻底删除回收站书籍、关联数据和本项目目录。"""

        book = self.get_book(book_id, include_deleted=True)
        if book.deleted_at is None:
            raise ConflictError(
                "请先将书籍移入回收站，再执行彻底删除",
                code="book_not_in_trash",
            )
        running = self.session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.book_id == book.id,
                GenerationJob.status.in_(("pending", "running")),
            )
        )
        if running:
            raise ConflictError(
                "书籍仍有运行中的任务，请等待任务结束或取消后重试",
                code="book_has_running_jobs",
                details={"jobs": int(running)},
            )

        # 目录名由受校验的 book_id 推导，绝不使用数据库中可能被篡改的 project_dir。
        books_root = (self.settings.data_dir / "books").resolve()
        project_root = resolve_relative(self.settings.data_dir, f"books/{book.id}")
        if project_root.parent != books_root or project_root.is_symlink():
            raise AppError(
                "书籍目录不安全，已拒绝彻底删除",
                status_code=400,
                code="unsafe_book_directory",
            )

        character_ids = list(
            self.session.scalars(
                select(Character.id).where(Character.book_id == book.id)
            )
        )

        # 先移除会因 SET NULL 违反 CHECK 的音频资产，再删除书籍本体。
        asset_conditions = [
            AudioAsset.segment_id.in_(
                select(Segment.id).where(Segment.book_id == book.id)
            ),
            AudioAsset.script_unit_id.in_(
                select(ScriptUnit.id).where(ScriptUnit.book_id == book.id)
            ),
        ]
        for asset in self.session.scalars(
            select(AudioAsset).where(or_(*asset_conditions))
        ):
            self.session.delete(asset)

        for cast in self.session.scalars(
            select(VoiceCast).where(VoiceCast.book_id == book.id)
        ):
            self.session.delete(cast)

        # 旧版 VoiceProfile 没有外键，需要按角色/旁白所有者显式清理。
        self.session.execute(
            VoiceProfile.__table__.delete().where(
                or_(
                    VoiceProfile.owner_id == book.id,
                    VoiceProfile.owner_id.in_(character_ids),
                )
            )
        )

        for actor in self.session.scalars(
            select(VoiceActor).where(VoiceActor.source_book_id == book.id)
        ):
            external_cast = self.session.scalar(
                select(VoiceCast.id).where(
                    VoiceCast.voice_actor_id == actor.id,
                    VoiceCast.book_id != book.id,
                ).limit(1)
            )
            if external_cast is not None:
                raise ConflictError(
                    "书籍专属配音员仍被其他书籍使用，已拒绝彻底删除",
                    code="book_voice_actor_in_use",
                    details={"voice_actor_id": actor.id},
                )
            self.session.delete(actor)

        self.session.delete(book)
        self.session.commit()

        if project_root.exists():
            try:
                shutil.rmtree(project_root)
            except OSError as exc:
                raise AppError(
                    "书籍记录已删除，但项目目录清理失败，请手动检查",
                    status_code=500,
                    code="book_directory_cleanup_failed",
                    details={"path": str(project_root)},
                ) from exc

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