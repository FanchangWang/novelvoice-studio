"""TXT 导入：编码处理、章节解析、落库与文件写入。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from novelvoice_studio.ai.registry import ProviderRegistry
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
    ReviewItem,
    Scene,
    ScriptUnit,
    ScriptVersion,
    Segment,
    VoiceProfile,
)
from novelvoice_studio.schemas.books import (
    BookCreate,
    BookDetail,
    BookMetadataOut,
    DuplicateBookInfo,
    EncodingCandidateOut,
    ImportPreviewResponse,
    ImportResult,
)
from novelvoice_studio.services import text_io
from novelvoice_studio.services.book_ids import allocate_book_id
from novelvoice_studio.services.book_metadata import BookMetadata
from novelvoice_studio.services.chapters import (
    ChapterSlice,
    ParseOutcome,
    StructuralHeading,
    normalize_chapter_title,
    parse_chapters,
)
from novelvoice_studio.services.import_structure import analyze_import
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


@dataclass(frozen=True)
class _MergePlan:
    text: str
    parsed: ParseOutcome
    matched: dict[str, ChapterSlice]
    old_hashes: dict[str, str]
    old_contents: dict[str, str]
    appended_only: bool


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _metadata_out(metadata: BookMetadata) -> BookMetadataOut:
    return BookMetadataOut(
        title=metadata.title,
        author=metadata.author,
        description=metadata.description,
        title_source=metadata.title_source,
        author_source=metadata.author_source,
        description_source=metadata.description_source,
        confidence=metadata.confidence,
        warnings=list(metadata.warnings),
    )


class ImportService:
    def __init__(
        self,
        settings: Settings,
        session: Session,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.session = session
        self.registry = registry

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
        analysis = analyze_import(self.registry, detection.text, safe_name, custom_regex=custom_regex)
        parsed = analysis.parsed
        metadata = analysis.metadata
        warnings = self._collect_warnings(detection, parsed, encoding is not None)
        warnings.extend(analysis.warnings)
        warnings.extend(metadata.warnings)
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
            structure_source=analysis.structure_source,
            ai_assisted=analysis.ai_assisted,
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
            metadata=_metadata_out(metadata),
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
        if duplicate_policy not in {"new", "reject", "replace", "merge"}:
            raise ValidationError("duplicate_policy 只能是 new / reject / replace / merge")

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
        analysis = analyze_import(
            self.registry,
            detection.text,
            safe_name,
            custom_regex=custom_regex,
            book_title=_clean_text(title, 255),
        )
        uploaded_parsed = analysis.parsed
        if not uploaded_parsed.chapters:
            raise ValidationError("未能解析出任何章节")

        sha256 = text_io.sha256_bytes(data)
        duplicate = self._find_duplicate(sha256)
        replacing = False
        merging = duplicate_policy == "merge"
        target: Book | None = None
        merge_plan: _MergePlan | None = None

        if duplicate is not None and duplicate_policy == "reject":
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
                    details={"book_id": duplicate.id if duplicate else None,
                             "title": duplicate.title if duplicate else None},
                )
            target = self._resolve_target(replace_book_id)
            replacing = True
        elif merging:
            target_id = replace_book_id or (duplicate.id if duplicate else None)
            if not target_id:
                raise ValidationError(
                    "增量导入必须指定目标书籍",
                    code="merge_book_id_required",
                )
            target = self._resolve_target(target_id)
            self._ensure_merge_is_safe(target)
            merge_plan = self._plan_merge(target, detection.text, uploaded_parsed, custom_regex)

        if target is None:
            book_id = allocate_book_id(self.session)
            paths = book_paths(self.settings.data_dir, book_id)
            if paths.root.exists():
                raise ConflictError("书籍目录已存在，请重试")
        else:
            book_id = target.id
            paths = book_paths(self.settings.data_dir, book_id)
            if replacing:
                self._ensure_replace_is_safe(target)

        parsed = merge_plan.parsed if merge_plan is not None else uploaded_parsed
        metadata = analysis.metadata
        warnings = self._collect_warnings(detection, parsed, encoding is not None)
        warnings.extend(analysis.warnings)
        warnings.extend(metadata.warnings)
        if merge_plan is not None:
            warnings.append(
                "已识别为新增章节片段并追加到原书。"
                if merge_plan.appended_only
                else "已按完整新版智能合并；未变化章节保留原分析、选角与音频。"
            )
        final_title = (
            _clean_text(title, 255)
            or metadata.title
            or "未命名书籍"
        )
        final_author = _clean_text(author, 255) or metadata.author
        final_description = _clean_text(description, 5000) or metadata.description
        if target is not None:
            explicit_title = _clean_text(title, 255) if replacing else None
            explicit_author = _clean_text(author, 255) if replacing else None
            explicit_description = _clean_text(description, 5000) if replacing else None
            final_title = explicit_title or target.title or final_title
            final_author = explicit_author or target.author or final_author
            final_description = explicit_description or target.description or final_description

        moved_trash: Path | None = None
        root_created = False
        merge_backups: dict[Path, bytes] = {}
        effective_text = merge_plan.text if merge_plan is not None else detection.text
        source_data = (
            effective_text.encode(detection.encoding)
            if merge_plan is not None
            else data
        )
        source_sha256 = text_io.sha256_bytes(source_data)
        try:
            if replacing and paths.root.exists():
                moved_trash = self._move_to_trash(paths)
            root_created = not paths.root.exists()
            if merging:
                for path in (paths.original_path, paths.normalized_path, paths.manifest_path):
                    if path.exists():
                        merge_backups[path] = path.read_bytes()
            paths.ensure()
            atomic_write_bytes(paths.original_path, source_data)
            atomic_write_text(paths.normalized_path, effective_text)

            if replacing:
                self._clear_book_content(book_id)
                book = self.session.get(Book, book_id)
                if book is None:
                    raise ConflictError("待覆盖的书籍不存在")
            elif merging:
                book = target
            else:
                book = Book(id=book_id, title=final_title, project_dir=paths.relative_root())
                self.session.add(book)
            if book is None:
                raise ConflictError("目标书籍不存在")

            book.title = final_title
            if book.author is None:
                book.author = final_author
            if book.description is None:
                book.description = final_description
            book.language = (language or "zh-CN")[:16]
            book.status = "ready"
            book.project_dir = paths.relative_root()
            book.source_file_path = paths.relative_original()
            book.source_encoding = detection.encoding
            book.source_char_count = len(effective_text)
            book.source_sha256 = source_sha256
            book.import_warnings_json = warnings
            book.deleted_at = None
            book.updated_at = utc_now()
            self.session.flush()

            if merge_plan is not None:
                chapter_rows, stats = self._apply_merge(book, merge_plan)
                if stats["created"] or stats["changed"]:
                    book.bible_status = "needs_review"
                    book.production_status = "needs_review"
            else:
                chapter_rows = []
                for sequence, item in enumerate(parsed.chapters, start=1):
                    chapter = Chapter(
                        id=new_id("ch"),
                        book_id=book.id,
                        title=(
                            normalize_chapter_title(item.title, book_title=book.title)[:255]
                            or f"第 {sequence} 章"
                        ),
                        sequence=sequence,
                        volume_number=item.volume_number,
                        volume_title=item.volume_title,
                        chapter_kind=item.chapter_kind,
                        classification_status=(
                            "unclassified" if item.chapter_kind == "unclassified" else "ai_suggested"
                        ),
                        analysis_included=(
                            False if item.chapter_kind == "unclassified" else item.analysis_included
                        ),
                        source_hash=_text_hash(effective_text[item.start:item.end]),
                        source_start_offset=item.start,
                        source_end_offset=item.end,
                        analysis_status="not_started",
                    )
                    chapter_rows.append(chapter)
                    self.session.add(chapter)
                stats = {
                    "created": len(chapter_rows),
                    "updated": 0,
                    "unchanged": 0,
                    "changed": 0,
                }
            self.session.flush()
            book.meta_json = {
                **(book.meta_json or {}),
                "metadata": {
                    "title_source": metadata.title_source,
                    "author_source": metadata.author_source,
                    "description_source": metadata.description_source,
                    "confidence": metadata.confidence,
                    "original_filename": safe_name,
                },
                "import_structure": {
                    "source": analysis.structure_source,
                    "ai_assisted": analysis.ai_assisted,
                },
                "last_import": {
                    "mode": "merge" if merging else "replace" if replacing else "new",
                    "appended_only": merge_plan.appended_only if merge_plan else False,
                    **stats,
                },
            }

            self._write_manifest(
                paths=paths,
                book=book,
                filename=safe_name,
                data=source_data,
                detection=detection,
                parsed=parsed,
                chapters=chapter_rows,
                warnings=warnings,
                replaced=replacing,
                source_text=effective_text,
            )
            self.session.commit()
        except Exception:
            self.session.rollback()
            if root_created:
                shutil.rmtree(paths.root, ignore_errors=True)
            for path, content in merge_backups.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            if moved_trash is not None and not paths.root.exists():
                paths.root.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(moved_trash), str(paths.root))
            raise

        self.session.refresh(book)
        return ImportResult(
            book=to_book_detail(book, len(parsed.chapters)),
            chapters_created=stats["created"],
            chapters_updated=stats["changed"],
            chapters_unchanged=stats["unchanged"],
            chapters_changed=stats["changed"],
            encoding=detection.encoding,
            warnings=warnings,
            replaced=replacing,
            mode="merge" if merging else "replace" if replacing else "new",
            structure_source=analysis.structure_source,
            ai_assisted=analysis.ai_assisted,
        )

    def create_empty_book(self, payload: BookCreate) -> BookDetail:
        book_id = allocate_book_id(self.session)
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

    def _resolve_target(self, book_id: str) -> Book:
        validate_id(book_id, "book")
        target = self.session.get(Book, book_id)
        if target is None or target.deleted_at is not None:
            raise ValidationError(
                "目标书籍不存在或已在回收站",
                code="book_not_found",
                details={"book_id": book_id},
            )
        return target

    def _plan_merge(
        self,
        book: Book,
        uploaded_text: str,
        uploaded: ParseOutcome,
        custom_regex: str | None,
    ) -> _MergePlan:
        paths = book_paths(self.settings.data_dir, book.id)
        old_text = (
            paths.normalized_path.read_text(encoding="utf-8")
            if paths.normalized_path.exists()
            else ""
        )
        old_chapters = list(self.session.scalars(
            select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.sequence)
        ))
        if old_chapters and not old_text:
            raise ConflictError(
                "目标书籍缺少规范化原文，无法安全增量合并",
                code="merge_source_missing",
                details={"book_id": book.id},
            )
        if not old_chapters:
            return _MergePlan(
                text=uploaded_text,
                parsed=uploaded,
                matched={},
                old_hashes={},
                old_contents={},
                appended_only=False,
            )

        old_contents = {
            row.id: old_text[row.source_start_offset:row.source_end_offset]
            for row in old_chapters
        }
        old_hashes = {
            row.id: row.source_hash or _text_hash(old_contents[row.id])
            for row in old_chapters
        }
        new_hashes = [
            _text_hash(uploaded_text[item.start:item.end])
            for item in uploaded.chapters
        ]
        overlap = bool(set(old_hashes.values()) & set(new_hashes))
        appended_only = not overlap
        if appended_only:
            separator = ""
            if not old_text.endswith("\n"):
                separator += "\n"
            if not old_text.endswith("\n\n"):
                separator += "\n"
            effective_text = old_text + separator + uploaded_text
            # 追加片段不再调用 AI；复用旧书与上传片段已识别的标题，避免结构丢失。
            combined_headings: list[StructuralHeading] = []
            for row in old_chapters:
                content = old_contents[row.id]
                title_offset = content.find(row.title)
                if row.title and title_offset >= 0 and (title_offset == 0 or content[title_offset - 1] == "\n"):
                    start = row.source_start_offset + title_offset
                    combined_headings.append(StructuralHeading(
                        start=start,
                        end=start + len(row.title),
                        title=row.title,
                    ))
            delta = len(effective_text) - len(uploaded_text)
            for item in uploaded.chapters:
                if item.heading and item.start >= 0 and item.heading == uploaded_text[item.start:item.end].strip():
                    combined_headings.append(StructuralHeading(
                        start=item.start + delta,
                        end=item.end + delta,
                        title=item.heading,
                    ))
            parsed = parse_chapters(
                effective_text,
                custom_regex,
                book_title=book.title,
                extra_headings=combined_headings,
            )
        else:
            effective_text = uploaded_text
            parsed = uploaded

        new_by_hash: dict[str, list[ChapterSlice]] = {}
        new_by_key: dict[tuple[int, str], list[ChapterSlice]] = {}
        for item in parsed.chapters:
            item_hash = _text_hash(effective_text[item.start:item.end])
            new_by_hash.setdefault(item_hash, []).append(item)
            new_by_key.setdefault((item.volume_number, item.title), []).append(item)

        matched: dict[str, ChapterSlice] = {}
        used_ids: set[int] = set()
        for row in old_chapters:
            candidates = new_by_hash.get(old_hashes[row.id], [])
            if len(candidates) == 1 and id(candidates[0]) not in used_ids:
                matched[row.id] = candidates[0]
                used_ids.add(id(candidates[0]))
        for row in old_chapters:
            if row.id in matched:
                continue
            candidates = new_by_key.get((row.volume_number, row.title), [])
            candidates = [item for item in candidates if id(item) not in used_ids]
            if not candidates:
                candidates = [
                    item for item in parsed.chapters
                    if item.title == row.title and id(item) not in used_ids
                ]
            if len(candidates) == 1:
                matched[row.id] = candidates[0]
                used_ids.add(id(candidates[0]))

        missing = [row.title for row in old_chapters if row.id not in matched]
        if missing:
            raise ConflictError(
                "完整新版缺少原书中的章节，已拒绝合并以避免静默丢章",
                code="merge_missing_chapters",
                details={"book_id": book.id, "missing_titles": missing[:50]},
            )
        return _MergePlan(
            text=effective_text,
            parsed=parsed,
            matched=matched,
            old_hashes=old_hashes,
            old_contents=old_contents,
            appended_only=appended_only,
        )

    def _apply_merge(
        self,
        book: Book,
        plan: _MergePlan,
    ) -> tuple[list[Chapter], dict[str, int]]:
        old_rows = list(self.session.scalars(
            select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.sequence)
        ))
        old_by_id = {row.id: row for row in old_rows}
        # 先移出唯一序号区间，再逐章归位，避免 SQLite 唯一键更新冲突。
        for index, row in enumerate(old_rows, start=1):
            row.sequence = -index
        self.session.flush()

        chapter_rows: list[Chapter] = []
        stats = {"created": 0, "updated": 0, "unchanged": 0, "changed": 0}
        matched_slice_ids: set[int] = set()
        for sequence, item in enumerate(plan.parsed.chapters, start=1):
            chapter = next(
                (row for row in old_rows if plan.matched.get(row.id) is item),
                None,
            )
            item_hash = _text_hash(plan.text[item.start:item.end])
            if chapter is None:
                chapter = Chapter(
                    id=new_id("ch"),
                    book_id=book.id,
                    analysis_status="not_started",
                )
                self.session.add(chapter)
                stats["created"] += 1
            else:
                matched_slice_ids.add(id(item))
                old_hash = plan.old_hashes[chapter.id]
                new_content = plan.text[item.start:item.end]
                unchanged = old_hash == item_hash or (
                    new_content.startswith(plan.old_contents[chapter.id])
                    and not new_content[len(plan.old_contents[chapter.id]):].strip()
                )
                if unchanged:
                    stats["unchanged"] += 1
                    delta = item.start - chapter.source_start_offset
                    if delta:
                        self._rebase_current_offsets(chapter, delta)
                else:
                    stats["changed"] += 1
                    stats["updated"] += 1
                    self._invalidate_chapter(chapter)
            chapter.title = (
                normalize_chapter_title(item.title, book_title=book.title)[:255]
                or f"第 {sequence} 章"
            )
            chapter.sequence = sequence
            chapter.volume_number = item.volume_number
            chapter.volume_title = item.volume_title
            chapter.source_hash = item_hash
            chapter.source_start_offset = item.start
            chapter.source_end_offset = item.end
            chapter.updated_at = utc_now()
            chapter_rows.append(chapter)

        # 完整新版已校验无缺失；兜底防止异常匹配导致旧行残留。
        for row in old_rows:
            if id(plan.matched.get(row.id)) not in matched_slice_ids:
                raise ConflictError(
                    "章节匹配不完整，已拒绝合并",
                    code="merge_match_incomplete",
                )
        self.session.flush()
        chapter_rows.sort(key=lambda row: row.sequence)
        return chapter_rows, stats

    def _rebase_current_offsets(self, chapter: Chapter, delta: int) -> None:
        if not delta:
            return
        scenes = list(self.session.scalars(
            select(Scene).where(
                Scene.chapter_id == chapter.id,
                Scene.analysis_version == chapter.scene_version,
            )
        ))
        for scene in scenes:
            scene.source_start_offset += delta
            scene.source_end_offset += delta
        versions = list(self.session.scalars(
            select(ScriptVersion).where(
                ScriptVersion.chapter_id == chapter.id,
                ScriptVersion.is_active.is_(True),
            )
        ))
        version_ids = [row.id for row in versions]
        if version_ids:
            for unit in self.session.scalars(
                select(ScriptUnit).where(ScriptUnit.script_version_id.in_(version_ids))
            ):
                unit.source_start_offset += delta
                unit.source_end_offset += delta
        for segment in self.session.scalars(
            select(Segment).where(Segment.chapter_id == chapter.id)
        ):
            if segment.source_start_offset is not None:
                segment.source_start_offset += delta
            if segment.source_end_offset is not None:
                segment.source_end_offset += delta

    def _invalidate_chapter(self, chapter: Chapter) -> None:
        chapter.summary = None
        chapter.summary_json = {}
        chapter.analysis_meta_json = {}
        chapter.analysis_status = "not_started"
        chapter.scene_status = "not_started"
        chapter.scene_version = 0
        chapter.script_status = "not_started"
        for version in self.session.scalars(
            select(ScriptVersion).where(ScriptVersion.chapter_id == chapter.id)
        ):
            if version.is_active:
                version.status = "superseded"
            version.is_active = False
        for unit in self.session.scalars(
            select(ScriptUnit).where(ScriptUnit.chapter_id == chapter.id)
        ):
            unit.audio_status = "stale"
        for segment in self.session.scalars(
            select(Segment).where(Segment.chapter_id == chapter.id)
        ):
            segment.audio_status = "stale"
        existing = self.session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == chapter.book_id,
                ReviewItem.entity_type == "chapter",
                ReviewItem.entity_id == chapter.id,
                ReviewItem.code == "chapter_source_changed",
                ReviewItem.status == "open",
            )
        )
        if existing is None:
            self.session.add(ReviewItem(
                id=new_id("review"),
                book_id=chapter.book_id,
                entity_type="chapter",
                entity_id=chapter.id,
                code="chapter_source_changed",
                severity="warning",
                title=f"章节「{chapter.title}」正文已更新",
                message="旧分场、剧本和音频已保留为历史版本；请重新生成当前方案。",
                source="system",
            ))

    def _ensure_merge_is_safe(self, book: Book) -> None:
        running = self.session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.book_id == book.id,
                GenerationJob.status.in_(("pending", "running")),
            )
        )
        if running:
            raise ConflictError(
                "该书仍有未完成的分析/生成任务，拒绝增量导入。",
                code="merge_blocked_by_jobs",
                details={"book_id": book.id, "jobs": int(running)},
            )

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
        source_text: str,
    ) -> None:
        last_import = (book.meta_json or {}).get("last_import", {})
        manifest = {
            "schema_version": 2,
            "book_id": book.id,
            "title": book.title,
            "imported_at": utc_now().isoformat(),
            "original_filename": filename,
            "replaced": replaced,
            "import": last_import,
            "source": {
                "original": paths.relative_original(),
                "normalized": paths.relative_normalized(),
                "encoding": detection.encoding,
                "encoding_label": detection.label,
                "encoding_confidence": detection.confidence,
                "sha256": text_io.sha256_bytes(data),
                "size_bytes": len(data),
                "char_count": len(source_text),
                "line_count": len(source_text.splitlines()) or 1,
            },
            "chapters": [
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "sequence": chapter.sequence,
                    "volume_number": chapter.volume_number,
                    "volume_title": chapter.volume_title,
                    "chapter_kind": chapter.chapter_kind,
                    "analysis_included": chapter.analysis_included,
                    "source_hash": chapter.source_hash,
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