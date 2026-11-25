"""TXT 导入：编码处理、章节解析、落库与文件写入。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import ConflictError, UnsupportedFileError, ValidationError
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    AudioAsset,
    Book,
    Chapter,
    Character,
    CharacterRelationship,
    GenerationJob,
    PlaybackState,
    Segment,
    VoiceProfile,
)
from novelvoice_studio.schemas.books import (
    BookCreate,
    BookDetail,
    DuplicateBookInfo,
    EncodingCandidateOut,
    ImportPreviewResponse,
    ImportResult,
)
from novelvoice_studio.services import text_io
from novelvoice_studio.services.chapters import ChapterSlice, ParseOutcome, parse_chapters
from novelvoice_studio.services.paths import (
    BookPaths,
    atomic_write_bytes,
    atomic_write_text,
    book_paths,
    new_id,
    validate_id,
)
from novelvoice_studio.services.serializers import to_book_detail

MAX_PREVIEW_TITLES = 200
PREVIEW_SAMPLE_CHARS = 800


def _safe_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name:
        raise UnsupportedFileError("缺少文件名")
    if PurePosixPath(name).suffix.lower() != ".txt":
        raise UnsupportedFileError("仅支持 .txt 文件", details={"filename": name})
    return name


def _clean_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.replace("\x00", " ").split()).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


class ImportService:
    def __init__(self, settings: Settings, session: Session) -> None:
        self.settings = settings
        self.session = session

    # ---------- public ----------

    def preview(
        self,
        filename: str,
        data: bytes,
        *,
        encoding: str | None = None,
        custom_regex: str | None = None,
    ) -> ImportPreviewResponse:
        safe_name = _safe_filename(filename)
        self._check_size(data)
        detection = self._decode(data, encoding)
        parsed = parse_chapters(detection.text, custom_regex)
        warnings = self._collect_warnings(detection, parsed, encoding is not None)
        duplicate = self._find_duplicate(text_io.sha256_bytes(data))
        titles = [chapter.title for chapter in parsed.chapters]
        return ImportPreviewResponse(
            filename=safe_name,
            size_bytes=len(data),
            sha256=text_io.sha256_bytes(data),
            encoding=detection.encoding,
            encoding_label=detection.label,
            encoding_confidence=detection.confidence,
            needs_encoding_confirmation=detection.needs_confirmation,
            encoding_candidates=[
                EncodingCandidateOut(
                    encoding=item.encoding,
                    label=item.label,
                    confidence=item.confidence,
                    reason=item.reason,
                )
                for item in detection.candidates
            ],
            sample_text=detection.text[:PREVIEW_SAMPLE_CHARS],
            total_chars=len(detection.text),
            line_count=len(detection.text.splitlines()) or 1,
            chapter_count=len(parsed.chapters),
            chapter_titles=titles[:MAX_PREVIEW_TITLES],
            chapters_truncated=len(titles) > MAX_PREVIEW_TITLES,
            warnings=warnings,
            duplicate=(
                DuplicateBookInfo(
                    id=duplicate.id,
                    title=duplicate.title,
                    created_at=duplicate.created_at,
                )
                if duplicate
                else None
            ),
        )

    def import_book(
        self,
        filename: str,
        data: bytes,
        *,
        encoding: str | None = None,
        title: str | None = None,
        author: str | None = None,
        description: str | None = None,
        language: str = "zh-CN",
        custom_regex: str | None = None,
        duplicate_policy: str = "new",
        replace_book_id: str | None = None,
    ) -> ImportResult:
        safe_name = _safe_filename(filename)
        self._check_size(data)
        if duplicate_policy not in {"new", "reject", "replace"}:
            raise ValidationError("duplicate_policy 只能是 new / reject / replace")

        detection = self._decode(data, encoding)
        if detection.needs_confirmation and encoding is None:
            raise ValidationError(
                "编码识别置信度较低，请先预览并手动选择编码",
                code="encoding_confirmation_required",
                details={
                    "encoding": detection.encoding,
                    "candidates": [
                        {"encoding": item.encoding, "label": item.label, "confidence": item.confidence}
                        for item in detection.candidates
                    ],
                },
            )
        parsed = parse_chapters(detection.text, custom_regex)
        if not parsed.chapters:
            raise ValidationError("未能解析出任何章节")

        sha256 = text_io.sha256_bytes(data)
        duplicate = self._find_duplicate(sha256)
        book_id: str
        replacing = False
        target: Book | None = None

        if duplicate is not None:
            if duplicate_policy == "reject":
                raise ConflictError(
                    "检测到重复导入的书籍",
                    code="duplicate_book",
                    details={"book_id": duplicate.id, "title": duplicate.title},
                )
            if duplicate_policy == "replace":
                if not replace_book_id:
                    raise ValidationError(
                        "覆盖导入必须指定 replace_book_id",
                        code="replace_book_id_required",
                        details={"book_id": duplicate.id, "title": duplicate.title},
                    )
                target = self._resolve_replace_target(sha256, replace_book_id)
                replacing = True

        if target is None:
            book_id = new_id("book")
            paths = book_paths(self.settings.data_dir, book_id)
            if paths.root.exists():
                raise ConflictError("书籍目录已存在，请重试")
        else:
            book_id = target.id
            paths = book_paths(self.settings.data_dir, book_id)
            self._ensure_replace_is_safe(target)

        warnings = self._collect_warnings(detection, parsed, encoding is not None)
        final_title = (
            _clean_text(title, 255)
            or _clean_text(Path(safe_name).stem, 255)
            or "未命名书籍"
        )

        moved_trash: Path | None = None
        root_created = False
        try:
            if replacing and paths.root.exists():
                moved_trash = self._move_to_trash(paths)
            paths.ensure()
            root_created = True
            atomic_write_bytes(paths.original_path, data)
            atomic_write_text(paths.normalized_path, detection.text)

            if replacing:
                self._clear_book_content(book_id)
                book = self.session.get(Book, book_id)
                if book is None:
                    raise ConflictError("待覆盖的书籍不存在")
            else:
                book = Book(id=book_id, title=final_title, project_dir=paths.relative_root())
                self.session.add(book)

            book.title = final_title
            if author is not None:
                book.author = _clean_text(author, 255)
            if description is not None:
                book.description = _clean_text(description, 5000)
            book.language = (language or "zh-CN")[:16]
            book.status = "ready"
            book.project_dir = paths.relative_root()
            book.source_file_path = paths.relative_original()
            book.source_encoding = detection.encoding
            book.source_char_count = len(detection.text)
            book.source_sha256 = sha256
            book.import_warnings_json = warnings
            book.deleted_at = None
            book.updated_at = utc_now()
            self.session.flush()

            chapter_rows: list[Chapter] = []
            for sequence, item in enumerate(parsed.chapters, start=1):
                chapter = Chapter(
                    id=new_id("ch"),
                    book_id=book.id,
                    title=item.title[:255] or f"第 {sequence} 章",
                    sequence=sequence,
                    source_start_offset=item.start,
                    source_end_offset=item.end,
                    analysis_status="not_started",
                )
                chapter_rows.append(chapter)
                self.session.add(chapter)
            self.session.flush()

            self._write_manifest(
                paths=paths,
                book=book,
                filename=safe_name,
                data=data,
                detection=detection,
                parsed=parsed,
                chapters=chapter_rows,
                warnings=warnings,
                replaced=replacing,
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            if root_created:
                shutil.rmtree(paths.root, ignore_errors=True)
            if moved_trash is not None and not paths.root.exists():
                paths.root.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_trash), str(paths.root))
            raise

        self.session.refresh(book)
        return ImportResult(
            book=to_book_detail(book, len(parsed.chapters)),
            chapters_created=len(parsed.chapters),
            encoding=detection.encoding,
            warnings=warnings,
            replaced=replacing,
        )

    def create_empty_book(self, payload: BookCreate) -> BookDetail:
        book_id = new_id("book")
        paths = book_paths(self.settings.data_dir, book_id)
        paths.ensure()
        title = _clean_text(payload.title, 255) or "未命名书籍"
        book = Book(
            id=book_id,
            title=title,
            author=_clean_text(payload.author, 255),
            description=_clean_text(payload.description, 5000),
            language=(payload.language or "zh-CN")[:16],
            status="empty",
            project_dir=paths.relative_root(),
            source_char_count=0,
            import_warnings_json=["空项目：请导入 TXT 或后续手动追加章节。"],
        )
        self.session.add(book)
        self.session.commit()
        self.session.refresh(book)
        return to_book_detail(book, 0)

    # ---------- internals ----------

    def _check_size(self, data: bytes) -> None:
        if not data:
            raise ValidationError("文件为空")
        limit = max(1, self.settings.app.max_upload_mb) * 1024 * 1024
        if len(data) > limit:
            raise ValidationError(
                f"文件超过大小限制（{self.settings.app.max_upload_mb} MB）",
                status_code=413,
                code="file_too_large",
            )

    def _decode(self, data: bytes, encoding: str | None):
        if encoding:
            text, detection = text_io.decode_with(data, encoding)
            return detection
        return text_io.detect_and_decode(data)

    def _collect_warnings(self, detection, parsed: ParseOutcome, manual_encoding: bool) -> list[str]:
        warnings: list[str] = []
        if manual_encoding:
            warnings.append(f"编码由用户指定为 {detection.label}。")
        elif detection.has_bom:
            warnings.append(f"检测到 {detection.label}，已自动去除 BOM。")
        elif detection.needs_confirmation:
            warnings.append(
                f"编码 {detection.label} 置信度较低（{detection.confidence:.2f}），建议人工确认。"
            )
        warnings.extend(parsed.warnings)
        return warnings

    def _find_duplicate(self, sha256: str) -> Book | None:
        return self.session.scalar(
            select(Book)
            .where(Book.source_sha256 == sha256, Book.deleted_at.is_(None))
            .order_by(Book.created_at.desc())
            .limit(1)
        )

    def _resolve_replace_target(self, sha256: str, replace_book_id: str) -> Book:
        validate_id(replace_book_id, "book")
        target = self.session.get(Book, replace_book_id)
        if target is None or target.deleted_at is not None:
            raise ValidationError(
                "待覆盖的书籍不存在或已在回收站",
                code="replace_book_not_found",
                details={"book_id": replace_book_id},
            )
        if target.source_sha256 != sha256:
            raise ValidationError(
                "待覆盖书籍的内容与上传文件不一致，拒绝覆盖",
                code="replace_content_mismatch",
                details={"book_id": replace_book_id},
            )
        return target

    def _ensure_replace_is_safe(self, book: Book) -> None:
        asset_count = self.session.scalar(
            select(func.count(AudioAsset.id))
            .join(Segment, AudioAsset.segment_id == Segment.id)
            .join(Chapter, Segment.chapter_id == Chapter.id)
            .where(Chapter.book_id == book.id)
        )
        if asset_count:
            raise ConflictError(
                "该书已存在音频资产，拒绝覆盖导入；请先另存为新书。",
                code="replace_blocked_by_audio",
                details={"book_id": book.id, "audio_assets": int(asset_count)},
            )
        running = self.session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.book_id == book.id,
                GenerationJob.status.in_(("pending", "running")),
            )
        )
        if running:
            raise ConflictError(
                "该书仍有未完成的分析/生成任务，拒绝覆盖导入。",
                code="replace_blocked_by_jobs",
                details={"book_id": book.id, "jobs": int(running)},
            )

    def _move_to_trash(self, paths: BookPaths) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        trash_dir = self.settings.data_dir / "trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        target = trash_dir / f"{paths.book_id}-{stamp}"
        counter = 1
        while target.exists():
            target = trash_dir / f"{paths.book_id}-{stamp}-{counter}"
            counter += 1
        shutil.move(str(paths.root), str(target))
        return target

    def _clear_book_content(self, book_id: str) -> None:
        character_ids = list(
            self.session.scalars(select(Character.id).where(Character.book_id == book_id))
        )
        self.session.execute(delete(Chapter).where(Chapter.book_id == book_id))
        self.session.execute(
            delete(CharacterRelationship).where(CharacterRelationship.book_id == book_id)
        )
        self.session.execute(delete(Character).where(Character.book_id == book_id))
        if character_ids:
            self.session.execute(
                delete(VoiceProfile).where(
                    VoiceProfile.owner_type == "character",
                    VoiceProfile.owner_id.in_(character_ids),
                )
            )
        self.session.execute(
            delete(VoiceProfile).where(
                VoiceProfile.owner_type == "narrator", VoiceProfile.owner_id == book_id
            )
        )
        self.session.execute(delete(GenerationJob).where(GenerationJob.book_id == book_id))
        self.session.execute(delete(PlaybackState).where(PlaybackState.book_id == book_id))
        self.session.flush()

    def _write_manifest(
        self,
        *,
        paths: BookPaths,
        book: Book,
        filename: str,
        data: bytes,
        detection,
        parsed: ParseOutcome,
        chapters: list[Chapter],
        warnings: list[str],
        replaced: bool,
    ) -> None:
        manifest = {
            "schema_version": 1,
            "book_id": book.id,
            "title": book.title,
            "imported_at": utc_now().isoformat(),
            "original_filename": filename,
            "replaced": replaced,
            "source": {
                "original": paths.relative_original(),
                "normalized": paths.relative_normalized(),
                "encoding": detection.encoding,
                "encoding_label": detection.label,
                "encoding_confidence": detection.confidence,
                "sha256": text_io.sha256_bytes(data),
                "size_bytes": len(data),
                "char_count": len(detection.text),
                "line_count": len(detection.text.splitlines()) or 1,
            },
            "chapters": [
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "sequence": chapter.sequence,
                    "start": chapter.source_start_offset,
                    "end": chapter.source_end_offset,
                }
                for chapter in chapters
            ],
            "warnings": warnings,
        }
        atomic_write_text(
            paths.manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )