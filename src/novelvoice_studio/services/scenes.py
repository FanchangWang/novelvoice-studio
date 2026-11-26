"""v0.5.0 P3：逐章分场与场景内角色状态快照。

处理策略：

1. 先按角色圣经与章节摘要构造上下文，再把章节切成有编号的原文片段；
2. 模型必须完整、连续、无重叠地覆盖窗口内全部片段，否则确定性回退；
3. 角色姓名只映射到当前角色圣经中的标准姓名/别名，无法匹配的姓名进入异常；
4. 当前时间线场景用人物弧校正年龄阶段，倒叙/梦境不强行套用当前状态；
5. 每个场景的原文范围、置信度、证据落库；重跑生成新版本，旧版本保留。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.base import (
    ChapterSceneResult,
    EvidenceDraft,
    SceneCharacterStateDraft,
    SceneDraft,
)
from novelvoice_studio.ai.prompts import SCENE_PROMPT_VERSION

KNOWN_CHARACTER_CHARS = 6000
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
    BookBible,
    Chapter,
    Character,
    CharacterArc,
    GenerationJob,
    ReviewItem,
    Scene,
    SceneCharacter,
)
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.scenes import (
    SceneCharacterOut,
    SceneOut,
    SceneReviewItemOut,
    SceneStateResponse,
    SceneStatusResponse,
)
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.chapters import ParagraphSlice, split_paragraphs
from novelvoice_studio.services.paths import new_id, validate_id

logger = logging.getLogger(__name__)

JOB_TYPE = "chapter_scenes"
JOB_STAGE = "scene"
ACTIVE_JOB_STATUSES = ("pending", "running")
NOTE_LIMIT = 20
_TIME_MODES = {"present", "flashback", "flashforward", "dream", "unknown"}
_PRESENCE_VALUES = {"speaker", "present", "mentioned"}


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


@dataclass(frozen=True)
class _Unit:
    index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Window:
    index: int
    total: int
    units: tuple[_Unit, ...]

    @property
    def start(self) -> int:
        return self.units[0].start

    @property
    def end(self) -> int:
        return self.units[-1].end

    @property
    def text(self) -> str:
        return "\n".join(item.text for item in self.units)


@dataclass(frozen=True)
class _ChapterSource:
    id: str
    title: str
    sequence: int
    start: int
    end: int
    text: str
    paragraphs: tuple[ParagraphSlice, ...]
    summary: str | None


@dataclass(frozen=True)
class _ArcInfo:
    label: str
    age_stage: str
    chapter_start_id: str | None
    chapter_end_id: str | None
    story_order_start: int | None
    story_order_end: int | None
    story_time_start: str | None
    story_time_end: str | None
    voice_hint: str | None


@dataclass(frozen=True)
class _CharacterInfo:
    id: str
    name: str
    aliases: tuple[str, ...]
    gender: str
    age_range: str
    identity_description: str | None
    voice_description: str | None
    role_tier: str
    arcs: tuple[_ArcInfo, ...]


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
class _ResolvedState:
    character: _CharacterInfo
    state: SceneCharacterStateDraft


@dataclass
class _ScenePlan:
    index: int
    draft: SceneDraft
    start: int
    end: int
    states: list[_ResolvedState]
    pov_character_id: str | None
    story_order: int | None
    reviews: list[_ReviewDraft] = field(default_factory=list)
    source: str = "ai"
    fallback: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.fallback:
            return "needs_review"
        return (
            "needs_review"
            if any(item.severity in {"blocking", "warning"} for item in self.reviews)
            else "completed"
        )


def _split_long_paragraph(
    text: str, start: int, limit: int
) -> list[tuple[int, int, str]]:
    """超长段落按句末标点切分；找不到标点时硬切，确保窗口可控。"""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(text):
        hard_end = min(len(text), cursor + limit)
        if hard_end >= len(text):
            end = len(text)
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


def _prepare_units(
    paragraphs: tuple[ParagraphSlice, ...], limit: int
) -> list[_Unit]:
    units: list[_Unit] = []
    for paragraph in paragraphs:
        if len(paragraph.text) <= limit:
            candidates = [(paragraph.start, paragraph.end, paragraph.text)]
        else:
            candidates = _split_long_paragraph(paragraph.text, paragraph.start, limit)
        for start, end, text in candidates:
            if end <= start or not text.strip():
                continue
            units.append(_Unit(index=len(units), start=start, end=end, text=text))
    return units


def _build_windows(units: list[_Unit], limit: int) -> list[_Window]:
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    size = 0
    for unit in units:
        if current and size + len(unit.text) > limit:
            groups.append(current)
            current = []
            size = 0
        current.append(unit)
        size += len(unit.text)
    if current:
        groups.append(current)
    total = len(groups)
    return [
        _Window(index=index + 1, total=total, units=tuple(group))
        for index, group in enumerate(groups)
    ]


class SceneService:
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

    def _active_bible(self, session: Session, book_id: str) -> BookBible | None:
        return session.scalar(
            select(BookBible)
            .where(BookBible.book_id == book_id, BookBible.is_active.is_(True))
            .order_by(BookBible.version.desc())
            .limit(1)
        )

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

    def _read_chapter(
        self, book: Book, chapter: Chapter, source: str
    ) -> _ChapterSource:
        start, end = chapter.source_start_offset, chapter.source_end_offset
        if start < 0 or end <= start or end > len(source):
            raise AppError(
                f"章节「{chapter.title}」偏移与源文本不一致，可能文件已被修改",
                status_code=500,
                code="source_offset_mismatch",
            )
        text = source[start:end]
        if not text.strip():
            raise ValidationError(
                f"章节「{chapter.title}」没有正文，无法生成分场",
                code="chapter_empty",
            )
        paragraphs = tuple(split_paragraphs(source, start, end))
        if not paragraphs:
            raise ValidationError(
                f"章节「{chapter.title}」没有可用段落，无法生成分场",
                code="chapter_empty",
            )
        return _ChapterSource(
            id=chapter.id,
            title=chapter.title,
            sequence=chapter.sequence,
            start=start,
            end=end,
            text=text,
            paragraphs=paragraphs,
            summary=_clean_text(chapter.summary, 4000),
        )

    def _timeline_row(self, bible: BookBible, chapter_id: str) -> dict[str, Any] | None:
        row: dict[str, Any] | None = None
        for item in (bible.content_json or {}).get("chapter_timelines") or []:
            if isinstance(item, dict) and str(item.get("chapter_id") or "") == chapter_id:
                row = dict(item)
                break
        if row is None:
            return None
        # P2 的时间线概览（timeline_json）才有最终 story_order；内容行不一定带。
        for item in (bible.timeline_json or {}).get("chapters") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("chapter_id") or "") != chapter_id:
                continue
            for field in (
                "story_order",
                "story_time_label",
                "time_mode",
                "title",
                "sequence",
                "summary",
            ):
                current = row.get(field)
                incoming = item.get(field)
                if (current is None or current == "" or current == 0) and (
                    incoming is not None and incoming != ""
                ):
                    row[field] = incoming
            break
        return row

    def _require_bible_context(
        self, session: Session, chapter: Chapter
    ) -> tuple[BookBible, dict[str, Any]]:
        bible = self._active_bible(session, chapter.book_id)
        if bible is None or bible.status not in {"approved", "needs_review"}:
            raise ValidationError(
                "请先生成并确认全书角色圣经，再进行分场分析",
                code="bible_required",
            )
        row = self._timeline_row(bible, chapter.id)
        if row is None:
            raise ValidationError(
                "当前角色圣经未覆盖该章节（可能是新拆分/新增章节），请先重新生成角色圣经",
                code="bible_chapter_missing",
            )
        return bible, row

    def _load_characters(
        self,
        session: Session,
        book_id: str,
        bible_id: str,
        chapter: Chapter,
        story_order: int | None,
    ) -> tuple[list[_CharacterInfo], dict[str, int]]:
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
        arcs = list(
            session.scalars(
                select(CharacterArc)
                .where(
                    CharacterArc.book_id == book_id,
                    CharacterArc.bible_id == bible_id,
                )
                .order_by(CharacterArc.arc_index.asc())
            )
        )
        sequences = dict(
            session.execute(
                select(Chapter.id, Chapter.sequence).where(Chapter.book_id == book_id)
            ).all()
        )
        arcs_by_character: dict[str, list[_ArcInfo]] = {}
        for arc in arcs:
            voice_hint = _clean_text(
                (arc.voice_traits_json or {}).get("voice_hint")
                or (arc.voice_traits_json or {}).get("description"),
                255,
            )
            arcs_by_character.setdefault(arc.character_id, []).append(
                _ArcInfo(
                    label=arc.label,
                    age_stage=arc.age_stage,
                    chapter_start_id=arc.chapter_start_id,
                    chapter_end_id=arc.chapter_end_id,
                    story_order_start=arc.story_order_start,
                    story_order_end=arc.story_order_end,
                    story_time_start=arc.story_time_start,
                    story_time_end=arc.story_time_end,
                    voice_hint=voice_hint,
                )
            )
        infos: list[_CharacterInfo] = []
        for character in characters:
            aliases: list[str] = []
            for alias in character.aliases_json or []:
                name = alias.get("name") if isinstance(alias, dict) else alias
                cleaned = _clean_name(name)
                if cleaned and cleaned != character.canonical_name and cleaned not in aliases:
                    aliases.append(cleaned)
            infos.append(
                _CharacterInfo(
                    id=character.id,
                    name=character.canonical_name,
                    aliases=tuple(aliases),
                    gender=character.gender,
                    age_range=character.age_range,
                    identity_description=character.identity_description,
                    voice_description=character.voice_description,
                    role_tier=character.role_tier,
                    arcs=tuple(arcs_by_character.get(character.id, [])),
                )
            )
        return infos, sequences

    def _known_payload(
        self,
        infos: list[_CharacterInfo],
        sequences: dict[str, int],
        chapter_text: str = "",
        story_order: int | None = None,
    ) -> list[dict[str, Any]]:
        """只把本章真正出现/可能被指代的角色送进提示词。

        全书 100+ 角色时，完整注册表会撑爆 12k 上下文；按姓名命中过滤，
        再对字段做压缩，保证单次 prompt 有足够空间留给原文。
        """
        scored: list[tuple[int, _CharacterInfo]] = []
        for info in infos:
            hits = 0
            for name in (info.name, *info.aliases):
                if name and len(name) >= 2:
                    hits += chapter_text.count(name)
            scored.append((hits, info))
        selected = [info for hits, info in scored if hits > 0]
        hit_map = {id(info): hits for hits, info in scored}
        selected.sort(key=lambda info: -hit_map.get(id(info), 0))
        if len(selected) < 8:
            for tier in ("protagonist", "major", "supporting"):
                for _hits, info in scored:
                    if info.role_tier == tier and info not in selected:
                        selected.append(info)
                    if len(selected) >= 24:
                        break
                if len(selected) >= 24:
                    break
        selected = selected[:48]
        payload: list[dict[str, Any]] = []
        budget = KNOWN_CHARACTER_CHARS
        for info in selected:
            arcs = list(info.arcs)
            if story_order:
                overlapping = [
                    arc
                    for arc in arcs
                    if (arc.story_order_start is None or arc.story_order_start <= story_order)
                    and (arc.story_order_end is None or arc.story_order_end >= story_order)
                ]
                arcs = overlapping or arcs[:1]
            item = {
                "canonical_name": info.name,
                "aliases": list(info.aliases)[:6],
                "gender": info.gender,
                "age_range": info.age_range,
                "role_tier": info.role_tier,
                "identity_description": _clean_text(info.identity_description, 60),
                "voice_hint": _clean_text(info.voice_description, 40),
                "arcs": [
                    {
                        "label": arc.label,
                        "age_stage": arc.age_stage,
                        "story_time_start": arc.story_time_start,
                        "story_time_end": arc.story_time_end,
                        "voice_hint": _clean_text(arc.voice_hint, 40),
                    }
                    for arc in arcs[:2]
                ],
            }
            cost = len(json.dumps(item, ensure_ascii=False))
            if payload and budget - cost < 0:
                break
            budget -= cost
            payload.append(item)
        return payload

    def _chapter_time(self, row: dict[str, Any]) -> dict[str, Any]:
        mode = str(row.get("time_mode") or "unknown")
        return {
            "story_time_label": _clean_text(row.get("story_time_label"), 120),
            "time_mode": mode if mode in _TIME_MODES else "unknown",
            "story_order": int(row["story_order"]) if row.get("story_order") else None,
        }

    def _arc_for_chapter(
        self,
        info: _CharacterInfo,
        chapter: _ChapterSource,
        story_order: int | None,
        sequences: dict[str, int],
    ):
        matches: list[_ArcInfo] = []
        for arc in info.arcs:
            start_sequence = sequences.get(arc.chapter_start_id or "")
            end_sequence = sequences.get(arc.chapter_end_id or "")
            chapter_match = False
            if start_sequence is not None and end_sequence is not None:
                chapter_match = start_sequence <= chapter.sequence <= end_sequence
            elif start_sequence is not None:
                chapter_match = chapter.sequence >= start_sequence
            elif end_sequence is not None:
                chapter_match = chapter.sequence <= end_sequence
            if not chapter_match and story_order is not None:
                if (
                    arc.story_order_start is not None
                    and arc.story_order_end is not None
                    and arc.story_order_start <= story_order <= arc.story_order_end
                ):
                    chapter_match = True
            if chapter_match:
                matches.append(arc)
        if matches:
            return matches[0]
        if len(info.arcs) == 1:
            return info.arcs[0]
        return None

    def _registry(
        self, infos: list[_CharacterInfo]
    ) -> tuple[dict[str, list[_CharacterInfo]], dict[str, list[_CharacterInfo]]]:
        canonical: dict[str, list[_CharacterInfo]] = {}
        aliases: dict[str, list[_CharacterInfo]] = {}
        for info in infos:
            canonical.setdefault(_norm(info.name), []).append(info)
            for alias in info.aliases:
                aliases.setdefault(_norm(alias), []).append(info)
        return canonical, aliases

    def _resolve_character(
        self,
        name: str,
        canonical: dict[str, list[_CharacterInfo]],
        aliases: dict[str, list[_CharacterInfo]],
    ) -> tuple[_CharacterInfo | None, str | None]:
        key = _norm(name)
        if not key:
            return None, "blank"
        candidates = canonical.get(key) or aliases.get(key) or []
        unique = {item.id: item for item in candidates}
        if len(unique) == 1:
            return next(iter(unique.values())), None
        if len(unique) > 1:
            return None, "ambiguous"
        return None, "unknown"

    # ------------------------------------------------------------ start/cancel

    def start_scene_analysis(self, chapter_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            chapter, book = self._chapter(session, chapter_id)
            if self._active_job(session, chapter.id) is not None:
                raise ConflictError(
                    "本章正在生成分场，请等待当前任务结束或先取消",
                    code="scene_analysis_running",
                )
            bible, row = self._require_bible_context(session, chapter)
            source = BookService(self.settings, session).read_source(book)
            chapter_source = self._read_chapter(book, chapter, source)
            units = _prepare_units(chapter_source.paragraphs, self.window_chars)
            windows = _build_windows(units, self.window_chars)
            if not units or not windows:
                raise ValidationError("章节没有可用段落，无法生成分场", code="chapter_empty")
            max_version = int(
                session.scalar(
                    select(func.max(Scene.analysis_version)).where(
                        Scene.chapter_id == chapter.id
                    )
                )
                or 0
            )
            version = max(chapter.scene_version, max_version) + 1
            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=chapter.id,
                target_type="chapter",
                stage=JOB_STAGE,
                job_type=JOB_TYPE,
                status="pending",
                total_items=len(windows),
                completed_items=0,
                failed_items=0,
                progress=0.0,
                attempt_count=0,
                max_attempts=1,
                params_json={
                    "chapter_id": chapter.id,
                    "scene_version": version,
                    "bible_id": bible.id,
                    "script_version": chapter.script_status,
                    "windows": len(windows),
                    "units": len(units),
                    "source_start": chapter_source.start,
                    "source_end": chapter_source.end,
                    "chapter_time": self._chapter_time(row),
                },
                result_json={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            chapter.scene_status = "running"
            chapter.updated_at = utc_now()
            session.flush()
            output = to_job_out(job)

        thread = threading.Thread(
            target=self._run_job,
            args=(output.id,),
            name=f"novelvoice-scenes-{output.id}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001
            logger.exception("无法启动分场后台线程")
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, output.id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = "thread_start_failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                self._fail_scene_job(session, output.id, exc)
            raise AppError(
                "无法启动分场后台线程",
                status_code=500,
                code="scene_thread_failed",
            ) from exc
        return output

    def cancel_scene_analysis(self, chapter_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            chapter, _book = self._chapter(session, chapter_id)
            job = self._active_job(session, chapter.id)
            if job is None:
                raise ConflictError(
                    "本章没有正在生成的分场任务", code="no_active_job"
                )
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            self._restore_chapter_status(session, chapter, cancelled=True)
            return to_job_out(job)

    # ---------------------------------------------------------------- status

    def scene_status(self, chapter_id: str) -> SceneStatusResponse:
        with session_scope(self.session_factory) as session:
            chapter, book = self._chapter(session, chapter_id)
            job = self._latest_job(session, chapter.id)
            active = job is not None and job.status in ACTIVE_JOB_STATUSES
            scene_ids, conditions = self._review_scope(session, chapter)
            open_reviews = int(
                session.scalar(
                    select(func.count(ReviewItem.id)).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                        ReviewItem.code.startswith("scene_"),
                        or_(*conditions),
                    )
                )
                or 0
            )
            return SceneStatusResponse(
                chapter_id=chapter.id,
                book_id=book.id,
                scene_status="running" if active else chapter.scene_status,
                scene_version=chapter.scene_version,
                open_reviews=open_reviews,
                job=to_job_out(job) if job else None,
            )

    def get_scenes(self, chapter_id: str) -> SceneStateResponse:
        with session_scope(self.session_factory) as session:
            chapter, book = self._chapter(session, chapter_id)
            job = self._latest_job(session, chapter.id)
            active = job is not None and job.status in ACTIVE_JOB_STATUSES
            scene_rows = list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.chapter_id == chapter.id,
                        Scene.analysis_version == chapter.scene_version,
                    )
                    .order_by(Scene.scene_index.asc())
                )
            ) if chapter.scene_version > 0 else []
            scene_ids = [row.id for row in scene_rows]
            states: list[SceneCharacter] = []
            if scene_ids:
                states = list(
                    session.scalars(
                        select(SceneCharacter)
                        .where(SceneCharacter.scene_id.in_(scene_ids))
                        .order_by(SceneCharacter.created_at.asc(), SceneCharacter.id.asc())
                    )
                )
            character_ids = {row.character_id for row in states if row.character_id}
            character_ids.update(
                row.pov_character_id for row in scene_rows if row.pov_character_id
            )
            names: dict[str, str] = {}
            if character_ids:
                names = dict(
                    session.execute(
                        select(Character.id, Character.canonical_name).where(
                            Character.id.in_(character_ids)
                        )
                    ).all()
                )
            states_by_scene: dict[str, list[SceneCharacter]] = {}
            for row in states:
                states_by_scene.setdefault(row.scene_id, []).append(row)
            items = [
                self._serialize_scene(
                    row,
                    states_by_scene.get(row.id, []),
                    names.get(row.pov_character_id) if row.pov_character_id else None,
                    names,
                )
                for row in scene_rows
            ]
            all_scene_ids, conditions = self._review_scope(session, chapter)
            reviews = list(
                session.scalars(
                    select(ReviewItem)
                    .where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                        ReviewItem.code.startswith("scene_"),
                        or_(*conditions),
                    )
                    .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
                )
            )
            return SceneStateResponse(
                chapter_id=chapter.id,
                book_id=book.id,
                scene_status="running" if active else chapter.scene_status,
                scene_version=chapter.scene_version,
                open_reviews=len(reviews),
                job=to_job_out(job) if job else None,
                scenes=items,
                review_items=[self._review_out(item) for item in reviews],
            )

    def _review_scope(
        self, session: Session, chapter: Chapter
    ) -> tuple[list[str], list[Any]]:
        scene_ids = list(
            session.scalars(select(Scene.id).where(Scene.chapter_id == chapter.id))
        )
        conditions = [
            and_(
                ReviewItem.entity_type == "chapter",
                ReviewItem.entity_id == chapter.id,
            )
        ]
        if scene_ids:
            conditions.append(
                and_(
                    ReviewItem.entity_type == "scene",
                    ReviewItem.entity_id.in_(scene_ids),
                )
            )
        return scene_ids, conditions

    def _serialize_scene(
        self,
        row: Scene,
        states: list[SceneCharacter],
        pov_name: str | None,
        names: dict[str, str],
    ) -> SceneOut:
        return SceneOut(
            id=row.id,
            book_id=row.book_id,
            chapter_id=row.chapter_id,
            analysis_version=row.analysis_version,
            scene_index=row.scene_index,
            title=row.title,
            summary=row.summary,
            location=row.location,
            story_time_label=row.story_time_label,
            story_order=row.story_order,
            time_mode=row.time_mode,
            pov_character_id=row.pov_character_id,
            pov_character_name=pov_name,
            mood=row.mood,
            source_start_offset=row.source_start_offset,
            source_end_offset=row.source_end_offset,
            analysis_status=row.analysis_status,
            confidence=row.confidence,
            evidence=list(row.evidence_json or []),
            meta=dict(row.meta_json or {}),
            source=row.source,
            characters=[
                SceneCharacterOut(
                    id=state.id,
                    book_id=state.book_id,
                    scene_id=state.scene_id,
                    character_id=state.character_id,
                    character_name=names.get(state.character_id),
                    state_phase=state.state_phase,
                    presence=state.presence,
                    age_stage=state.age_stage,
                    physical_state=state.physical_state,
                    emotional_state=state.emotional_state,
                    relationship_state=state.relationship_state,
                    knowledge_state=state.knowledge_state,
                    disguise_state=state.disguise_state,
                    voice_note=state.voice_note,
                    state_tags=list(state.state_tags_json or []),
                    confidence=state.confidence,
                    evidence=list(state.evidence_json or []),
                    source=state.source,
                    created_at=state.created_at,
                    updated_at=state.updated_at,
                )
                for state in states
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _review_out(self, item: ReviewItem) -> SceneReviewItemOut:
        return SceneReviewItemOut(
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
                bible, row = self._require_bible_context(session, chapter)
                source = BookService(self.settings, session).read_source(book)
                chapter_source = self._read_chapter(book, chapter, source)
                units = _prepare_units(chapter_source.paragraphs, self.window_chars)
                windows = _build_windows(units, self.window_chars)
                if not windows:
                    raise ValidationError(
                        "章节没有可用段落，无法生成分场", code="chapter_empty"
                    )
                chapter_time = self._chapter_time(row)
                infos, sequences = self._load_characters(
                    session,
                    book.id,
                    bible.id,
                    chapter,
                    chapter_time["story_order"],
                )
                known = self._known_payload(
                    infos,
                    sequences,
                    "".join(unit.text for unit in units),
                    chapter_time["story_order"],
                )
                all_chapters = list(
                    session.scalars(
                        select(Chapter)
                        .where(Chapter.book_id == book.id)
                        .order_by(Chapter.sequence.asc())
                    )
                )
                summary_by_sequence = {
                    item.sequence: _clean_text(item.summary, 800)
                    for item in all_chapters
                }
                previous_summary = _clean_text(
                    summary_by_sequence.get(chapter_source.sequence - 1), 500
                )
                next_summary = _clean_text(
                    summary_by_sequence.get(chapter_source.sequence + 1), 500
                )
                chapter_summary = _clean_text(
                    chapter_source.summary or row.get("summary"), 1200
                )
                book_title = book.title
                bible_id = bible.id
                version = int((job.params_json or {}).get("scene_version") or 0)
                if version <= 0:
                    version = max(
                        chapter.scene_version,
                        int(
                            session.scalar(
                                select(func.max(Scene.analysis_version)).where(
                                    Scene.chapter_id == chapter.id
                                )
                            )
                            or 0
                        ),
                    ) + 1
                    params = dict(job.params_json or {})
                    params["scene_version"] = version
                    job.params_json = params
                job.total_items = len(windows)
                job.updated_at = utc_now()

            canonical, aliases = self._registry(infos)
            plans: list[_ScenePlan] = []
            chapter_reviews: list[_ReviewDraft] = []
            calls: list[dict[str, Any]] = []
            notes: list[str] = []
            fallback_windows: list[dict[str, Any]] = []
            valid_windows = 0
            last_error: Exception | None = None

            for window in windows:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                prompt_paragraphs = [
                    {"index": unit.index, "text": unit.text} for unit in window.units
                ]
                context = {
                    "book_title": book_title,
                    "chapter_title": chapter_source.title,
                    "chapter_sequence": chapter_source.sequence,
                    "chapter_summary": chapter_summary,
                    "previous_summary": previous_summary,
                    "next_summary": next_summary,
                    "chapter_time": chapter_time,
                    "window": {"index": window.index, "total": window.total},
                }
                result: ChapterSceneResult | None = None
                failure_reason: str | None = None
                try:
                    result = self.registry.llm.analyze_chapter_scenes(
                        prompt_paragraphs, known, context
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    failure_reason = (
                        f"{type(exc).__name__}: "
                        f"{_clean_text(getattr(exc, 'message', str(exc)), 300)}"
                    )
                if result is not None:
                    coverage_error = self._coverage_error(result, window)
                    if coverage_error:
                        failure_reason = coverage_error
                    else:
                        valid_windows += 1
                        calls.append(
                            {
                                "window": window.index,
                                "call": dict(result.meta or {}),
                            }
                        )
                        window_plans = self._plan_window(
                            result,
                            window,
                            chapter_source,
                            chapter_time,
                            infos,
                            canonical,
                            aliases,
                            sequences,
                            start_index=len(plans),
                        )
                        plans.extend(window_plans)
                        notes.extend(
                            note
                            for note in (
                                _clean_text(item, 300) for item in result.notes
                            )
                            if note
                        )
                if failure_reason is not None:
                    logger.warning(
                        "分场窗口 %s/%s 回退：%s",
                        window.index,
                        window.total,
                        failure_reason,
                    )
                    fallback_windows.append(
                        {"window": window.index, "reason": failure_reason[:300]}
                    )
                    plans.append(
                        self._fallback_plan(
                            window,
                            chapter_source,
                            chapter_time,
                            infos,
                            sequences,
                            start_index=len(plans),
                            reason=failure_reason,
                        )
                    )
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                    job.completed_items = window.index
                    job.progress = min(1.0, window.index / max(1, window.total))
                    job.updated_at = utc_now()
                    job.result_json = {
                        **dict(job.result_json or {}),
                        "last_window": window.index,
                        "scenes": len(plans),
                        "fallback_windows": len(fallback_windows),
                    }

            if valid_windows == 0:
                reason = str(last_error) if last_error is not None else "模型未返回可用的分场结果"
                raise AppError(
                    f"分场模型未返回可用结果：{_clean_text(reason, 300)}",
                    status_code=502,
                    code="scene_llm_failed",
                )

            if fallback_windows:
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        entity_id=chapter_source.id,
                        code="scene_llm_fallback",
                        severity="warning",
                        title="部分窗口分场失败，已确定性回退",
                        message="模型调用或输出校验未通过，相关窗口已按原文顺序整段回退为单场景。",
                        payload={
                            "windows": fallback_windows,
                            "count": len(fallback_windows),
                        },
                    )
                )
            for index, note in enumerate(
                list(dict.fromkeys(notes))[:NOTE_LIMIT], start=1
            ):
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        entity_id=chapter_source.id,
                        code=f"scene_note:{index}",
                        severity="info",
                        title="分场备注",
                        message=note,
                        payload={"chapter_id": chapter_source.id},
                    )
                )
            if not any(plan.states for plan in plans):
                chapter_reviews.append(
                    _ReviewDraft(
                        entity_type="chapter",
                        entity_id=chapter_source.id,
                        code="scene_no_characters",
                        severity="warning",
                        title="未识别到场景角色",
                        message="分场没有匹配到任何角色状态，请检查角色圣经是否覆盖本章角色。",
                        payload={"chapter_id": chapter_source.id},
                    )
                )

            self._finalize(
                job_id,
                chapter_source,
                version,
                bible_id,
                plans,
                chapter_reviews,
                fallback_windows,
                calls,
                started,
                started_perf,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("分场任务 %s 异常终止", job_id)
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
                    job.error_code = str(getattr(exc, "code", "scene_failed"))
                    job.error_message = str(getattr(exc, "message", exc))[:2000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                    self._fail_scene_job(session, job_id, exc)
            except Exception:  # noqa: BLE001
                logger.exception("写入分场失败状态时出错")

    # --------------------------------------------------------------- planning

    def _coverage_error(
        self, result: ChapterSceneResult, window: _Window
    ) -> str | None:
        if not result.scenes:
            return "模型未返回场景"
        expected = [unit.index for unit in window.units]
        allowed = set(expected)
        scenes = sorted(
            result.scenes,
            key=lambda item: (item.paragraph_start, item.paragraph_end),
        )
        cursor = 0
        for scene in scenes:
            if scene.paragraph_start not in allowed or scene.paragraph_end not in allowed:
                return (
                    f"场景范围 {scene.paragraph_start}-{scene.paragraph_end} "
                    f"超出窗口片段范围 {expected[0]}-{expected[-1]}"
                )
            if scene.paragraph_start != expected[cursor]:
                return (
                    f"场景范围不连续：期望从片段 {expected[cursor]} 开始，"
                    f"实际为 {scene.paragraph_start}"
                )
            try:
                end_position = expected.index(scene.paragraph_end, cursor)
            except ValueError:
                return f"场景结束片段 {scene.paragraph_end} 顺序无效"
            cursor = end_position + 1
        if cursor != len(expected):
            return "场景未覆盖窗口内全部片段"
        return None

    def _plan_window(
        self,
        result: ChapterSceneResult,
        window: _Window,
        chapter: _ChapterSource,
        chapter_time: dict[str, Any],
        infos: list[_CharacterInfo],
        canonical: dict[str, list[_CharacterInfo]],
        aliases: dict[str, list[_CharacterInfo]],
        sequences: dict[str, int],
        *,
        start_index: int,
    ) -> list[_ScenePlan]:
        units_by_index = {unit.index: unit for unit in window.units}
        plans: list[_ScenePlan] = []
        for offset, draft in enumerate(
            sorted(result.scenes, key=lambda item: (item.paragraph_start, item.paragraph_end))
        ):
            start_unit = units_by_index[draft.paragraph_start]
            end_unit = units_by_index[draft.paragraph_end]
            time_mode = draft.time_mode
            if time_mode == "unknown":
                time_mode = str(chapter_time.get("time_mode") or "unknown")
            if time_mode not in _TIME_MODES:
                time_mode = "unknown"
            story_time_label = _clean_text(
                draft.story_time_label, 120
            ) or chapter_time.get("story_time_label")
            story_order = chapter_time.get("story_order")
            location = _clean_text(draft.location, 255)
            if location and location.casefold() in {"unknown", "未知", "null", "none"}:
                location = None
            mood = _clean_text(draft.mood, 64)
            if mood and mood.casefold() in {"unknown", "未知", "null", "none"}:
                mood = None
            scene_text = "\n".join(
                units_by_index[unit.index].text
                for unit in window.units
                if draft.paragraph_start <= unit.index <= draft.paragraph_end
            )
            title = _clean_text(draft.title, 255) or _clean_text(scene_text, 40)
            summary = _clean_text(draft.summary, 4000) or _clean_text(scene_text, 400)
            pov_id: str | None = None
            pov_unknown: str | None = None
            pov_name = _clean_name(draft.pov_name or "")
            if pov_name and _norm(pov_name) not in {
                "无",
                "unknown",
                "narrator",
                "none",
                "null",
                "旁白",
                "未知",
            }:
                pov_info, pov_reason = self._resolve_character(
                    pov_name, canonical, aliases
                )
                if pov_info is not None:
                    pov_id = pov_info.id
                else:
                    pov_unknown = pov_name

            resolved_states: list[_ResolvedState] = []
            unknown_names: list[dict[str, str]] = []
            age_conflicts: list[dict[str, str]] = []
            seen: dict[str, _ResolvedState] = {}
            state_positions: dict[str, int] = {}

            def prepare_state(
                state: SceneCharacterStateDraft, info: _CharacterInfo
            ) -> dict[str, str] | None:
                arc = self._arc_for_chapter(info, chapter, story_order, sequences)
                conflict: dict[str, str] | None = None
                if time_mode == "present":
                    target_stage = (
                        arc.age_stage
                        if arc is not None and arc.age_stage != "unknown"
                        else info.age_range
                    )
                    if target_stage != "unknown":
                        if (
                            state.age_stage != "unknown"
                            and state.age_stage != target_stage
                        ):
                            conflict = {
                                "name": info.name,
                                "model": state.age_stage,
                                "bible": target_stage,
                            }
                        state.age_stage = target_stage
                if not state.voice_note:
                    state.voice_note = (
                        (arc.voice_hint if arc is not None else None)
                        or info.voice_description
                    )
                state.physical_state = _clean_text(state.physical_state, 255)
                state.emotional_state = _clean_text(state.emotional_state, 255)
                state.relationship_state = _clean_text(state.relationship_state, 255)
                state.knowledge_state = _clean_text(state.knowledge_state, 1000)
                state.disguise_state = _clean_text(state.disguise_state, 255)
                state.voice_note = _clean_text(state.voice_note, 255)
                state.state_tags = list(dict.fromkeys(
                    tag for tag in (_clean_text(item, 32) for item in state.state_tags) if tag
                ))[:12]
                return conflict

            for state in draft.characters:
                name = _clean_name(state.name)
                if not name:
                    continue
                info, reason = self._resolve_character(name, canonical, aliases)
                if info is None:
                    unknown_names.append(
                        {
                            "name": name,
                            "reason": "ambiguous" if reason == "ambiguous" else "unknown",
                        }
                    )
                    continue
                existing = seen.get(info.id)
                if existing is not None and state.confidence <= existing.state.confidence:
                    continue
                conflict = prepare_state(state, info)
                resolved = _ResolvedState(character=info, state=state)
                if existing is not None:
                    resolved_states[state_positions[info.id]] = resolved
                else:
                    state_positions[info.id] = len(resolved_states)
                    resolved_states.append(resolved)
                seen[info.id] = resolved
                if conflict is not None and not any(
                    item["name"] == conflict["name"] for item in age_conflicts
                ):
                    age_conflicts.append(conflict)

            reviews: list[_ReviewDraft] = []
            if unknown_names:
                reviews.append(
                    _ReviewDraft(
                        entity_type="scene",
                        code="scene_unknown_character",
                        severity="warning",
                        title="场景角色未匹配到角色圣经",
                        message="以下姓名不在角色注册表中，状态已被跳过，请确认是否漏建角色。",
                        payload={"names": unknown_names},
                    )
                )
            if age_conflicts:
                reviews.append(
                    _ReviewDraft(
                        entity_type="scene",
                        code="scene_age_stage_conflict",
                        severity="warning",
                        title="角色年龄阶段与人物弧不一致",
                        message="当前时间线场景已按人物弧校正年龄阶段，请确认差异是否来自倒叙或回忆。",
                        payload={"conflicts": age_conflicts},
                    )
                )
            low_states = [
                item.character.name
                for item in resolved_states
                if item.state.confidence < self.confidence_threshold
            ]
            if (
                draft.confidence < self.confidence_threshold
                or low_states
            ):
                reviews.append(
                    _ReviewDraft(
                        entity_type="scene",
                        code="scene_low_confidence",
                        severity="warning",
                        title="场景或角色状态置信度偏低",
                        message="低置信场景/状态已保留，建议抽查分场边界与角色状态。",
                        payload={
                            "scene_confidence": draft.confidence,
                            "low_states": low_states,
                        },
                    )
                )
            if pov_unknown:
                reviews.append(
                    _ReviewDraft(
                        entity_type="scene",
                        code="scene_pov_unknown",
                        severity="info",
                        title="场景视角未确定",
                        message=f"模型给出的视角角色「{pov_unknown}」不在角色注册表中，已置空。",
                        payload={"pov_name": pov_unknown},
                    )
                )
            evidence = _evidence_dicts(draft.evidence, chapter.id)
            if not evidence:
                evidence = [
                    {
                        "chapter_id": chapter.id,
                        "quote": _clean_text(scene_text, 100) or "",
                        "reason": "场景对应原文范围",
                    }
                ]
            plans.append(
                _ScenePlan(
                    index=start_index + offset,
                    draft=SceneDraft(
                        paragraph_start=draft.paragraph_start,
                        paragraph_end=draft.paragraph_end,
                        title=title,
                        summary=summary,
                        location=location,
                        story_time_label=story_time_label,
                        time_mode=time_mode,
                        pov_name=draft.pov_name,
                        mood=mood,
                        characters=[item.state for item in resolved_states],
                        confidence=draft.confidence,
                        evidence=draft.evidence,
                    ),
                    start=start_unit.start,
                    end=end_unit.end,
                    states=resolved_states,
                    pov_character_id=pov_id,
                    story_order=story_order,
                    reviews=reviews,
                    source="ai",
                    meta={
                        "prompt_version": SCENE_PROMPT_VERSION,
                        "provider": self.registry.llm.name,
                        "model": self.registry.llm.model,
                        "is_mock": self.registry.llm.is_mock,
                        "window": {"index": window.index, "total": window.total},
                        "fallback": False,
                        "call": dict(result.meta or {}),
                        "evidence": evidence,
                    },
                )
            )
        return plans

    def _fallback_plan(
        self,
        window: _Window,
        chapter: _ChapterSource,
        chapter_time: dict[str, Any],
        infos: list[_CharacterInfo],
        sequences: dict[str, int],
        *,
        start_index: int,
        reason: str,
    ) -> _ScenePlan:
        text = window.text
        time_mode = str(chapter_time.get("time_mode") or "unknown")
        if time_mode not in _TIME_MODES:
            time_mode = "unknown"
        states: list[_ResolvedState] = []
        for info in infos:
            if info.name not in text and not any(alias in text for alias in info.aliases):
                continue
            arc = self._arc_for_chapter(info, chapter, chapter_time.get("story_order"), sequences)
            age_stage = (
                arc.age_stage if arc is not None and arc.age_stage != "unknown" else info.age_range
            )
            state = SceneCharacterStateDraft(
                name=info.name,
                presence="mentioned",
                age_stage=age_stage,
                voice_note=(
                    (arc.voice_hint if arc is not None else None)
                    or info.voice_description
                ),
                state_tags=["确定性回退"],
                confidence=0.2,
                evidence=[text[:80]],
            )
            state.physical_state = None
            state.emotional_state = None
            state.knowledge_state = None
            state.disguise_state = None
            state.voice_note = _clean_text(state.voice_note, 255)
            states.append(_ResolvedState(character=info, state=state))
        summary = _clean_text(text, 400)
        evidence = [
            {
                "chapter_id": chapter.id,
                "quote": _clean_text(text, 100) or "",
                "reason": reason or "分场回退",
            }
        ]
        return _ScenePlan(
            index=start_index,
            draft=SceneDraft(
                paragraph_start=window.units[0].index,
                paragraph_end=window.units[-1].index,
                title=_clean_text(window.units[0].text, 40) or f"场景 {window.index}",
                summary=summary,
                story_time_label=chapter_time.get("story_time_label"),
                time_mode=time_mode,
                characters=[item.state for item in states],
                confidence=0.2,
                evidence=[EvidenceDraft(quote=_clean_text(text, 300) or "")],
            ),
            start=window.start,
            end=window.end,
            states=states,
            pov_character_id=None,
            story_order=chapter_time.get("story_order"),
            reviews=[],
            source="system",
            fallback=True,
            meta={
                "prompt_version": SCENE_PROMPT_VERSION,
                "provider": self.registry.llm.name,
                "model": self.registry.llm.model,
                "is_mock": self.registry.llm.is_mock,
                "window": {"index": window.index, "total": window.total},
                "fallback": True,
                "fallback_reason": reason[:300],
                "evidence": evidence,
            },
        )

    # --------------------------------------------------------------- finalize

    def _finalize(
        self,
        job_id: str,
        chapter: _ChapterSource,
        version: int,
        bible_id: str,
        plans: list[_ScenePlan],
        chapter_reviews: list[_ReviewDraft],
        fallback_windows: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        started: str,
        started_perf: float,
    ) -> None:
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return
            chapter_row = session.get(Chapter, chapter.id)
            if chapter_row is None:
                raise NotFoundError("章节在分场过程中被删除")
            existing = session.scalar(
                select(Scene.id)
                .where(
                    Scene.chapter_id == chapter.id,
                    Scene.analysis_version == version,
                )
                .limit(1)
            )
            if existing is not None:
                raise ConflictError(
                    "当前分场版本已存在，请重新发起分析",
                    code="scene_version_conflict",
                )
            scene_rows: list[tuple[_ScenePlan, Scene]] = []
            for plan in plans:
                scene_id = new_id("scene")
                evidence = _evidence_dicts(plan.draft.evidence, chapter.id)
                if not evidence:
                    evidence = list((plan.meta or {}).get("evidence") or [])
                row = Scene(
                    id=scene_id,
                    book_id=job.book_id,
                    chapter_id=chapter.id,
                    analysis_version=version,
                    scene_index=plan.index,
                    title=_clean_text(plan.draft.title, 255),
                    summary=_clean_text(plan.draft.summary, 4000),
                    location=_clean_text(plan.draft.location, 255),
                    story_time_label=_clean_text(plan.draft.story_time_label, 120),
                    story_order=plan.story_order,
                    time_mode=plan.draft.time_mode,
                    pov_character_id=plan.pov_character_id,
                    mood=_clean_text(plan.draft.mood, 64),
                    source_start_offset=plan.start,
                    source_end_offset=plan.end,
                    analysis_status=plan.status,
                    confidence=_clamp(plan.draft.confidence),
                    evidence_json=evidence,
                    meta_json=dict(plan.meta or {}),
                    source=plan.source,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(row)
                for resolved in plan.states:
                    state = resolved.state
                    state_evidence = _evidence_dicts(state.evidence, chapter.id)
                    if not state_evidence:
                        # 状态自身未给证据时，退到场景级证据，保证每条状态可回溯原文。
                        fallback_evidence = evidence or [
                            {
                                "chapter_id": chapter.id,
                                "quote": _clean_text(text_for_plan(plan, chapter), 100)
                                or "",
                                "reason": "场景对应原文范围",
                            }
                        ]
                        state_evidence = [dict(fallback_evidence[0])]
                    session.add(
                        SceneCharacter(
                            id=new_id("schar"),
                            book_id=job.book_id,
                            scene_id=scene_id,
                            character_id=resolved.character.id,
                            state_phase=0,
                            presence=state.presence
                            if state.presence in _PRESENCE_VALUES
                            else "present",
                            age_stage=state.age_stage,
                            physical_state=_clean_text(state.physical_state, 255),
                            emotional_state=_clean_text(state.emotional_state, 255),
                            relationship_state=_clean_text(
                                state.relationship_state, 255
                            ),
                            knowledge_state=_clean_text(state.knowledge_state, 1000),
                            disguise_state=_clean_text(state.disguise_state, 255),
                            voice_note=_clean_text(state.voice_note, 255),
                            state_tags_json=list(state.state_tags or [])[:12],
                            confidence=_clamp(state.confidence),
                            evidence_json=state_evidence,
                            source=plan.source,
                            created_at=utc_now(),
                            updated_at=utc_now(),
                        )
                    )
                scene_rows.append((plan, row))
            session.flush()

            scene_ids = [
                row.id
                for row in session.scalars(
                    select(Scene).where(Scene.chapter_id == chapter.id)
                )
            ]
            stale_conditions = [
                and_(
                    ReviewItem.entity_type == "chapter",
                    ReviewItem.entity_id == chapter.id,
                )
            ]
            if scene_ids:
                stale_conditions.append(
                    and_(
                        ReviewItem.entity_type == "scene",
                        ReviewItem.entity_id.in_(scene_ids),
                    )
                )
            stale_items = list(
                session.scalars(
                    select(ReviewItem).where(
                        ReviewItem.book_id == job.book_id,
                        ReviewItem.status == "open",
                        ReviewItem.source == "ai",
                        ReviewItem.code.startswith("scene_"),
                        or_(*stale_conditions),
                    )
                )
            )
            for item in stale_items:
                item.status = "ignored"
                item.resolution_note = "重新生成分场后该疑点未再出现，已自动忽略。"
                item.resolved_at = utc_now()
                item.updated_at = utc_now()
            # autoflush 关闭：先落库忽略状态，避免下面的 upsert 命中旧行。
            session.flush()

            review_count = 0
            for review in chapter_reviews:
                self._review(
                    session,
                    book_id=job.book_id,
                    entity_type=review.entity_type,
                    entity_id=review.entity_id or chapter.id,
                    review=review,
                )
                review_count += 1
            for plan, scene_row in scene_rows:
                for review in plan.reviews:
                    self._review(
                        session,
                        book_id=job.book_id,
                        entity_type="scene",
                        entity_id=scene_row.id,
                        review=review,
                    )
                    review_count += 1

            needs_review = any(plan.status == "needs_review" for plan in plans) or any(
                review.severity in {"blocking", "warning"} for review in chapter_reviews
            )
            chapter_row.scene_version = version
            chapter_row.scene_status = "needs_review" if needs_review else "completed"
            chapter_row.updated_at = utc_now()
            job.status = "completed"
            job.completed_items = job.total_items
            job.progress = 1.0
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            job.result_json = {
                "chapter_id": chapter.id,
                "scene_version": version,
                "bible_id": bible_id,
                "scenes": len(plans),
                "character_states": sum(len(plan.states) for plan in plans),
                "fallback_windows": len(fallback_windows),
                "review_items": review_count,
                "warnings": [
                    review.message
                    for review in chapter_reviews
                    if review.message and review.severity in {"blocking", "warning"}
                ],
                "prompt_version": SCENE_PROMPT_VERSION,
                "model": self.registry.llm.model,
                "is_mock": self.registry.llm.is_mock,
                "calls": calls,
                "started_at": started,
                "duration_ms": int((time.perf_counter() - started_perf) * 1000),
            }

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

    # ------------------------------------------------------------- fail/restore

    def _restore_chapter_status(
        self, session: Session, chapter: Chapter, *, cancelled: bool = False
    ) -> None:
        if chapter.scene_version <= 0:
            chapter.scene_status = "not_started" if cancelled else "failed"
            chapter.updated_at = utc_now()
            return
        statuses = list(
            session.scalars(
                select(Scene.analysis_status).where(
                    Scene.chapter_id == chapter.id,
                    Scene.analysis_version == chapter.scene_version,
                )
            )
        )
        if not statuses:
            chapter.scene_status = "needs_review"
        elif any(status in {"needs_review", "failed"} for status in statuses):
            chapter.scene_status = "needs_review"
        else:
            chapter.scene_status = "completed"
        chapter.updated_at = utc_now()

    def _fail_scene_job(
        self, session: Session, job_id: str, error: Exception
    ) -> None:
        job = session.get(GenerationJob, job_id)
        if job is None:
            return
        chapter = session.get(Chapter, job.target_id or "")
        if chapter is None:
            return
        self._restore_chapter_status(session, chapter)
        code = str(getattr(error, "code", "scene_failed"))
        message = str(getattr(error, "message", error))[:1200]
        self._review(
            session,
            book_id=job.book_id,
            entity_type="chapter",
            entity_id=chapter.id,
            review=_ReviewDraft(
                entity_type="chapter",
                entity_id=chapter.id,
                code="scene_analysis_failed",
                severity="blocking",
                title="分场分析失败",
                message=message,
                payload={"error_code": code, "chapter_id": chapter.id},
            ),
        )


def text_for_plan(plan: _ScenePlan, chapter: _ChapterSource) -> str:
    """取场景覆盖范围内的原文摘要，用于回退状态的证据。"""
    start = max(0, plan.start - chapter.start)
    end = max(start, plan.end - chapter.start)
    return chapter.text[start:end]


def recover_interrupted_scenes(session: Session) -> dict[str, int]:
    """启动时把上次进程遗留的分场任务与章状态标记为失败。"""
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
        job.error_message = "应用在分场分析过程中退出，任务已中断；请重新发起分析。"
        job.finished_at = now
        job.updated_at = now
        if job.target_id:
            chapter_ids.add(job.target_id)
    chapters = list(
        session.scalars(
            select(Chapter).where(Chapter.scene_status == "running")
        )
    )
    chapter_ids.update(chapter.id for chapter in chapters)
    restored = 0
    for chapter_id in chapter_ids:
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            continue
        if chapter.scene_version > 0:
            statuses = list(
                session.scalars(
                    select(Scene.analysis_status).where(
                        Scene.chapter_id == chapter.id,
                        Scene.analysis_version == chapter.scene_version,
                    )
                )
            )
            chapter.scene_status = (
                "needs_review"
                if not statuses or any(
                    status in {"needs_review", "failed"} for status in statuses
                )
                else "completed"
            )
        else:
            chapter.scene_status = "failed"
        chapter.updated_at = now
        restored += 1
    return {"jobs": len(jobs), "chapters": restored}