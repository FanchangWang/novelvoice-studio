"""v0.5.0 P4：基于分场与角色状态生成轻改编有声剧本。

策略：

1. 只读取当前有效分场版本，按场景分块调用模型，避免整章/整书一次生成；
2. 模型给出的 ``source_text`` 必须是场景原文的连续片段；后端按顺序对齐，
   未覆盖的非空白原文由确定性旁白补齐，不静默丢字；
3. 角色姓名只映射当前角色圣经；对白说话人无法确认时进入异常；
4. 生成剧本版本并自动设为当前版本；旧版本、旧单元保留，可回退；
5. 音效/环境/停顿只做 cue，不进入 TTS；剧本或表演字段变化会使音频 stale。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.base import (
    ChapterScriptResult,
    EvidenceDraft,
    ScriptUnitDraft,
)
from novelvoice_studio.ai.prompts import SCRIPT_PROMPT_VERSION
from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from novelvoice_studio.db.session import session_scope
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    Book,
    Chapter,
    Character,
    GenerationJob,
    ReviewItem,
    Scene,
    SceneCharacter,
    ScriptUnit,
    ScriptVersion,
    VoiceActor,
    VoiceCast,
)
from novelvoice_studio.models.segment import DELIVERY_VALUES, EMOTION_VALUES
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.scripts import (
    ScriptReviewItemOut,
    ScriptStateResponse,
    ScriptStatusResponse,
    ScriptUnitOut,
    ScriptVersionOut,
    ScriptVersionListResponse,
)
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.paths import new_id, validate_id
from novelvoice_studio.services.unit_audio import (
    refresh_stale_units,
    resolve_unit_voices,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "chapter_script"
JOB_STAGE = "script"
ACTIVE_JOB_STATUSES = ("pending", "running")
NOTE_LIMIT = 20
SCRIPT_TTS_TYPES = {"narration", "dialogue", "inner_monologue"}
SCRIPT_CUE_TYPES = {"sfx", "ambience", "pause"}
SCRIPT_UNIT_TYPES = SCRIPT_TTS_TYPES | SCRIPT_CUE_TYPES | {"unknown"}
_SPEAKER_TYPES = {"narrator", "character", "unknown", "none"}
_EMOTIONS = set(EMOTION_VALUES)
_DELIVERIES = set(DELIVERY_VALUES)
_UNSET_NAMES = {
    "无",
    "未知",
    "unknown",
    "none",
    "null",
    "旁白",
    "narrator",
    "未确定",
    "无法判断",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _clean_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:limit] if cleaned else None


def _clean_name(value: object, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clamp(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _evidence_dicts(
    items: object, chapter_id: str | None, limit: int = 8
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(items, list):
        return rows
    for item in items:
        quote = ""
        reason = None
        item_chapter = chapter_id
        if isinstance(item, EvidenceDraft):
            quote = _clean_text(item.quote, 500) or ""
            reason = _clean_text(item.reason, 300)
            item_chapter = item.chapter_id or chapter_id
        elif isinstance(item, dict):
            quote = _clean_text(item.get("quote"), 500) or ""
            reason = _clean_text(item.get("reason"), 300)
            item_chapter = item.get("chapter_id") or chapter_id
        elif isinstance(item, str):
            quote = _clean_text(item, 500) or ""
        if not quote:
            continue
        key = (item_chapter or "", quote)
        if key in seen:
            continue
        seen.add(key)
        row: dict[str, Any] = {"chapter_id": item_chapter, "quote": quote}
        if reason:
            row["reason"] = reason
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _split_window(text: str, start: int, limit: int) -> list[tuple[int, int, str]]:
    """按句末标点切块；无法切时硬切，保证每块可控。"""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    length = len(text)
    while cursor < length:
        hard_end = min(length, cursor + limit)
        if hard_end >= length:
            end = length
        else:
            window = text[cursor:hard_end]
            cut = -1
            for position in range(len(window) - 1, max(1, len(window) // 2), -1):
                if window[position] in "。！？!?；;\n":
                    cut = position + 1
                    break
            end = cursor + cut if cut > 0 else hard_end
        content_start = cursor
        while content_start < end and text[content_start].isspace():
            content_start += 1
        content_end = end
        while content_end > content_start and text[content_end - 1].isspace():
            content_end -= 1
        if content_end > content_start:
            spans.append(
                (
                    start + content_start,
                    start + content_end,
                    text[content_start:content_end],
                )
            )
        cursor = end
    return spans


_SENTENCE = re.compile(r"[^\n。！？!?；;]+[。！？!?；;]?")
_QUOTED = re.compile(r"[“「『]([^”」』]+)[”」』]")
_INNER_MARKERS = ("心想", "心里", "暗想", "默念", "暗自")


def _candidate_type(text: str) -> str:
    if _QUOTED.search(text):
        return "dialogue"
    if any(marker in text for marker in _INNER_MARKERS):
        return "inner_monologue"
    return "narration"


@dataclass(frozen=True)
class _SceneSource:
    id: str
    scene_index: int
    title: str | None
    summary: str | None
    location: str | None
    story_time_label: str | None
    time_mode: str
    source_start_offset: int
    source_end_offset: int
    text: str
    states: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _Chunk:
    scene: _SceneSource
    index: int
    total: int
    start: int
    end: int
    text: str

    @property
    def abs_start(self) -> int:
        return self.scene.source_start_offset + self.start


@dataclass
class _ReviewDraft:
    entity_type: str
    code: str
    severity: str
    title: str
    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    entity_id: str | None = None


@dataclass
class _UnitPlan:
    unit_type: str
    source_text: str
    text: str
    display_text: str | None
    source_start_offset: int
    source_end_offset: int
    scene_id: str | None
    speaker_type: str = "unknown"
    speaker_id: str | None = None
    speaker_name_snapshot: str | None = None
    character_state_id: str | None = None
    emotion: str = "neutral"
    intensity: int = 1
    delivery: str = "normal"
    tts_instruction: str | None = None
    speed: float = 1.0
    pause_before_ms: int = 0
    pause_after_ms: int = 250
    sfx_json: dict[str, Any] = field(default_factory=dict)
    review_status: str = "auto"
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source: str = "ai"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_cue(self) -> bool:
        return self.unit_type in SCRIPT_CUE_TYPES


@dataclass
class _ChunkOutcome:
    units: list[_UnitPlan]
    reviews: list[_ReviewDraft]
    calls: list[dict[str, Any]]
    notes: list[str]
    failed: bool = False
    failure_reason: str | None = None
    invalid_quotes: int = 0
    filled_chars: int = 0


class ScriptService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        registry: ProviderRegistry,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry
        self.window_chars = max(200, settings.analysis.max_chapter_chars)
        self.confidence_threshold = settings.analysis.scene_confidence_threshold

    # --------------------------------------------------------------- helpers

    def _book(self, session: Session, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _chapter(self, session: Session, chapter_id: str) -> tuple[Chapter, Book]:
        validate_id(chapter_id, "ch")
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            raise NotFoundError("章节不存在")
        return chapter, self._book(session, chapter.book_id)

    def _active_job(self, session: Session, chapter_id: str) -> GenerationJob | None:
        return session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.target_type == "chapter",
                GenerationJob.target_id == chapter_id,
                GenerationJob.job_type == JOB_TYPE,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )

    def _latest_job(self, session: Session, chapter_id: str) -> GenerationJob | None:
        return session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.target_type == "chapter",
                GenerationJob.target_id == chapter_id,
                GenerationJob.job_type == JOB_TYPE,
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )

    def _scene_rows(self, session: Session, chapter: Chapter) -> list[Scene]:
        if chapter.scene_version <= 0:
            return []
        return list(
            session.scalars(
                select(Scene)
                .where(
                    Scene.chapter_id == chapter.id,
                    Scene.analysis_version == chapter.scene_version,
                )
                .order_by(Scene.scene_index.asc())
            )
        )

    def _scene_sources(
        self,
        session: Session,
        chapter: Chapter,
        source: str,
        scenes: list[Scene],
    ) -> list[_SceneSource]:
        states = list(
            session.scalars(
                select(SceneCharacter)
                .where(SceneCharacter.scene_id.in_([row.id for row in scenes]))
                .order_by(SceneCharacter.created_at.asc(), SceneCharacter.id.asc())
            )
        )
        names = dict(
            session.execute(
                select(Character.id, Character.canonical_name).where(
                    Character.id.in_(
                        [row.character_id for row in states if row.character_id]
                    )
                )
            ).all()
        )
        by_scene: dict[str, list[SceneCharacter]] = {}
        for state in states:
            by_scene.setdefault(state.scene_id, []).append(state)
        result: list[_SceneSource] = []
        for row in scenes:
            start = max(chapter.source_start_offset, row.source_start_offset)
            end = min(chapter.source_end_offset, row.source_end_offset)
            if end <= start:
                continue
            text = source[start:end]
            if not text.strip():
                continue
            payload: list[dict[str, Any]] = []
            for state in by_scene.get(row.id, []):
                payload.append(
                    {
                        "id": state.id,
                        "character_id": state.character_id,
                        "character_name": names.get(state.character_id),
                        "presence": state.presence,
                        "age_stage": state.age_stage,
                        "physical_state": state.physical_state,
                        "emotional_state": state.emotional_state,
                        "relationship_state": state.relationship_state,
                        "knowledge_state": state.knowledge_state,
                        "disguise_state": state.disguise_state,
                        "voice_note": state.voice_note,
                        "state_tags": list(state.state_tags_json or []),
                    }
                )
            result.append(
                _SceneSource(
                    id=row.id,
                    scene_index=row.scene_index,
                    title=row.title,
                    summary=row.summary,
                    location=row.location,
                    story_time_label=row.story_time_label,
                    time_mode=row.time_mode,
                    source_start_offset=start,
                    source_end_offset=end,
                    text=text,
                    states=tuple(payload),
                )
            )
        return result

    def _chunks(self, scenes: list[_SceneSource]) -> list[_Chunk]:
        chunks: list[_Chunk] = []
        for scene in scenes:
            spans = _split_window(
                scene.text,
                scene.source_start_offset,
                self.window_chars,
            )
            total = len(spans)
            for index, (start, end, text) in enumerate(spans, start=1):
                chunks.append(
                    _Chunk(
                        scene=scene,
                        index=index,
                        total=total,
                        start=start - scene.source_start_offset,
                        end=end - scene.source_start_offset,
                        text=text,
                    )
                )
        return chunks

    def _load_characters(
        self, session: Session, book_id: str
    ) -> tuple[list[Character], list[dict[str, Any]]]:
        characters = list(
            session.scalars(
                select(Character)
                .where(
                    Character.book_id == book_id,
                    Character.merged_into_id.is_(None),
                )
                .order_by(Character.created_at.asc(), Character.id.asc())
            )
        )
        payload: list[dict[str, Any]] = []
        for row in characters:
            aliases: list[str] = []
            for alias in row.aliases_json or []:
                name = alias.get("name") if isinstance(alias, dict) else alias
                cleaned = _clean_name(name)
                if cleaned and cleaned != row.canonical_name and cleaned not in aliases:
                    aliases.append(cleaned)
            payload.append(
                {
                    "canonical_name": row.canonical_name,
                    "aliases": aliases,
                    "gender": row.gender,
                    "age_range": row.age_range,
                    "identity_description": row.identity_description,
                    "voice_description": row.voice_description,
                    "role_tier": row.role_tier,
                }
            )
        return characters, payload

    def _character_index(
        self, characters: list[Character]
    ) -> tuple[dict[str, list[Character]], dict[str, list[Character]]]:
        canonical: dict[str, list[Character]] = {}
        aliases: dict[str, list[Character]] = {}
        for row in characters:
            canonical.setdefault(_norm(row.canonical_name), []).append(row)
            for alias in row.aliases_json or []:
                name = alias.get("name") if isinstance(alias, dict) else alias
                cleaned = _clean_name(name)
                if cleaned:
                    aliases.setdefault(_norm(cleaned), []).append(row)
        return canonical, aliases

    def _resolve_character(
        self,
        name: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
    ) -> tuple[Character | None, str | None]:
        key = _norm(name)
        if not key:
            return None, "blank"
        candidates = canonical.get(key) or aliases.get(key) or []
        unique = {row.id: row for row in candidates}
        if len(unique) == 1:
            return next(iter(unique.values())), None
        if len(unique) > 1:
            return None, "ambiguous"
        return None, "unknown"

    # ------------------------------------------------------------ start/cancel

    def start_script(self, chapter_id: str, adaptation_level: str = "light") -> AnalysisJobOut:
        adaptation = (adaptation_level or "light").strip().lower()
        if adaptation not in {"light", "standard", "strong"}:
            raise ValidationError("改编档位只能是 light / standard / strong", code="invalid_adaptation")
        with session_scope(self.session_factory) as session:
            chapter, book = self._chapter(session, chapter_id)
            if self._active_job(session, chapter.id) is not None:
                raise ConflictError(
                    "本章正在生成有声剧本，请等待当前任务结束或先取消",
                    code="script_analysis_running",
                )
            if chapter.scene_version <= 0:
                raise ValidationError(
                    "请先生成并确认本章分场，再生成有声剧本",
                    code="scenes_required",
                )
            scenes = self._scene_rows(session, chapter)
            if not scenes:
                raise ValidationError(
                    "当前分场版本没有可用场景，请重新生成分场",
                    code="scenes_required",
                )
            source = BookService(self.settings, session).read_source(book)
            scene_sources = self._scene_sources(session, chapter, source, scenes)
            chunks = self._chunks(scene_sources)
            if not chunks:
                raise ValidationError("章节没有可用原文，无法生成剧本", code="chapter_empty")
            max_version = int(
                session.scalar(
                    select(func.max(ScriptVersion.version)).where(
                        ScriptVersion.chapter_id == chapter.id
                    )
                )
                or 0
            )
            version = max_version + 1
            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=chapter.id,
                target_type="chapter",
                stage=JOB_STAGE,
                job_type=JOB_TYPE,
                status="pending",
                total_items=len(chunks),
                completed_items=0,
                failed_items=0,
                progress=0.0,
                attempt_count=0,
                max_attempts=1,
                params_json={
                    "chapter_id": chapter.id,
                    "script_version": version,
                    "scene_version": chapter.scene_version,
                    "adaptation_level": adaptation,
                    "chunks": len(chunks),
                    "scenes": len(scene_sources),
                },
                result_json={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            chapter.script_status = "running"
            chapter.updated_at = utc_now()
            session.flush()
            output = to_job_out(job)

        thread = threading.Thread(
            target=self._run_job,
            args=(output.id,),
            name=f"novelvoice-script-{output.id}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001
            logger.exception("无法启动剧本后台线程")
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, output.id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = "thread_start_failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                self._fail_script_job(session, output.id, exc)
            raise AppError(
                "无法启动剧本后台线程",
                status_code=500,
                code="script_thread_failed",
            ) from exc
        return output

    def cancel_script(self, chapter_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            chapter, _book = self._chapter(session, chapter_id)
            job = self._active_job(session, chapter.id)
            if job is None:
                raise ConflictError("本章没有正在生成的剧本任务", code="no_active_job")
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            self._restore_chapter_status(session, chapter, cancelled=True)
            return to_job_out(job)

    # ---------------------------------------------------------------- status

    def script_status(self, chapter_id: str) -> ScriptStatusResponse:
        with session_scope(self.session_factory) as session:
            chapter, book = self._chapter(session, chapter_id)
            job = self._latest_job(session, chapter.id)
            active = job is not None and job.status in ACTIVE_JOB_STATUSES
            version = self._active_version(session, chapter.id)
            _, conditions = self._review_scope(session, chapter)
            open_reviews = int(
                session.scalar(
                    select(func.count(ReviewItem.id)).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                        ReviewItem.code.startswith("script_"),
                        or_(*conditions),
                    )
                )
                or 0
            )
            return ScriptStatusResponse(
                chapter_id=chapter.id,
                book_id=book.id,
                script_status="running" if active else chapter.script_status,
                script_version=version.version if version else 0,
                adaptation_level=version.adaptation_level if version else None,
                open_reviews=open_reviews,
                job=to_job_out(job) if job else None,
            )

    def get_script(
        self, chapter_id: str, version: int | None = None
    ) -> ScriptStateResponse:
        with session_scope(self.session_factory) as session:
            chapter, book = self._chapter(session, chapter_id)
            job = self._latest_job(session, chapter.id)
            active = job is not None and job.status in ACTIVE_JOB_STATUSES
            row = self._select_version(session, chapter.id, version)
            units = (
                list(
                    session.scalars(
                        select(ScriptUnit)
                        .where(ScriptUnit.script_version_id == row.id)
                        .order_by(ScriptUnit.unit_index.asc())
                    )
                )
                if row is not None
                else []
            )
            if units and refresh_stale_units(session, units):
                session.commit()
            voices = resolve_unit_voices(session, units)
            names = dict(
                session.execute(
                    select(Character.id, Character.canonical_name).where(
                        Character.id.in_(
                            [unit.speaker_id for unit in units if unit.speaker_id]
                        )
                    )
                ).all()
            ) if units else {}
            _, conditions = self._review_scope(session, chapter)
            reviews = list(
                session.scalars(
                    select(ReviewItem)
                    .where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                        ReviewItem.code.startswith("script_"),
                        or_(*conditions),
                    )
                    .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
                )
            )
            return ScriptStateResponse(
                chapter_id=chapter.id,
                book_id=book.id,
                script_status="running" if active else chapter.script_status,
                script_version=row.version if row else 0,
                adaptation_level=row.adaptation_level if row else None,
                open_reviews=len(reviews),
                job=to_job_out(job) if job else None,
                version=self._version_out(row, len(units)) if row else None,
                units=[
                    self._unit_out(
                        unit,
                        names.get(unit.speaker_id),
                        voices.get(unit.id),
                    )
                    for unit in units
                ],
                review_items=[self._review_out(item) for item in reviews],
            )

    def list_versions(self, chapter_id: str) -> ScriptVersionListResponse:
        with session_scope(self.session_factory) as session:
            chapter, _book = self._chapter(session, chapter_id)
            rows = list(
                session.scalars(
                    select(ScriptVersion)
                    .where(ScriptVersion.chapter_id == chapter.id)
                    .order_by(ScriptVersion.version.desc())
                )
            )
            counts = dict(
                session.execute(
                    select(ScriptUnit.script_version_id, func.count(ScriptUnit.id))
                    .where(
                        ScriptUnit.script_version_id.in_([row.id for row in rows])
                    )
                    .group_by(ScriptUnit.script_version_id)
                ).all()
            ) if rows else {}
            return ScriptVersionListResponse(
                chapter_id=chapter.id,
                items=[self._version_out(row, int(counts.get(row.id, 0))) for row in rows],
                total=len(rows),
            )

    def activate_version(self, version_id: str, *, approve: bool = False) -> ScriptVersionOut:
        with session_scope(self.session_factory) as session:
            validate_id(version_id, "script")
            row = session.get(ScriptVersion, version_id)
            if row is None:
                raise NotFoundError("剧本版本不存在")
            chapter = session.get(Chapter, row.chapter_id)
            if chapter is None:
                raise NotFoundError("章节不存在")
            active_rows = list(
                session.scalars(
                    select(ScriptVersion).where(
                        ScriptVersion.chapter_id == row.chapter_id,
                        ScriptVersion.is_active.is_(True),
                    )
                )
            )
            for active in active_rows:
                if active.id != row.id:
                    active.is_active = False
                    active.updated_at = utc_now()
            session.flush()
            row.is_active = True
            if approve:
                row.status = "approved"
                row.approved_at = utc_now()
            row.updated_at = utc_now()
            chapter.script_status = (
                "needs_review" if row.status == "needs_review" else "completed"
            )
            chapter.updated_at = utc_now()
            count = int(
                session.scalar(
                    select(func.count(ScriptUnit.id)).where(
                        ScriptUnit.script_version_id == row.id
                    )
                )
                or 0
            )
            return self._version_out(row, count)

    # ---------------------------------------------------------------- worker

    def _run_job(self, job_id: str) -> None:
        started = _iso_now()
        started_perf = time.perf_counter()
        try:
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = utc_now()
                job.attempt_count = 1
                job.updated_at = utc_now()
                chapter, book = self._chapter(session, job.target_id or "")
                scenes = self._scene_rows(session, chapter)
                if not scenes:
                    raise ValidationError(
                        "当前分场版本没有可用场景，请重新生成分场",
                        code="scenes_required",
                    )
                source = BookService(self.settings, session).read_source(book)
                scene_sources = self._scene_sources(session, chapter, source, scenes)
                chunks = self._chunks(scene_sources)
                if not chunks:
                    raise ValidationError("章节没有可用原文，无法生成剧本", code="chapter_empty")
                characters, known = self._load_characters(session, book.id)
                job.total_items = len(chunks)
                job.updated_at = utc_now()
                params = dict(job.params_json or {})
                version = int(params.get("script_version") or 0)
                if version <= 0:
                    version = int(
                        session.scalar(
                            select(func.max(ScriptVersion.version)).where(
                                ScriptVersion.chapter_id == chapter.id
                            )
                        )
                        or 0
                    ) + 1
                    params["script_version"] = version
                    job.params_json = params
                adaptation = str(params.get("adaptation_level") or "light")
                scene_version = chapter.scene_version
                book_title = book.title
                chapter_title = chapter.title
                chapter_sequence = chapter.sequence
                source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

            canonical, alias_index = self._character_index(characters)
            all_units: list[_UnitPlan] = []
            chapter_reviews: list[_ReviewDraft] = []
            calls: list[dict[str, Any]] = []
            notes: list[str] = []
            failed_chunks: list[dict[str, Any]] = []
            invalid_quotes = 0
            filled_chars = 0
            valid_chunks = 0
            last_error: Exception | None = None

            for chunk in chunks:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                context = {
                    "book_title": book_title,
                    "chapter_title": chapter_title,
                    "chapter_sequence": chapter_sequence,
                    "adaptation_level": adaptation,
                    "scene": {
                        "title": chunk.scene.title,
                        "summary": chunk.scene.summary,
                        "location": chunk.scene.location,
                        "story_time_label": chunk.scene.story_time_label,
                        "time_mode": chunk.scene.time_mode,
                    },
                    "speaker_states": list(chunk.scene.states),
                    "chunk": {"index": chunk.index, "total": chunk.total},
                }
                result: ChapterScriptResult | None = None
                failure_reason: str | None = None
                try:
                    result = self.registry.llm.adapt_scene_script(
                        chunk.text, known, context
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    failure_reason = (
                        f"{type(exc).__name__}: "
                        f"{_clean_text(getattr(exc, 'message', str(exc)), 300)}"
                    )
                if result is not None and not result.units:
                    failure_reason = "模型未返回剧本单元"
                if result is not None and result.units:
                    valid_chunks += 1
                    calls.append(
                        {
                            "scene": chunk.scene.scene_index,
                            "chunk": chunk.index,
                            "call": dict(result.meta or {}),
                        }
                    )
                    outcome = self._plan_chunk(
                        result,
                        chunk,
                        chapter.id,
                        canonical,
                        alias_index,
                        start_index=len(all_units),
                    )
                    all_units.extend(outcome.units)
                    chapter_reviews.extend(outcome.reviews)
                    notes.extend(
                        note
                        for note in (_clean_text(item, 300) for item in result.notes)
                        if note
                    )
                    invalid_quotes += outcome.invalid_quotes
                    filled_chars += outcome.filled_chars
                if failure_reason is not None:
                    logger.warning(
                        "剧本场景 %s 分块 %s/%s 回退：%s",
                        chunk.scene.scene_index,
                        chunk.index,
                        chunk.total,
                        failure_reason,
                    )
                    failed_chunks.append(
                        {
                            "scene": chunk.scene.scene_index,
                            "chunk": chunk.index,
                            "reason": failure_reason[:300],
                        }
                    )
                    fallback = self._fallback_chunk(
                        chunk, chapter.id, start_index=len(all_units)
                    )
                    all_units.extend(fallback.units)
                    chapter_reviews.extend(fallback.reviews)
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                    job.completed_items += 1
                    job.progress = min(1.0, job.completed_items / max(1, job.total_items))
                    job.updated_at = utc_now()
                    job.result_json = {
                        **dict(job.result_json or {}),
                        "units": len(all_units),
                        "failed_chunks": len(failed_chunks),
                    }

            if valid_chunks == 0:
                reason = str(last_error) if last_error is not None else "模型未返回可用的剧本结果"
                raise AppError(
                    f"剧本模型未返回可用结果：{_clean_text(reason, 300)}",
                    status_code=502,
                    code="script_llm_failed",
                )

            if failed_chunks:
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        code="script_llm_fallback",
                        severity="warning",
                        title="部分场景分块生成失败，已确定性回退",
                        message="模型调用或输出校验未通过，相关分块已按原文补齐为旁白/对白单元。",
                        payload={"chunks": failed_chunks, "count": len(failed_chunks)},
                    )
                )
            if invalid_quotes or filled_chars:
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        code="script_source_fill",
                        severity="warning",
                        title="部分原文未能按模型单元对齐，已补齐",
                        message="模型给出的原文片段无法对齐或未完整覆盖，后端已按原文顺序补齐，请抽查。",
                        payload={
                            "invalid_quotes": invalid_quotes,
                            "filled_chars": filled_chars,
                        },
                    )
                )
            unresolved = [
                plan
                for plan in all_units
                if plan.unit_type == "dialogue" and plan.speaker_type != "character"
            ]
            if unresolved:
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        code="script_unresolved_speaker",
                        severity="warning",
                        title="部分对白说话人未确认",
                        message="以下对白未匹配到角色注册表，已保留台词但说话人未知。",
                        payload={
                            "units": [
                                {
                                    "unit_index": plan.meta.get("unit_index"),
                                    "speaker_name": plan.speaker_name_snapshot,
                                    "source": _clean_text(plan.source_text, 80),
                                }
                                for plan in unresolved[:30]
                            ],
                            "count": len(unresolved),
                        },
                    )
                )
            low = [
                plan
                for plan in all_units
                if plan.confidence < self.confidence_threshold
            ]
            if low:
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        code="script_low_confidence",
                        severity="warning",
                        title="部分剧本单元置信度偏低",
                        message="低置信单元已保留，建议在制作方案中抽查表演字段与说话人。",
                        payload={
                            "units": [
                                plan.meta.get("unit_index") for plan in low[:50]
                            ],
                            "count": len(low),
                        },
                    )
                )
            for index, note in enumerate(
                list(dict.fromkeys(notes))[:NOTE_LIMIT], start=1
            ):
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        code=f"script_note:{index}",
                        severity="info",
                        title="剧本备注",
                        message=note,
                        payload={"chapter_id": chapter.id},
                    )
                )
            if not any(
                plan.unit_type in SCRIPT_TTS_TYPES and plan.text.strip()
                for plan in all_units
            ):
                raise AppError(
                    "生成的剧本没有可朗读单元",
                    status_code=502,
                    code="script_empty",
                )

            for index, plan in enumerate(all_units):
                plan.meta["unit_index"] = index

            self._finalize(
                job_id,
                chapter.id,
                version,
                scene_version,
                adaptation,
                source_hash,
                all_units,
                chapter_reviews,
                failed_chunks,
                invalid_quotes,
                filled_chars,
                calls,
                started,
                started_perf,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("剧本任务 %s 异常终止", job_id)
            try:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None:
                        return
                    if job.status == "cancelled":
                        chapter = session.get(Chapter, job.target_id or "")
                        if chapter is not None:
                            self._restore_chapter_status(session, chapter, cancelled=True)
                        return
                    job.status = "failed"
                    job.error_code = str(getattr(exc, "code", "script_failed"))
                    job.error_message = str(getattr(exc, "message", exc))[:2000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                    self._fail_script_job(session, job_id, exc)
            except Exception:  # noqa: BLE001
                logger.exception("写入剧本失败状态时出错")

    # --------------------------------------------------------------- planning

    def _plan_chunk(
        self,
        result: ChapterScriptResult,
        chunk: _Chunk,
        chapter_id: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
        *,
        start_index: int,
    ) -> _ChunkOutcome:
        units: list[_UnitPlan] = []
        reviews: list[_ReviewDraft] = []
        cursor = 0
        invalid = 0
        filled = 0
        states_by_character = {
            str(state.get("character_id") or ""): state
            for state in chunk.scene.states
            if state.get("character_id")
        }
        for draft in result.units:
            quote = (draft.source_text or "").strip()
            if not quote:
                invalid += 1
                continue
            local = chunk.text.find(quote, cursor)
            if local < 0:
                invalid += 1
                continue
            if local > cursor:
                gap = chunk.text[cursor:local]
                if gap.strip():
                    fill_units = self._fill_gap(
                        chunk,
                        cursor,
                        local,
                        chapter_id,
                        start_index=start_index + len(units),
                    )
                    units.extend(fill_units)
                    filled += len(gap.strip())
            source_text = chunk.text[local : local + len(quote)]
            source_start = chunk.abs_start + local
            source_end = source_start + len(source_text)
            plan = self._plan_unit(
                draft,
                chunk,
                source_text,
                source_start,
                source_end,
                chapter_id,
                canonical,
                aliases,
                states_by_character,
            )
            if plan is not None:
                plan.meta["unit_index"] = start_index + len(units)
                units.append(plan)
            cursor = local + len(quote)
        if cursor < len(chunk.text):
            tail = chunk.text[cursor:]
            if tail.strip():
                fill_units = self._fill_gap(
                    chunk,
                    cursor,
                    len(chunk.text),
                    chapter_id,
                    start_index=start_index + len(units),
                )
                units.extend(fill_units)
                filled += len(tail.strip())
        return _ChunkOutcome(
            units=units,
            reviews=reviews,
            calls=[],
            notes=[],
            invalid_quotes=invalid,
            filled_chars=filled,
        )

    def _plan_unit(
        self,
        draft: ScriptUnitDraft,
        chunk: _Chunk,
        source_text: str,
        source_start: int,
        source_end: int,
        chapter_id: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
        states_by_character: dict[str, dict[str, Any]],
    ) -> _UnitPlan | None:
        unit_type = str(draft.unit_type or "").strip().lower()
        if unit_type not in SCRIPT_UNIT_TYPES:
            unit_type = _candidate_type(source_text)
        if unit_type == "unknown":
            unit_type = _candidate_type(source_text)
        is_cue = unit_type in SCRIPT_CUE_TYPES
        raw_text = (draft.text or "").strip()
        if is_cue:
            text = ""
        else:
            text = raw_text or source_text
            text = " ".join(text.split())
        display = _clean_text(draft.display_text, 4000) or _clean_text(
            source_text, 4000
        )
        if is_cue and display:
            pass
        elif not is_cue and not text:
            return None

        speaker_type = "none" if is_cue else "unknown"
        speaker_id = None
        speaker_name = None
        state_id = None
        emotion = str(draft.emotion or "neutral").strip().lower()
        if emotion not in _EMOTIONS:
            emotion = "neutral"
        delivery = str(draft.delivery or "normal").strip().lower()
        if delivery not in _DELIVERIES:
            delivery = "normal"
        intensity = max(1, min(3, int(draft.intensity or 1)))
        speed = max(0.5, min(2.0, float(draft.speed or 1.0)))
        speaker_candidate = _clean_name(draft.speaker_name or "")
        if not is_cue:
            if unit_type in {"narration", "inner_monologue"}:
                speaker_type = "narrator"
            elif unit_type == "dialogue":
                if speaker_candidate and _norm(speaker_candidate) not in _UNSET_NAMES:
                    speaker_info, reason = self._resolve_character(
                        speaker_candidate, canonical, aliases
                    )
                    if speaker_info is not None:
                        speaker_type = "character"
                        speaker_id = speaker_info.id
                        speaker_name = speaker_info.canonical_name
                        state = states_by_character.get(speaker_info.id)
                        if state is not None:
                            state_id = str(state.get("id") or "") or None
                    else:
                        speaker_type = "unknown"
                        speaker_name = speaker_candidate
                else:
                    speaker_type = "unknown"
            elif speaker_type not in _SPEAKER_TYPES:
                speaker_type = "unknown"

        review_status = "auto"
        if unit_type == "unknown" or (unit_type == "dialogue" and speaker_type != "character"):
            review_status = "needs_review"
        if _clamp(draft.confidence) < self.confidence_threshold:
            review_status = "needs_review"
        evidence = _evidence_dicts(draft.evidence, chapter_id)
        if not evidence:
            evidence = [
                {
                    "chapter_id": chapter_id,
                    "quote": _clean_text(source_text, 100) or "",
                    "reason": "剧本单元对应原文范围",
                }
            ]
        sfx: dict[str, Any] = {}
        if is_cue:
            raw_sfx = draft.sfx if isinstance(draft.sfx, dict) else {}
            sfx = {
                "label": _clean_text(raw_sfx.get("label"), 80) or display or unit_type,
                "description": _clean_text(raw_sfx.get("description"), 300),
                "timing": _clean_text(raw_sfx.get("timing"), 24) or "during",
                "intensity": max(1, min(3, int(raw_sfx.get("intensity") or 1))),
            }
            if display:
                sfx["display_text"] = display
        return _UnitPlan(
            unit_type=unit_type,
            source_text=source_text,
            text=text,
            display_text=display,
            source_start_offset=source_start,
            source_end_offset=source_end,
            scene_id=chunk.scene.id,
            speaker_type=speaker_type if speaker_type in _SPEAKER_TYPES else "unknown",
            speaker_id=speaker_id,
            speaker_name_snapshot=speaker_name,
            character_state_id=state_id,
            emotion=emotion,
            intensity=intensity,
            delivery=delivery,
            tts_instruction=_clean_text(draft.tts_instruction, 1000),
            speed=speed,
            pause_before_ms=max(0, min(5000, int(draft.pause_before_ms or 0))),
            pause_after_ms=max(0, min(5000, int(draft.pause_after_ms or 250))),
            sfx_json=sfx,
            review_status=review_status,
            confidence=_clamp(draft.confidence),
            evidence=evidence,
            source="ai",
            meta={
                "prompt_version": SCRIPT_PROMPT_VERSION,
                "provider": self.registry.llm.name,
                "model": self.registry.llm.model,
                "is_mock": self.registry.llm.is_mock,
                "scene_index": chunk.scene.scene_index,
                "chunk": {"index": chunk.index, "total": chunk.total},
            },
        )

    def _fill_gap(
        self,
        chunk: _Chunk,
        start: int,
        end: int,
        chapter_id: str,
        *,
        start_index: int,
    ) -> list[_UnitPlan]:
        gap_text = chunk.text[start:end]
        if not gap_text.strip():
            return []
        units: list[_UnitPlan] = []
        for sentence in _SENTENCE.finditer(gap_text):
            raw = sentence.group(0)
            if not raw.strip():
                continue
            lead = len(raw) - len(raw.lstrip())
            trail = len(raw.rstrip())
            content = raw[lead:trail]
            if not content:
                continue
            local_start = start + sentence.start() + lead
            local_end = local_start + len(content)
            unit_type = _candidate_type(content)
            speaker_type = "narrator" if unit_type != "dialogue" else "unknown"
            text = content
            if unit_type == "dialogue":
                quoted = _QUOTED.search(content)
                if quoted and quoted.group(1).strip():
                    text = " ".join(quoted.group(1).split())
            units.append(
                _UnitPlan(
                    unit_type=unit_type,
                    source_text=content,
                    text=" ".join(text.split()),
                    display_text=content,
                    source_start_offset=chunk.abs_start + local_start,
                    source_end_offset=chunk.abs_start + local_end,
                    scene_id=chunk.scene.id,
                    speaker_type=speaker_type,
                    review_status="needs_review",
                    confidence=0.2,
                    evidence=[
                        {
                            "chapter_id": chapter_id,
                            "quote": _clean_text(content, 100) or "",
                            "reason": "模型未覆盖，按原文补齐",
                        }
                    ],
                    source="system",
                    meta={"fill": True, "unit_index": start_index + len(units)},
                )
            )
        return units

    def _fallback_chunk(
        self, chunk: _Chunk, chapter_id: str, *, start_index: int
    ) -> _ChunkOutcome:
        units = self._fill_gap(
            chunk,
            0,
            len(chunk.text),
            chapter_id,
            start_index=start_index,
        )
        return _ChunkOutcome(
            units=units,
            reviews=[],
            calls=[],
            notes=[],
            failed=True,
        )

    # --------------------------------------------------------------- finalize

    def _finalize(
        self,
        job_id: str,
        chapter_id: str,
        version: int,
        scene_version: int,
        adaptation: str,
        source_hash: str,
        plans: list[_UnitPlan],
        chapter_reviews: list[_ReviewDraft],
        failed_chunks: list[dict[str, Any]],
        invalid_quotes: int,
        filled_chars: int,
        calls: list[dict[str, Any]],
        started: str,
        started_perf: float,
    ) -> None:
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise NotFoundError("章节在剧本生成过程中被删除")
            exists = session.scalar(
                select(ScriptVersion.id).where(
                    ScriptVersion.chapter_id == chapter_id,
                    ScriptVersion.version == version,
                )
            )
            if exists is not None:
                raise ConflictError(
                    "当前剧本版本已存在，请重新发起生成",
                    code="script_version_conflict",
                )
            counts: dict[str, int] = {}
            for plan in plans:
                counts[plan.unit_type] = counts.get(plan.unit_type, 0) + 1
            version_row = ScriptVersion(
                id=new_id("script"),
                book_id=job.book_id,
                chapter_id=chapter_id,
                version=version,
                status="draft",
                adaptation_level=adaptation,
                source_scene_version=scene_version,
                source_hash=source_hash,
                prompt_version=SCRIPT_PROMPT_VERSION,
                model=self.registry.llm.model,
                summary_json={
                    "units": len(plans),
                    "types": counts,
                    "failed_chunks": len(failed_chunks),
                    "invalid_quotes": invalid_quotes,
                    "filled_chars": filled_chars,
                },
                is_active=False,
                source="ai",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(version_row)
            session.flush()

            unit_rows: list[ScriptUnit] = []
            for index, plan in enumerate(plans):
                audio_status = "not_applicable" if plan.is_cue else "none"
                row = ScriptUnit(
                    id=new_id("unit"),
                    book_id=job.book_id,
                    chapter_id=chapter_id,
                    script_version_id=version_row.id,
                    scene_id=plan.scene_id,
                    unit_index=index,
                    unit_type=plan.unit_type,
                    speaker_type=plan.speaker_type,
                    speaker_id=plan.speaker_id,
                    speaker_name_snapshot=plan.speaker_name_snapshot,
                    character_state_id=plan.character_state_id,
                    voice_cast_id=None,
                    source_text=plan.source_text,
                    text=plan.text,
                    display_text=plan.display_text,
                    source_start_offset=plan.source_start_offset,
                    source_end_offset=plan.source_end_offset,
                    emotion=plan.emotion,
                    intensity=plan.intensity,
                    delivery=plan.delivery,
                    tts_instruction=plan.tts_instruction,
                    speed=plan.speed,
                    pause_before_ms=plan.pause_before_ms,
                    pause_after_ms=plan.pause_after_ms,
                    sfx_json=plan.sfx_json,
                    review_status=plan.review_status,
                    confidence=plan.confidence,
                    audio_status=audio_status,
                    current_audio_asset_id=None,
                    render_hash=None,
                    user_locked_fields_json=[],
                    evidence_json=plan.evidence,
                    source=plan.source,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(row)
                unit_rows.append(row)
            session.flush()

            # 旧版本仍是历史资产，只清掉当前章节尚未再现的旧 AI 疑点。
            unit_ids = [
                row
                for row in session.scalars(
                    select(ScriptUnit.id).where(ScriptUnit.chapter_id == chapter_id)
                )
            ]
            stale_conditions = [
                and_(
                    ReviewItem.entity_type == "chapter",
                    ReviewItem.entity_id == chapter_id,
                )
            ]
            if unit_ids:
                stale_conditions.append(
                    and_(
                        ReviewItem.entity_type == "script_unit",
                        ReviewItem.entity_id.in_(unit_ids),
                    )
                )
            stale_items = list(
                session.scalars(
                    select(ReviewItem).where(
                        ReviewItem.book_id == job.book_id,
                        ReviewItem.status == "open",
                        ReviewItem.source == "ai",
                        ReviewItem.code.startswith("script_"),
                        or_(*stale_conditions),
                    )
                )
            )
            for item in stale_items:
                item.status = "ignored"
                item.resolution_note = "重新生成剧本后该疑点未再出现，已自动忽略。"
                item.resolved_at = utc_now()
                item.updated_at = utc_now()
            session.flush()
            for review in chapter_reviews:
                self._review(
                    session,
                    book_id=job.book_id,
                    entity_type=review.entity_type,
                    entity_id=review.entity_id or chapter_id,
                    review=review,
                )

            # 新版本自动成为当前版本；旧版本保留，可回退。
            previous = list(
                session.scalars(
                    select(ScriptVersion).where(
                        ScriptVersion.chapter_id == chapter_id,
                        ScriptVersion.is_active.is_(True),
                    )
                )
            )
            for old in previous:
                old.is_active = False
                old.updated_at = utc_now()
            session.flush()
            needs_review = any(
                review.severity in {"blocking", "warning"}
                for review in chapter_reviews
            ) or any(plan.review_status == "needs_review" for plan in plans)
            version_row.is_active = True
            version_row.status = "needs_review" if needs_review else "draft"
            chapter.script_status = "needs_review" if needs_review else "completed"
            chapter.updated_at = utc_now()
            job.status = "completed"
            job.completed_items = job.total_items
            job.progress = 1.0
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            job.result_json = {
                "chapter_id": chapter_id,
                "script_version": version,
                "scene_version": scene_version,
                "adaptation_level": adaptation,
                "units": len(plans),
                "types": counts,
                "tts_units": sum(
                    1 for plan in plans if plan.unit_type in SCRIPT_TTS_TYPES
                ),
                "cue_units": sum(
                    1 for plan in plans if plan.unit_type in SCRIPT_CUE_TYPES
                ),
                "failed_chunks": len(failed_chunks),
                "invalid_quotes": invalid_quotes,
                "filled_chars": filled_chars,
                "review_items": len(chapter_reviews),
                "prompt_version": SCRIPT_PROMPT_VERSION,
                "model": self.registry.llm.model,
                "is_mock": self.registry.llm.is_mock,
                "calls": calls,
                "started_at": started,
                "duration_ms": int((time.perf_counter() - started_perf) * 1000),
            }

    # --------------------------------------------------------------- queries

    def _active_version(self, session: Session, chapter_id: str) -> ScriptVersion | None:
        return session.scalar(
            select(ScriptVersion)
            .where(
                ScriptVersion.chapter_id == chapter_id,
                ScriptVersion.is_active.is_(True),
            )
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )

    def _select_version(
        self, session: Session, chapter_id: str, version: int | None
    ) -> ScriptVersion | None:
        if version is not None:
            return session.scalar(
                select(ScriptVersion).where(
                    ScriptVersion.chapter_id == chapter_id,
                    ScriptVersion.version == version,
                )
            )
        active = self._active_version(session, chapter_id)
        if active is not None:
            return active
        return session.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.chapter_id == chapter_id)
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )

    def _review_scope(
        self, session: Session, chapter: Chapter
    ) -> tuple[list[str], list[Any]]:
        unit_ids = list(
            session.scalars(
                select(ScriptUnit.id).where(ScriptUnit.chapter_id == chapter.id)
            )
        )
        conditions = [
            and_(
                ReviewItem.entity_type == "chapter",
                ReviewItem.entity_id == chapter.id,
            )
        ]
        if unit_ids:
            conditions.append(
                and_(
                    ReviewItem.entity_type == "script_unit",
                    ReviewItem.entity_id.in_(unit_ids),
                )
            )
        return unit_ids, conditions

    def _version_out(self, row: ScriptVersion, unit_count: int) -> ScriptVersionOut:
        return ScriptVersionOut(
            id=row.id,
            book_id=row.book_id,
            chapter_id=row.chapter_id,
            version=row.version,
            status=row.status,
            adaptation_level=row.adaptation_level,
            source_scene_version=row.source_scene_version,
            source_hash=row.source_hash,
            prompt_version=row.prompt_version,
            model=row.model,
            summary=dict(row.summary_json or {}),
            is_active=row.is_active,
            approved_at=row.approved_at,
            unit_count=unit_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _unit_out(
        self,
        row: ScriptUnit,
        speaker_name: str | None,
        voice: tuple[VoiceCast | None, VoiceActor | None] | None = None,
    ) -> ScriptUnitOut:
        actor = voice[1] if voice is not None else None
        return ScriptUnitOut(
            id=row.id,
            book_id=row.book_id,
            chapter_id=row.chapter_id,
            script_version_id=row.script_version_id,
            scene_id=row.scene_id,
            unit_index=row.unit_index,
            unit_type=row.unit_type,
            speaker_type=row.speaker_type,
            speaker_id=row.speaker_id,
            speaker_name=row.speaker_name_snapshot or speaker_name,
            character_state_id=row.character_state_id,
            voice_cast_id=row.voice_cast_id,
            voice_actor_id=actor.id if actor is not None else None,
            voice_actor_name=actor.display_name if actor is not None else None,
            source_text=row.source_text,
            text=row.text,
            display_text=row.display_text,
            source_start_offset=row.source_start_offset,
            source_end_offset=row.source_end_offset,
            emotion=row.emotion,
            intensity=row.intensity,
            delivery=row.delivery,
            tts_instruction=row.tts_instruction,
            speed=row.speed,
            pause_before_ms=row.pause_before_ms,
            pause_after_ms=row.pause_after_ms,
            sfx=dict(row.sfx_json or {}),
            review_status=row.review_status,
            confidence=row.confidence,
            audio_status=row.audio_status,
            current_audio_asset_id=row.current_audio_asset_id,
            render_hash=row.render_hash,
            user_locked_fields=list(row.user_locked_fields_json or []),
            evidence=list(row.evidence_json or []),
            source=row.source,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _review_out(self, item: ReviewItem) -> ScriptReviewItemOut:
        return ScriptReviewItemOut(
            id=item.id,
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            code=item.code,
            severity=item.severity,
            title=item.title,
            message=item.message,
            payload=dict(item.payload_json or {}),
            status=item.status,
            source=item.source,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    # ------------------------------------------------------------- fail/restore

    def _restore_chapter_status(
        self, session: Session, chapter: Chapter, *, cancelled: bool = False
    ) -> None:
        version = self._active_version(session, chapter.id)
        if version is None:
            chapter.script_status = "not_started" if cancelled else "failed"
        elif version.status in {"needs_review", "failed"}:
            chapter.script_status = "needs_review"
        else:
            chapter.script_status = "completed"
        chapter.updated_at = utc_now()

    def _fail_script_job(self, session: Session, job_id: str, error: Exception) -> None:
        job = session.get(GenerationJob, job_id)
        if job is None:
            return
        chapter = session.get(Chapter, job.target_id or "")
        if chapter is None:
            return
        self._restore_chapter_status(session, chapter)
        code = str(getattr(error, "code", "script_failed"))
        message = str(getattr(error, "message", error))[:1200]
        self._review(
            session,
            book_id=job.book_id,
            entity_type="chapter",
            entity_id=chapter.id,
            review=_ReviewDraft(
                entity_type="chapter",
                entity_id=chapter.id,
                code="script_analysis_failed",
                severity="blocking",
                title="有声剧本生成失败",
                message=message,
                payload={"error_code": code, "chapter_id": chapter.id},
            ),
        )

    def _review(
        self,
        session: Session,
        *,
        book_id: str,
        entity_type: str,
        entity_id: str | None,
        review: _ReviewDraft,
    ) -> ReviewItem:
        existing = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.entity_type == entity_type,
                ReviewItem.entity_id == entity_id,
                ReviewItem.code == review.code,
                ReviewItem.status == "open",
            )
        )
        if existing is None:
            item = ReviewItem(
                id=new_id("review"),
                book_id=book_id,
                entity_type=entity_type,
                entity_id=entity_id,
                code=review.code,
                severity=review.severity,
                title=review.title[:255],
                message=review.message,
                payload_json=review.payload,
                status="open",
                source="ai",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(item)
            session.flush()
            return item
        existing.severity = review.severity
        existing.title = review.title[:255]
        existing.message = review.message
        existing.payload_json = review.payload
        existing.updated_at = utc_now()
        return existing


def recover_interrupted_scripts(session: Session) -> dict[str, int]:
    """启动时把遗留剧本任务标记失败，并恢复章节状态。"""
    jobs = list(
        session.scalars(
            select(GenerationJob).where(
                GenerationJob.job_type == JOB_TYPE,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    )
    now = utc_now()
    chapter_ids: set[str] = set()
    for job in jobs:
        job.status = "failed"
        job.error_code = "interrupted"
        job.error_message = "应用在剧本生成过程中退出，任务已中断；请重新发起生成。"
        job.finished_at = now
        job.updated_at = now
        if job.target_id:
            chapter_ids.add(job.target_id)
    chapters = list(
        session.scalars(select(Chapter).where(Chapter.script_status == "running"))
    )
    chapter_ids.update(chapter.id for chapter in chapters)
    restored = 0
    for chapter_id in chapter_ids:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            continue
        version = session.scalar(
            select(ScriptVersion)
            .where(
                ScriptVersion.chapter_id == chapter.id,
                ScriptVersion.is_active.is_(True),
            )
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )
        if version is None:
            chapter.script_status = "failed"
        elif version.status in {"needs_review", "failed"}:
            chapter.script_status = "needs_review"
        else:
            chapter.script_status = "completed"
        chapter.updated_at = now
        restored += 1
    return {"jobs": len(jobs), "chapters": restored}