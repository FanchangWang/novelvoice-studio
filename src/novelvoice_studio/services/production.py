"""v0.5.0 P6：制作台总览与异常收件箱查询/处理。"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from novelvoice_studio.core.errors import NotFoundError, ValidationError
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    Book,
    BookBible,
    Chapter,
    Character,
    CharacterArc,
    GenerationJob,
    ReviewItem,
    Scene,
    ScriptUnit,
    ScriptVersion,
    VoiceCast,
)
from novelvoice_studio.schemas.production import (
    ProductionChapterOut,
    ProductionOverviewOut,
    ReviewBatchResolveRequest,
    ReviewBatchResolveResponse,
    ReviewCountsOut,
    ReviewListResponse,
    ReviewItemOut,
    ReviewResolveRequest,
)
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.paths import validate_id
from novelvoice_studio.services.review_policy import AUTO_ACCEPTED_CODES, policy_for
from novelvoice_studio.services.production_status import (
    effective_entity_status,
    effective_stage_status,
    effective_unit_status,
    effective_version_status,
    entity_has_open_blocking,
    has_open_blocking,
    stage_review_scope,
)
from novelvoice_studio.services.unit_audio import refresh_stale_units

ACTIVE_JOB_STATUSES = ("pending", "running")
TTS_UNIT_TYPES = {"narration", "dialogue", "inner_monologue"}
CUE_UNIT_TYPES = {"sfx", "ambience", "pause"}

_JOB_STATUS_MAP = {
    "book_bible": "analyzing",
    "chapter_scene": "scripting",
    "chapter_scenes": "scripting",
    "chapter_script": "scripting",
    "production_batch": "scripting",
    "book_casting": "casting",
}


def item_chapter_for_review(
    item: ReviewItem,
    scene_chapter_map: dict[str, str],
    unit_chapter_map: dict[str, str],
    version_chapter_map: dict[str, str],
) -> str | None:
    if item.entity_type == "chapter":
        return item.entity_id
    if item.entity_type == "scene" and item.entity_id:
        return scene_chapter_map.get(item.entity_id)
    if item.entity_type == "script_unit" and item.entity_id:
        return unit_chapter_map.get(item.entity_id)
    if item.entity_type == "script_version" and item.entity_id:
        return version_chapter_map.get(item.entity_id)
    payload = item.payload_json or {}
    candidate = payload.get("chapter_id")
    return candidate if isinstance(candidate, str) else None


class ProductionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -------------------------------------------------------------- overview

    def overview(self, book_id: str) -> ProductionOverviewOut:
        book = self._book(book_id)
        chapters = list(
            self.session.scalars(
                select(Chapter)
                .where(Chapter.book_id == book.id)
                .order_by(Chapter.sequence.asc())
            )
        )
        chapter_ids = {item.id for item in chapters}
        included_chapter_count = sum(1 for item in chapters if item.analysis_included)

        bible = self.session.scalar(
            select(BookBible)
            .where(BookBible.book_id == book.id, BookBible.is_active.is_(True))
            .order_by(BookBible.version.desc())
            .limit(1)
        )
        character_ids: set[str] = set()
        arc_count = 0
        if bible is not None:
            arcs = list(
                self.session.scalars(
                    select(CharacterArc).where(CharacterArc.bible_id == bible.id)
                )
            )
            arc_count = len(arcs)
            character_ids = {item.character_id for item in arcs}
        character_count = len(character_ids)
        if character_ids:
            character_count = int(
                self.session.scalar(
                    select(func.count(Character.id)).where(
                        Character.id.in_(character_ids),
                        Character.merged_into_id.is_(None),
                    )
                )
                or 0
            )

        scene_rows = self.session.execute(
            select(Scene.id, Scene.chapter_id, Scene.analysis_version).where(
                Scene.book_id == book.id
            )
        ).all()
        scene_counts: dict[str, int] = {}
        scene_chapter_map: dict[str, str] = {}
        chapter_by_id = {item.id: item for item in chapters}
        for scene_id, chapter_id, version in scene_rows:
            chapter = chapter_by_id.get(chapter_id)
            if chapter is not None and chapter.scene_version == version:
                scene_counts[chapter_id] = scene_counts.get(chapter_id, 0) + 1
                scene_chapter_map[scene_id] = chapter_id

        versions = list(
            self.session.scalars(
                select(ScriptVersion).where(
                    ScriptVersion.book_id == book.id,
                    ScriptVersion.is_active.is_(True),
                )
            )
        )
        version_by_chapter = {item.chapter_id: item for item in versions}
        unit_chapter_map: dict[str, str] = {}
        unit_counts: dict[str, dict[str, int]] = {}
        if versions:
            active_units = list(
                self.session.scalars(
                    select(ScriptUnit).where(
                        ScriptUnit.script_version_id.in_(
                            [item.id for item in versions]
                        )
                    )
                )
            )
            if refresh_stale_units(self.session, active_units):
                self.session.commit()
            unit_chapter_map = {
                unit.id: unit.chapter_id for unit in active_units
            }
            rows = self.session.execute(
                select(
                    ScriptUnit.script_version_id,
                    ScriptUnit.unit_type,
                    ScriptUnit.audio_status,
                    func.count(ScriptUnit.id),
                )
                .where(
                    ScriptUnit.script_version_id.in_(
                        [item.id for item in versions]
                    )
                )
                .group_by(
                    ScriptUnit.script_version_id,
                    ScriptUnit.unit_type,
                    ScriptUnit.audio_status,
                )
            ).all()
            for version_id, unit_type, audio_status, count in rows:
                bucket = unit_counts.setdefault(
                    version_id,
                    {
                        "total": 0,
                        "tts": 0,
                        "cue": 0,
                        "ready": 0,
                        "stale": 0,
                    },
                )
                bucket["total"] += int(count)
                if unit_type in TTS_UNIT_TYPES:
                    bucket["tts"] += int(count)
                    if audio_status == "ready":
                        bucket["ready"] += int(count)
                    elif audio_status == "stale":
                        bucket["stale"] += int(count)
                elif unit_type in CUE_UNIT_TYPES:
                    bucket["cue"] += int(count)

        open_reviews = [
            item
            for item in self.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.book_id == book.id,
                    ReviewItem.status == "open",
                )
            )
            if policy_for(item.code, category=item.category).actionable
        ]
        review_by_chapter = self._reviews_by_chapter(
            open_reviews,
            chapter_ids,
            scene_chapter_map,
            unit_chapter_map,
            {item.id: item.chapter_id for item in versions},
        )
        severity_counts = Counter(item.severity for item in open_reviews)
        blocking_by_stage = self._blocking_by_stage(
            open_reviews,
            chapter_ids,
            scene_chapter_map,
            unit_chapter_map,
            {item.id: item.chapter_id for item in versions},
        )
        blocking_workflow_count = sum(
            1
            for item in open_reviews
            if item.severity == "blocking"
            and (
                not item.code.startswith(("scene_", "script_"))
                or item_chapter_for_review(
                    item,
                    scene_chapter_map,
                    unit_chapter_map,
                    {version.id: version.chapter_id for version in versions},
                )
                in blocking_by_stage[
                    "scene" if item.code.startswith("scene_") else "script"
                ]
            )
        )

        cast_count = int(
            self.session.scalar(
                select(func.count(VoiceCast.id)).where(
                    VoiceCast.book_id == book.id,
                    VoiceCast.is_active.is_(True),
                )
            )
            or 0
        )
        narrator_ready = bool(
            self.session.scalar(
                select(func.count(VoiceCast.id)).where(
                    VoiceCast.book_id == book.id,
                    VoiceCast.is_active.is_(True),
                    VoiceCast.scope == "narrator",
                )
            )
        )

        chapter_items: list[ProductionChapterOut] = []
        chapters_with_scenes = 0
        chapters_with_scripts = 0
        scene_total = 0
        unit_total = 0
        tts_total = 0
        cue_total = 0
        audio_ready = 0
        audio_stale = 0
        for chapter in chapters:
            scene_count = scene_counts.get(chapter.id, 0)
            if scene_count:
                chapters_with_scenes += 1
            scene_total += scene_count
            version = version_by_chapter.get(chapter.id)
            counts = unit_counts.get(version.id, {}) if version is not None else {}
            if version is not None:
                chapters_with_scripts += 1
            unit_total += int(counts.get("total", 0))
            tts_total += int(counts.get("tts", 0))
            cue_total += int(counts.get("cue", 0))
            audio_ready += int(counts.get("ready", 0))
            audio_stale += int(counts.get("stale", 0))
            chapter_items.append(
                ProductionChapterOut(
                    id=chapter.id,
                    title=chapter.title,
                    sequence=chapter.sequence,
                    volume_number=chapter.volume_number,
                    volume_title=chapter.volume_title,
                    chapter_kind=chapter.chapter_kind,
                    analysis_included=chapter.analysis_included,
                    char_count=chapter.char_count,
                    scene_status=effective_stage_status(
                        chapter.scene_status,
                        blocking=chapter.id in blocking_by_stage["scene"],
                    ),
                    scene_version=chapter.scene_version,
                    scene_count=scene_count,
                    script_status=effective_stage_status(
                        chapter.script_status,
                        blocking=chapter.id in blocking_by_stage["script"],
                    ),
                    script_version=version.version if version is not None else 0,
                    unit_count=int(counts.get("total", 0)),
                    tts_unit_count=int(counts.get("tts", 0)),
                    cue_count=int(counts.get("cue", 0)),
                    audio_ready_units=int(counts.get("ready", 0)),
                    audio_stale_units=int(counts.get("stale", 0)),
                    review_count=review_by_chapter.get(chapter.id, 0),
                    summary=chapter.summary,
                )
            )

        active_job = self.session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.book_id == book.id,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(
                case((GenerationJob.job_type == "auto_production", 0), else_=1),
                GenerationJob.created_at.desc(),
            )
            .limit(1)
        )
        display_job = active_job
        if display_job is None:
            # 自动制作结束后保留父任务，使四阶段结果、系统故障和重试入口不会消失。
            display_job = self.session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.book_id == book.id,
                    GenerationJob.job_type == "auto_production",
                )
                .order_by(GenerationJob.created_at.desc())
                .limit(1)
            )
        bible_ready = bool(
            bible is not None and bible.status in {"approved", "needs_review"}
        )
        derived = self._derived_status(
            book,
            active_job,
            bible_ready,
            included_chapter_count,
            chapters_with_scenes,
            chapters_with_scripts,
            cast_count,
            blocking_workflow_count,
            severity_counts.get("warning", 0),
        )
        if (
            active_job is None
            and display_job is not None
            and display_job.job_type == "auto_production"
            and display_job.status == "failed"
        ):
            derived = "failed"
        next_action = self._next_action(
            bible_ready=bible_ready,
            chapter_count=included_chapter_count,
            chapters_with_scenes=chapters_with_scenes,
            chapters_with_scripts=chapters_with_scripts,
            cast_count=cast_count,
            blocking=blocking_workflow_count,
            warning=severity_counts.get("warning", 0),
            tts_total=tts_total,
            audio_ready=audio_ready,
        )
        return ProductionOverviewOut(
            book_id=book.id,
            title=book.title,
            production_status=book.production_status,
            derived_status=derived,
            bible_status=book.bible_status,
            bible_version=bible.version if bible is not None else None,
            bible_ready=bible_ready,
            bible_summary=bible.summary if bible is not None else None,
            character_count=character_count,
            arc_count=arc_count,
            chapter_count=included_chapter_count,
            chapters_with_scenes=chapters_with_scenes,
            chapters_with_scripts=chapters_with_scripts,
            scene_count=scene_total,
            script_unit_count=unit_total,
            tts_unit_count=tts_total,
            cue_count=cue_total,
            audio_ready_units=audio_ready,
            audio_stale_units=audio_stale,
            cast_count=cast_count,
            narrator_ready=narrator_ready,
            open_reviews=len(open_reviews),
            blocking_reviews=severity_counts.get("blocking", 0),
            warning_reviews=severity_counts.get("warning", 0),
            info_reviews=severity_counts.get("info", 0),
            next_action=next_action,
            job=to_job_out(display_job) if display_job is not None else None,
            chapters=chapter_items,
        )

    # ---------------------------------------------------------------- reviews

    def list_reviews(
        self,
        *,
        book_id: str | None = None,
        status: str | None = "open",
        severity: str | None = None,
        code: str | None = None,
        entity_type: str | None = None,
        chapter_id: str | None = None,
        category: str | None = "content",
        limit: int = 100,
        offset: int = 0,
    ) -> ReviewListResponse:
        limit = max(1, min(250, int(limit)))
        offset = max(0, int(offset))
        if book_id is not None:
            self._book(book_id)
        if chapter_id is not None:
            validate_id(chapter_id, "ch")
            chapter = self.session.get(Chapter, chapter_id)
            if chapter is None:
                raise NotFoundError("章节不存在")
            if book_id is not None and chapter.book_id != book_id:
                raise ValidationError("章节不属于当前书籍")
        conditions = []
        count_conditions = []
        if category not in {None, "content", "system", "all"}:
            raise ValidationError("异常分类无效")
        if category in {"content", "system"}:
            conditions.append(ReviewItem.category == category)
            count_conditions.append(ReviewItem.category == category)
        if category == "content":
            auto_condition = ReviewItem.code.in_(AUTO_ACCEPTED_CODES)
            for prefix in ("bible_note:%", "scene_note:%", "script_note:%"):
                auto_condition = auto_condition | ReviewItem.code.like(prefix)
            conditions.append(~auto_condition)
            count_conditions.append(~auto_condition)
        if book_id is not None:
            conditions.append(ReviewItem.book_id == book_id)
            count_conditions.append(ReviewItem.book_id == book_id)
        if status is not None:
            if status not in {"open", "resolved", "ignored"}:
                raise ValidationError("异常状态无效")
            conditions.append(ReviewItem.status == status)
        if severity is not None:
            if severity not in {"blocking", "warning", "info"}:
                raise ValidationError("异常级别无效")
            conditions.append(ReviewItem.severity == severity)
        if code:
            conditions.append(ReviewItem.code == code)
            count_conditions.append(ReviewItem.code == code)
        if entity_type:
            conditions.append(ReviewItem.entity_type == entity_type)
            count_conditions.append(ReviewItem.entity_type == entity_type)
        if chapter_id is not None:
            chapter_condition = or_(
                (ReviewItem.entity_type == "chapter")
                & (ReviewItem.entity_id == chapter_id),
                (ReviewItem.entity_type == "scene")
                & (
                    select(Scene.chapter_id)
                    .where(Scene.id == ReviewItem.entity_id)
                    .scalar_subquery()
                    == chapter_id
                ),
                (ReviewItem.entity_type == "script_unit")
                & (
                    select(ScriptUnit.chapter_id)
                    .where(ScriptUnit.id == ReviewItem.entity_id)
                    .scalar_subquery()
                    == chapter_id
                ),
                (ReviewItem.entity_type == "script_version")
                & (
                    select(ScriptVersion.chapter_id)
                    .where(ScriptVersion.id == ReviewItem.entity_id)
                    .scalar_subquery()
                    == chapter_id
                ),
                func.json_extract(ReviewItem.payload_json, "$.chapter_id")
                == chapter_id,
            )
            conditions.append(chapter_condition)
            count_conditions.append(chapter_condition)

        severity_order = case(
            (ReviewItem.severity == "blocking", 0),
            (ReviewItem.severity == "warning", 1),
            else_=2,
        )
        rows = self.session.execute(
            select(ReviewItem, Book.title)
            .join(Book, Book.id == ReviewItem.book_id)
            .where(*conditions)
            .order_by(
                severity_order,
                ReviewItem.created_at.desc(),
                ReviewItem.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
        total = int(
            self.session.scalar(
                select(func.count(ReviewItem.id)).where(*conditions)
            )
            or 0
        )
        counts = self._review_counts(book_id, count_conditions)
        return ReviewListResponse(
            items=[
                self._review_out(item, book_title)
                for item, book_title in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
            counts=counts,
        )

    def resolve_review(
        self, review_id: str, payload: ReviewResolveRequest
    ) -> tuple[ReviewItemOut, str, str]:
        validate_id(review_id, "review")
        item = self.session.get(ReviewItem, review_id)
        if item is None:
            raise NotFoundError("异常项不存在")
        book_title = self.session.scalar(
            select(Book.title).where(Book.id == item.book_id)
        )
        if item.status != "open":
            return self._review_out(item, book_title), item.book_id, item.code
        note = (payload.note or "").strip()
        if payload.action == "ignore" and not note:
            raise ValidationError("忽略异常必须填写原因")
        now = utc_now()
        item.status = "resolved" if payload.action == "accept" else "ignored"
        item.resolution_note = note or "已接受"
        item.resolved_at = now
        item.updated_at = now
        self.session.flush()
        chapter_id = self._review_chapter_id(item)
        if chapter_id and item.code.startswith("scene_"):
            self._refresh_stage_status(chapter_id, "scene")
        elif chapter_id and item.code.startswith("script_"):
            self._refresh_stage_status(chapter_id, "script")
        self.session.commit()
        return self._review_out(item, book_title), item.book_id, item.code

    def resolve_reviews(
        self, payload: ReviewBatchResolveRequest
    ) -> ReviewBatchResolveResponse:
        ids = list(dict.fromkeys(payload.review_ids))
        for review_id in ids:
            validate_id(review_id, "review")
        rows = list(
            self.session.scalars(select(ReviewItem).where(ReviewItem.id.in_(ids)))
        )
        found = {row.id for row in rows}
        missing = [review_id for review_id in ids if review_id not in found]
        if missing:
            raise NotFoundError(
                "部分异常项不存在",
                details={"missing": missing},
            )
        note = (payload.note or "").strip()
        if payload.action == "ignore" and not note:
            raise ValidationError("忽略异常必须填写原因")
        now = utc_now()
        resolved = 0
        ignored = 0
        skipped = 0
        touched: list[str] = []
        books: list[str] = []
        codes: list[str] = []
        scene_chapters: set[str] = set()
        script_chapters: set[str] = set()
        for row in rows:
            if row.status != "open":
                skipped += 1
                continue
            row.status = "resolved" if payload.action == "accept" else "ignored"
            row.resolution_note = note or "已接受"
            row.resolved_at = now
            row.updated_at = now
            if payload.action == "accept":
                resolved += 1
            else:
                ignored += 1
            touched.append(row.id)
            if row.code not in codes:
                codes.append(row.code)
            if row.book_id not in books:
                books.append(row.book_id)
            chapter_id = self._review_chapter_id(row)
            if chapter_id and row.code.startswith("scene_"):
                scene_chapters.add(chapter_id)
            elif chapter_id and row.code.startswith("script_"):
                script_chapters.add(chapter_id)
        self.session.flush()
        for chapter_id in scene_chapters:
            self._refresh_stage_status(chapter_id, "scene")
        for chapter_id in script_chapters:
            self._refresh_stage_status(chapter_id, "script")
        self.session.commit()
        return ReviewBatchResolveResponse(
            action=payload.action,
            resolved=resolved,
            ignored=ignored,
            skipped=skipped,
            review_ids=touched,
            book_ids=books,
            codes=codes,
        )

    # ---------------------------------------------------------------- helpers

    def _review_chapter_id(self, item: ReviewItem) -> str | None:
        if item.entity_type == "chapter":
            return item.entity_id
        if item.entity_type == "scene" and item.entity_id:
            return self.session.scalar(
                select(Scene.chapter_id).where(Scene.id == item.entity_id)
            )
        if item.entity_type == "script_unit" and item.entity_id:
            return self.session.scalar(
                select(ScriptUnit.chapter_id).where(ScriptUnit.id == item.entity_id)
            )
        if item.entity_type == "script_version" and item.entity_id:
            return self.session.scalar(
                select(ScriptVersion.chapter_id).where(
                    ScriptVersion.id == item.entity_id
                )
            )
        payload = item.payload_json or {}
        candidate = payload.get("chapter_id")
        return candidate if isinstance(candidate, str) else None

    def _refresh_stage_status(self, chapter_id: str, stage: str) -> None:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            return
        version = None
        if stage == "script":
            version = self.session.scalar(
                select(ScriptVersion)
                .where(
                    ScriptVersion.chapter_id == chapter.id,
                    ScriptVersion.is_active.is_(True),
                )
                .order_by(ScriptVersion.version.desc())
                .limit(1)
            )
        _, conditions = stage_review_scope(self.session, chapter, stage, version)
        blocking = has_open_blocking(self.session, chapter.book_id, stage, conditions)
        if stage == "scene":
            chapter.scene_status = effective_stage_status(
                chapter.scene_status,
                blocking=blocking,
            )
            rows = list(
                self.session.scalars(
                    select(Scene).where(
                        Scene.chapter_id == chapter.id,
                        Scene.analysis_version == chapter.scene_version,
                    )
                )
            )
            for row in rows:
                row.analysis_status = effective_entity_status(
                    row.analysis_status,
                    blocking=entity_has_open_blocking(
                        self.session,
                        row.book_id,
                        "scene",
                        "scene",
                        row.id,
                    ),
                )
                row.updated_at = utc_now()
        else:
            chapter.script_status = effective_stage_status(
                chapter.script_status,
                blocking=blocking,
            )
            if version is not None:
                version.status = effective_version_status(
                    version.status,
                    blocking=blocking,
                )
                version.updated_at = utc_now()
                units = list(
                    self.session.scalars(
                        select(ScriptUnit).where(
                            ScriptUnit.script_version_id == version.id,
                        )
                    )
                )
                for unit in units:
                    unit.review_status = effective_unit_status(
                        unit.review_status,
                        blocking=entity_has_open_blocking(
                            self.session,
                            unit.book_id,
                            "script",
                            "script_unit",
                            unit.id,
                        ),
                    )
                    unit.updated_at = utc_now()
        chapter.updated_at = utc_now()

    def _book(self, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = self.session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在")
        return book

    def _review_out(
        self, item: ReviewItem, book_title: str | None = None
    ) -> ReviewItemOut:
        chapter_id = self._review_chapter_id(item)
        chapter_title = (
            self.session.scalar(select(Chapter.title).where(Chapter.id == chapter_id))
            if chapter_id
            else None
        )
        policy = policy_for(item.code, category=item.category)
        return ReviewItemOut(
            id=item.id,
            book_id=item.book_id,
            book_title=book_title,
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            code=item.code,
            category=item.category or policy.category,
            actionable=policy.actionable,
            severity=item.severity,
            title=item.title,
            message=item.message,
            location=self._review_location(item, chapter_id),
            impact=policy.impact,
            recommended_action=policy.recommended_action,
            action_type=policy.action_type,
            payload=item.payload_json or {},
            status=item.status,
            source=item.source,
            resolution_note=item.resolution_note,
            resolved_at=item.resolved_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _review_location(
        self, item: ReviewItem, chapter_id: str | None
    ) -> dict[str, object]:
        """仅从已入库内容构造定位，不读取用户提供的任意路径。"""
        payload = item.payload_json or {}
        location: dict[str, object] = {"chapter_id": chapter_id}
        unit: ScriptUnit | None = None
        if item.entity_type == "script_unit" and item.entity_id:
            unit = self.session.get(ScriptUnit, item.entity_id)
        elif item.code == "script_unresolved_speaker" and chapter_id:
            indexes = [
                row.get("unit_index")
                for row in payload.get("units", [])
                if isinstance(row, dict) and isinstance(row.get("unit_index"), int)
            ]
            if indexes:
                version = self.session.scalar(
                    select(ScriptVersion)
                    .where(
                        ScriptVersion.chapter_id == chapter_id,
                        ScriptVersion.is_active.is_(True),
                    )
                    .order_by(ScriptVersion.version.desc())
                    .limit(1)
                )
                if version is not None:
                    unit = self.session.scalar(
                        select(ScriptUnit)
                        .where(
                            ScriptUnit.script_version_id == version.id,
                            ScriptUnit.unit_index.in_(indexes),
                            ScriptUnit.unit_type == "dialogue",
                            ScriptUnit.speaker_type != "character",
                        )
                        .order_by(ScriptUnit.unit_index)
                        .limit(1)
                    )
        if unit is not None:
            location.update(
                unit_id=unit.id,
                unit_index=unit.unit_index,
                quote=unit.source_text,
                source_start_offset=unit.source_start_offset,
                source_end_offset=unit.source_end_offset,
            )
            return location
        if item.entity_type == "scene" and item.entity_id:
            scene = self.session.get(Scene, item.entity_id)
            if scene is not None:
                evidence = scene.evidence_json or []
                quote = next(
                    (
                        row.get("quote")
                        for row in evidence
                        if isinstance(row, dict) and row.get("quote")
                    ),
                    "",
                )
                location.update(
                    scene_id=scene.id,
                    scene_index=scene.scene_index,
                    quote=quote,
                    source_start_offset=scene.source_start_offset,
                    source_end_offset=scene.source_end_offset,
                )
                return location
        if item.entity_type == "character" and item.entity_id:
            character = self.session.get(Character, item.entity_id)
            if character is not None:
                evidence = next(
                    (
                        row
                        for row in character.evidence_json
                        if isinstance(row, dict) and row.get("chapter_id")
                    ),
                    {},
                )
                evidence_chapter = evidence.get("chapter_id")
                if isinstance(evidence_chapter, str):
                    location["chapter_id"] = evidence_chapter
                location.update(
                    character_id=character.id,
                    character_name=character.canonical_name,
                    quote=evidence.get("quote", ""),
                )
                return location
        quote = payload.get("quote")
        if isinstance(quote, str) and quote:
            location["quote"] = quote
        return location

    def _derived_status(
        self,
        book: Book,
        job: GenerationJob | None,
        bible_ready: bool,
        chapter_count: int,
        chapters_with_scenes: int,
        chapters_with_scripts: int,
        cast_count: int,
        blocking: int,
        warning: int,
    ) -> str:
        if job is not None:
            if job.job_type == "auto_production":
                stage = (job.result_json or {}).get("stage")
                return {
                    "bible": "analyzing",
                    "casting": "casting",
                    "script": "scripting",
                    "audio": "generating",
                }.get(stage, "analyzing")
            return _JOB_STATUS_MAP.get(job.job_type, "analyzing")
        if chapter_count == 0:
            return "not_started"
        if not bible_ready:
            if book.bible_status == "failed":
                return "failed"
            if book.bible_status == "running":
                return "analyzing"
            return "not_started"
        if (
            chapters_with_scenes < chapter_count
            or chapters_with_scripts < chapter_count
        ):
            return "scripting"
        if cast_count == 0:
            return "casting"
        if blocking:
            return "needs_review"
        if book.production_status in {"ready", "needs_review", "failed"}:
            return book.production_status
        return "casting"

    @staticmethod
    def _next_action(
        *,
        bible_ready: bool,
        chapter_count: int,
        chapters_with_scenes: int,
        chapters_with_scripts: int,
        cast_count: int,
        blocking: int,
        warning: int,
        tts_total: int,
        audio_ready: int,
    ) -> str:
        if not bible_ready:
            return "bible"
        if chapter_count == 0:
            return None
        if chapters_with_scenes < chapter_count:
            return "scenes"
        if chapters_with_scripts < chapter_count:
            return "script"
        if cast_count == 0:
            return "casting"
        if blocking:
            return "reviews"
        if tts_total == 0:
            return "script"
        if audio_ready < tts_total:
            return "audio"
        return "done"

    def _reviews_by_chapter(
        self,
        reviews: list[ReviewItem],
        chapter_ids: set[str],
        scene_chapter_map: dict[str, str],
        unit_chapter_map: dict[str, str],
        version_chapter_map: dict[str, str],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in reviews:
            chapter_id = item_chapter_for_review(
                item,
                scene_chapter_map,
                unit_chapter_map,
                version_chapter_map,
            )
            if chapter_id in chapter_ids:
                counts[chapter_id] = counts.get(chapter_id, 0) + 1
        return counts

    @staticmethod
    def _blocking_by_stage(
        reviews: list[ReviewItem],
        chapter_ids: set[str],
        scene_chapter_map: dict[str, str],
        unit_chapter_map: dict[str, str],
        version_chapter_map: dict[str, str],
    ) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {"scene": set(), "script": set()}
        for item in reviews:
            if item.severity != "blocking":
                continue
            if item.code.startswith("scene_"):
                stage = "scene"
            elif item.code.startswith("script_"):
                stage = "script"
            else:
                continue
            chapter_id = item_chapter_for_review(
                item,
                scene_chapter_map,
                unit_chapter_map,
                version_chapter_map,
            )
            if chapter_id in chapter_ids:
                result[stage].add(chapter_id)
        return result

    def _review_counts(
        self,
        book_id: str | None,
        extra_conditions: list | None = None,
    ) -> ReviewCountsOut:
        conditions = list(extra_conditions or [])
        if book_id is not None and not conditions:
            conditions.append(ReviewItem.book_id == book_id)
        rows = self.session.execute(
            select(
                ReviewItem.status,
                ReviewItem.severity,
                func.count(ReviewItem.id),
            )
            .where(*conditions)
            .group_by(ReviewItem.status, ReviewItem.severity)
        ).all()
        counts = ReviewCountsOut()
        for status, severity, count in rows:
            value = int(count)
            if status == "open":
                counts.open += value
                if severity == "blocking":
                    counts.blocking += value
                elif severity == "warning":
                    counts.warning += value
                elif severity == "info":
                    counts.info += value
            elif status == "resolved":
                counts.resolved += value
            elif status == "ignored":
                counts.ignored += value
        return counts