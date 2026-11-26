"""v0.5.0 P2：全书角色圣经与剧情时间线。

处理策略（与设计文档 6.1/6.2 一致）：

1. 逐章/逐窗口提取人物观察，不把整本书放进一次 LLM 调用；
2. 先用确定性规则聚类姓名与别名，再把有歧义的候选交给 LLM 归并；
3. 人物弧按剧情时间而非章节顺序整理，倒叙/回忆状态不覆盖当前状态；
4. 低置信、别名冲突、时间线疑点写入异常收件箱（review_items）；
5. 用户锁定字段在重跑时保留；旧版本圣经与角色弧不删除。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.base import (
    BibleArcDraft,
    BibleChapterResult,
    BibleCharacterDraft,
    BibleMergeResult,
    BibleTimelineDraft,
    BibleTimelineResult,
    RelationshipDraft,
)
from novelvoice_studio.ai.prompts import (
    BIBLE_CHARACTER_PROMPT_VERSION,
    BIBLE_MERGE_PROMPT_VERSION,
    BIBLE_PROMPT_VERSION,
    BIBLE_TIMELINE_PROMPT_VERSION,
)
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
    CharacterRelationship,
    GenerationJob,
    ReviewItem,
)
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.bible import (
    BibleCharacterOut,
    BibleOut,
    BibleReviewItemOut,
    BibleStateResponse,
    BibleStatusResponse,
    BibleTimelineOut,
    CharacterArcOut,
    TimelineChapterOut,
)
from novelvoice_studio.services.analysis_serializers import (
    to_job_out,
    to_relationship_out,
)
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.character_ops import normalize_aliases
from novelvoice_studio.services.chapters import ParagraphSlice, split_paragraphs
from novelvoice_studio.services.paths import new_id, validate_id

logger = logging.getLogger(__name__)

JOB_TYPE = "book_bible"
ACTIVE_JOB_STATUSES = ("pending", "running")
MERGE_BATCH_SIZE = 20
MERGE_BATCH_CHARS = 5000
MERGE_CHAPTERS_PER_CHARACTER = 10
TIMELINE_MAX_CHAPTERS = 120
TIMELINE_MAX_CHARS = 9000
MERGE_NOTE_LIMIT = 20
ALIAS_REVIEW_MIN_CONFIDENCE = 0.3

_AGE_STAGE_LABELS = {
    "child": "幼年",
    "teenager": "少年",
    "young_adult": "青年",
    "adult": "成年",
    "middle_aged": "中年",
    "elderly": "老年",
    "unknown": "未分阶段",
}
_ROLE_RANK = {
    "protagonist": 4,
    "major": 3,
    "minor": 2,
    "extra": 1,
    "unknown": 0,
}
_ROLE_LABELS = {
    "protagonist": "主角",
    "major": "重要配角",
    "minor": "次要角色",
    "extra": "路人级角色",
    "unknown": "层级未知",
}
_TIME_MODES = {"present", "flashback", "flashforward", "dream", "unknown"}
# 模型偶尔输出英文关系类型；落库前归一到提示词约定的中文选项。
_RELATIONSHIP_TYPE_ALIASES = {
    "sibling": "亲属",
    "siblings": "亲属",
    "family": "亲属",
    "relative": "亲属",
    "parent": "亲属",
    "child": "亲属",
    "spouse": "亲属",
    "friend": "朋友",
    "friends": "朋友",
    "friendship": "朋友",
    "enemy": "敌对",
    "rival": "敌对",
    "mentor": "师徒",
    "apprentice": "师徒",
    "colleague": "同事",
    "coworker": "同事",
    "acquaintance": "其他具体关系",
}
# 只能出现在描述句、不能当别称使用的亲属/身份词。
_ALIAS_ROLE_WORDS = {
    "姐姐",
    "妹妹",
    "哥哥",
    "弟弟",
    "父亲",
    "母亲",
    "爸爸",
    "妈妈",
    "爹",
    "娘",
    "女儿",
    "儿子",
    "妻子",
    "丈夫",
    "老婆",
    "老公",
    "爷爷",
    "奶奶",
    "外公",
    "外婆",
    "叔叔",
    "阿姨",
    "舅舅",
    "姑姑",
    "店主",
    "老板",
    "木匠",
    "访客",
    "来人",
    "陌生人",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _clean_name(value: str, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _alias_is_name(value: str) -> bool:
    """别称必须是可称呼的名字或绰号，不能是“某某的姐姐”这类描述短语。"""
    cleaned = _clean_name(value)
    if not cleaned or len(cleaned) > 12:
        return False
    if "的" in cleaned:
        return False
    if cleaned in _ALIAS_ROLE_WORDS:
        return False
    # 三字以内可能是“沈老板”“孙二娘”这类称呼；更长且含亲属/身份词按描述句处理。
    return len(cleaned) <= 3 or not any(
        word in cleaned for word in _ALIAS_ROLE_WORDS
    )


def _normalize_relationship_type(value: str | None) -> str:
    cleaned = _clean_text(value, 64) or "unknown"
    return _RELATIONSHIP_TYPE_ALIASES.get(cleaned.casefold(), cleaned)


def _clean_text(value: str | None, limit: int = 1000) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:limit] if cleaned else None


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _evidence_dicts(
    items: Any, chapter_id: str | None, limit: int = 12
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items or []:
        if isinstance(item, dict):
            quote = str(item.get("quote") or "").strip()
            reason = str(item.get("reason") or "").strip() or None
        else:
            quote = str(getattr(item, "quote", "") or "").strip()
            reason = str(getattr(item, "reason", "") or "").strip() or None
        if not quote and not reason:
            continue
        key = (quote, reason or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "chapter_id": chapter_id,
                "quote": quote[:500],
                "reason": reason[:300] if reason else None,
            }
        )
        if len(result) >= limit:
            break
    return result


def _append_evidence(
    existing: Any, items: Any, chapter_id: str | None, limit: int = 30
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in existing or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not quote and not reason:
            continue
        key = (quote, reason)
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            {
                "chapter_id": item.get("chapter_id") or chapter_id,
                "quote": quote[:500],
                "reason": reason[:300] or None,
            }
        )
    for item in _evidence_dicts(items, chapter_id):
        key = (str(item["quote"]), str(item.get("reason") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-limit:]


def _stage_label(stage: str) -> str:
    return _AGE_STAGE_LABELS.get(stage, "未分阶段")


def _role_from_counts(chapter_count: int, total_chapters: int) -> str:
    if chapter_count <= 0:
        return "unknown"
    if chapter_count >= max(2, int(total_chapters * 0.6 + 0.999)):
        return "protagonist"
    if chapter_count >= max(2, int(total_chapters * 0.3 + 0.999)):
        return "major"
    if chapter_count >= 2:
        return "minor"
    return "extra"


def _overview_from_characters(
    characters: list[dict[str, Any]], total_chapters: int
) -> str:
    """用结构化角色表生成全书概览，避免让 4B 模型补写未经证实的情节。"""

    if not characters:
        return f"全书共 {total_chapters} 章，未识别到有明确叙事作用的角色。"
    ranked = sorted(
        characters,
        key=lambda item: (
            -_ROLE_RANK.get(str(item.get("role_tier") or "unknown"), 0),
            str(item.get("name") or ""),
        ),
    )
    leads = [
        item
        for item in ranked
        if item.get("role_tier") in {"protagonist", "major"}
    ][:6]
    selected = leads or ranked[:3]
    details: list[str] = []
    for item in selected:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        role = _ROLE_LABELS.get(
            str(item.get("role_tier") or "unknown"), "层级未知"
        )
        labels = [
            str(arc.get("label") or "").strip()
            for arc in (item.get("arcs") or [])
            if isinstance(arc, dict) and str(arc.get("label") or "").strip()
        ]
        labels = list(dict.fromkeys(labels))[:4]
        stage_text = f"；阶段：{'、'.join(labels)}" if labels else ""
        details.append(f"{name}（{role}{stage_text}）")
    if not details:
        return f"全书共 {total_chapters} 章，识别 {len(characters)} 个角色。"
    return _clean_text(
        f"全书共 {total_chapters} 章，识别 {len(characters)} 个角色。"
        f"主要角色：{'；'.join(details)}。"
        "本概览只依据结构化角色表生成，不补充原文没有的情节或亲属关系；"
        "关系、人物阶段与剧情时间线详见设定集。",
        600,
    ) or ""


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != key:
            next_key = self.parent[key]
            self.parent[key] = root
            key = next_key
        return root

    def union(self, left: str, right: str) -> str:
        first, second = self.find(left), self.find(right)
        if first == second:
            return first
        # 让字典序更小者作为根，保证列表在相同输入下稳定。
        if first <= second:
            self.parent[second] = first
            return first
        self.parent[first] = second
        return second


@dataclass(frozen=True)
class _Window:
    index: int
    total: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _ChapterSource:
    id: str
    title: str
    sequence: int
    start: int
    end: int
    text: str
    paragraphs: list[ParagraphSlice]
    summary: str | None


@dataclass
class _CharacterObservation:
    chapter_id: str
    chapter_sequence: int
    chapter_title: str
    draft: BibleCharacterDraft
    timeline: BibleTimelineDraft


@dataclass
class _ChapterTimeline:
    chapter_id: str
    sequence: int
    title: str
    summary: str | None
    story_time_label: str | None = None
    time_mode: str = "unknown"
    note: str | None = None
    confidence: float = 0.0


@dataclass
class _ChapterWork:
    """单章抽取的线程内产物；数据库写入统一回到主线程执行。"""

    source: _ChapterSource
    summary_meta: dict[str, Any]
    results: list[BibleChapterResult]
    extraction_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _RelationshipObservation:
    chapter_id: str
    chapter_sequence: int
    chapter_title: str
    draft: RelationshipDraft


@dataclass
class _Cluster:
    key: str
    names: Counter[str] = field(default_factory=Counter)
    aliases: dict[str, dict[str, Any]] = field(default_factory=dict)
    observations: list[_CharacterObservation] = field(default_factory=list)
    preferred_name: str | None = None
    role_tier: str = "unknown"
    overview: str | None = None
    merge_arcs: list[BibleArcDraft] = field(default_factory=list)
    character_id: str | None = None
    match_conflicts: list[str] = field(default_factory=list)

    @property
    def canonical_name(self) -> str:
        if self.preferred_name:
            return self.preferred_name
        if not self.names:
            return self.key
        return sorted(
            self.names.items(),
            key=lambda item: (-item[1], -len(item[0]), item[0]),
        )[0][0]

    @property
    def chapter_sequences(self) -> list[int]:
        return sorted({item.chapter_sequence for item in self.observations})

    @property
    def confidence(self) -> float:
        if not self.observations:
            return 0.0
        weights = sorted((_clamp(item.draft.confidence) for item in self.observations), reverse=True)
        top = weights[:3]
        return sum(top) / len(top)


class BibleService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        registry: ProviderRegistry,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry
        self.alias_threshold = settings.analysis.alias_confidence_threshold
        self.speaker_threshold = settings.analysis.speaker_confidence_threshold
        self.window_chars = settings.analysis.max_chapter_chars

    # --------------------------------------------------------------- helpers

    def _book(self, session: Session, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _active_job(self, session: Session, book_id: str) -> GenerationJob | None:
        return session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.book_id == book_id,
                GenerationJob.job_type == JOB_TYPE,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )

    def _chapters(self, session: Session, book_id: str) -> list[Chapter]:
        return list(
            session.scalars(
                select(Chapter)
                .where(Chapter.book_id == book_id)
                .order_by(Chapter.sequence.asc())
            )
        )

    def _read_chapter(
        self, session: Session, book: Book, chapter: Chapter, source: str
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
                f"章节「{chapter.title}」没有正文，无法生成角色圣经",
                code="chapter_empty",
            )
        paragraphs = split_paragraphs(source, start, end)
        if not paragraphs:
            raise ValidationError(
                f"章节「{chapter.title}」没有可用段落，无法生成角色圣经",
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

    def _windows(self, chapter: _ChapterSource) -> list[_Window]:
        limit = max(200, self.window_chars)
        if len(chapter.text) <= limit:
            return [_Window(1, 1, chapter.start, chapter.end, chapter.text)]
        spans: list[tuple[int, int]] = []
        current_start: int | None = None
        current_end: int | None = None
        for paragraph in chapter.paragraphs:
            if paragraph.end - paragraph.start > limit:
                if current_start is not None and current_end is not None:
                    spans.append((current_start, current_end))
                    current_start = current_end = None
                cursor = paragraph.start
                while cursor < paragraph.end:
                    end = min(paragraph.end, cursor + limit)
                    spans.append((cursor, end))
                    cursor = end
                continue
            if current_start is None:
                current_start, current_end = paragraph.start, paragraph.end
                continue
            if paragraph.end - current_start <= limit:
                current_end = paragraph.end
                continue
            spans.append((current_start, current_end))
            current_start, current_end = paragraph.start, paragraph.end
        if current_start is not None and current_end is not None:
            spans.append((current_start, current_end))
        if not spans:
            spans = [(chapter.start, chapter.end)]
        total = len(spans)
        return [
            _Window(
                index=index + 1,
                total=total,
                start=start,
                end=end,
                text=chapter.text[start - chapter.start : end - chapter.start],
            )
            for index, (start, end) in enumerate(spans)
        ]

    def _active_characters(self, session: Session, book_id: str) -> list[Character]:
        return list(
            session.scalars(
                select(Character)
                .where(Character.book_id == book_id, Character.merged_into_id.is_(None))
                .order_by(Character.created_at.asc())
            )
        )

    def _known_payload(
        self,
        characters: list[Character],
        extra_names: list[tuple[str, list[str]]] | None = None,
        text: str | None = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for character in characters:
            aliases = [item["name"] for item in normalize_aliases(character.aliases_json)]
            rows.setdefault(
                _norm(character.canonical_name),
                {
                    "name": character.canonical_name,
                    "aliases": aliases,
                    "gender": character.gender,
                    "age_range": character.age_range,
                    "character_type": character.character_type,
                    "identity": _clean_text(character.identity_description, 80),
                    "personality": [
                        str(item) for item in (character.personality_json or [])[:5]
                    ],
                    "voice": _clean_text(character.voice_description, 100),
                },
            )
        for name, aliases in extra_names or []:
            key = _norm(name)
            if not key:
                continue
            row = rows.setdefault(
                key,
                {
                    "name": name,
                    "aliases": [],
                    "gender": "unknown",
                    "age_range": "unknown",
                    "character_type": "unknown",
                    "identity": None,
                    "personality": [],
                    "voice": None,
                },
            )
            for alias in aliases:
                if alias and alias not in row["aliases"]:
                    row["aliases"].append(alias)
        selected = list(rows.values())
        if text:
            mentioned: list[dict[str, Any]] = []
            remaining: list[dict[str, Any]] = []
            folded = text.casefold()
            for row in selected:
                keys = [_norm(row["name"]), *(_norm(item) for item in row["aliases"])]
                if any(key and key in folded for key in keys):
                    mentioned.append(row)
                else:
                    remaining.append(row)
            selected = [*mentioned, *remaining]
        return selected[:limit]

    # ------------------------------------------------------------- summarize

    def _summarize(
        self, chapter: _ChapterSource, known: list[dict[str, Any]], windows: list[_Window]
    ) -> tuple[str, dict[str, Any]]:
        if chapter.summary:
            return chapter.summary, {"reused": True}
        summaries: list[str] = []
        key_events: list[str] = []
        characters: list[str] = []
        calls: list[dict[str, Any]] = []
        for window in windows:
            result = self.registry.llm.summarize_chapter(window.text, known)
            if result.summary.strip():
                summaries.append(result.summary.strip())
            for event in result.key_events:
                cleaned = _clean_text(event, 300)
                if cleaned and cleaned not in key_events:
                    key_events.append(cleaned)
            for name in result.characters:
                cleaned = _clean_name(name)
                if cleaned and cleaned not in characters:
                    characters.append(cleaned)
            calls.append(result.meta)
        summary = _clean_text(" ".join(summaries), 4000) or ""
        if not summary:
            raise ValidationError(
                f"章节「{chapter.title}」摘要为空，无法生成角色圣经",
                code="bible_summary_empty",
            )
        return summary, {
            "summary": summary,
            "key_events": key_events[:30],
            "characters": characters[:60],
            "meta": {"source": "bible", "calls": calls},
        }

    # ------------------------------------------------------------- extraction

    def _merge_draft(
        self, target: BibleCharacterDraft, source: BibleCharacterDraft
    ) -> BibleCharacterDraft:
        if _clamp(source.confidence) > _clamp(target.confidence):
            base, extra = source.model_copy(deep=True), target
        else:
            base, extra = target.model_copy(deep=True), source
        aliases = {
            _norm(item.name): item.model_copy(deep=True) for item in base.aliases
        }
        for item in extra.aliases:
            key = _norm(item.name)
            if not key or key == _norm(base.name):
                continue
            existing = aliases.get(key)
            if existing is None or _clamp(item.confidence) > _clamp(existing.confidence):
                aliases[key] = item.model_copy(deep=True)
        base.aliases = list(aliases.values())
        base.evidence = [
            *base.evidence,
            *[
                item
                for item in extra.evidence
                if _norm(item.quote) not in {_norm(existing.quote) for existing in base.evidence}
            ],
        ]
        for field_name in (
            "gender",
            "character_type",
            "role_tier",
            "identity_description",
            "personality_desc",
            "appearance_desc",
            "voice_description",
            "role_hint",
            "arc_note",
        ):
            if getattr(base, field_name) in {None, "", "unknown"}:
                setattr(base, field_name, getattr(extra, field_name))
        if base.age_range == "unknown" and extra.age_range != "unknown":
            base.age_range = extra.age_range
            base.age_basis = extra.age_basis
        tags = list(dict.fromkeys([*base.personality, *extra.personality]))
        base.personality = tags[:20]
        base.confidence = max(_clamp(base.confidence), _clamp(extra.confidence))
        base.is_provisional = base.is_provisional or extra.is_provisional
        return base

    def _merge_relationship_draft(
        self, target: RelationshipDraft, source: RelationshipDraft
    ) -> RelationshipDraft:
        if _clamp(source.confidence) > _clamp(target.confidence):
            base, extra = source.model_copy(deep=True), target
        else:
            base, extra = target.model_copy(deep=True), source
        if not base.description and extra.description:
            base.description = extra.description
        base.evidence = [
            *base.evidence,
            *[
                item
                for item in extra.evidence
                if _norm(item.quote)
                not in {_norm(existing.quote) for existing in base.evidence}
            ],
        ]
        base.confidence = max(_clamp(base.confidence), _clamp(extra.confidence))
        base.is_provisional = base.is_provisional or extra.is_provisional
        if base.relationship_type == "unknown" and extra.relationship_type != "unknown":
            base.relationship_type = extra.relationship_type
        return base

    def _merge_chapter_result(
        self, chapter: _ChapterSource, results: list[BibleChapterResult]
    ) -> tuple[
        list[_CharacterObservation],
        list[_RelationshipObservation],
        _ChapterTimeline,
    ]:
        drafts: dict[str, BibleCharacterDraft] = {}
        relationships: dict[tuple[str, str], RelationshipDraft] = {}
        for result in results:
            for draft in result.characters:
                name = _clean_name(draft.name)
                if not name:
                    continue
                draft.name = name
                key = _norm(name)
                draft.aliases = [
                    item
                    for item in draft.aliases
                    if _alias_is_name(item.name) and _norm(item.name) != key
                ]
                existing = drafts.get(key)
                drafts[key] = (
                    draft.model_copy(deep=True)
                    if existing is None
                    else self._merge_draft(existing, draft)
                )
            for relationship in result.relationships:
                key = (_norm(relationship.source_name), _norm(relationship.target_name))
                if not key[0] or not key[1] or key[0] == key[1]:
                    continue
                existing = relationships.get(key)
                relationships[key] = (
                    relationship.model_copy(deep=True)
                    if existing is None
                    else self._merge_relationship_draft(existing, relationship)
                )
        timeline = BibleTimelineDraft()
        for result in results:
            candidate = result.timeline
            if _clamp(candidate.confidence) >= _clamp(timeline.confidence):
                timeline = candidate
        observations = [
            _CharacterObservation(
                chapter_id=chapter.id,
                chapter_sequence=chapter.sequence,
                chapter_title=chapter.title,
                draft=draft,
                timeline=timeline,
            )
            for draft in drafts.values()
        ]
        relationship_observations = [
            _RelationshipObservation(
                chapter_id=chapter.id,
                chapter_sequence=chapter.sequence,
                chapter_title=chapter.title,
                draft=relationship,
            )
            for relationship in relationships.values()
        ]
        chapter_timeline = _ChapterTimeline(
            chapter_id=chapter.id,
            sequence=chapter.sequence,
            title=chapter.title,
            summary=_clean_text(chapter.summary, 400),
            story_time_label=timeline.story_time_label,
            time_mode=timeline.time_mode if timeline.time_mode in _TIME_MODES else "unknown",
            note=_clean_text(timeline.note, 300),
            confidence=_clamp(timeline.confidence),
        )
        return observations, relationship_observations, chapter_timeline

    # --------------------------------------------------------------- clusters

    def _sanitize_observations(
        self, observations: list[_CharacterObservation]
    ) -> None:
        """去掉描述性别称，并阻止把另一个角色的正式名当作别名合并。"""
        canonical = {
            _norm(item.draft.name)
            for item in observations
            if _norm(item.draft.name)
        }
        for observation in observations:
            own_key = _norm(observation.draft.name)
            observation.draft.aliases = [
                item
                for item in observation.draft.aliases
                if _alias_is_name(item.name)
                and _norm(item.name) != own_key
                and _norm(item.name) not in canonical
            ]

    def _build_clusters(
        self, observations: list[_CharacterObservation]
    ) -> tuple[list[_Cluster], dict[str, list[str]], list[str]]:
        uf = _UnionFind()
        names: dict[str, Counter[str]] = defaultdict(Counter)
        alias_targets: dict[str, set[str]] = defaultdict(set)
        alias_best: dict[str, Any] = {}
        alias_display: dict[str, str] = {}
        for observation in observations:
            draft = observation.draft
            name_key = _norm(draft.name)
            if not name_key:
                continue
            names[name_key][draft.name] += 1
            for item in draft.aliases:
                alias_key = _norm(item.name)
                if not alias_key or alias_key == name_key:
                    continue
                alias_targets[alias_key].add(name_key)
                alias_display.setdefault(alias_key, _clean_name(item.name))
                previous = alias_best.get(alias_key)
                if previous is None or _clamp(item.confidence) > _clamp(previous.confidence):
                    alias_best[alias_key] = item.model_copy(deep=True)

        conflicts: dict[str, list[str]] = {}
        warnings: list[str] = []
        for alias_key, owners in alias_targets.items():
            if len(owners) <= 1:
                continue
            conflicts[alias_key] = sorted(owners)
            warnings.append(
                f"称谓「{alias_display.get(alias_key, alias_key)}」在章节中指向多个角色，已保留为待确认别名。"
            )

        for observation in observations:
            draft = observation.draft
            name_key = _norm(draft.name)
            if not name_key:
                continue
            uf.find(name_key)
            for item in draft.aliases:
                alias_key = _norm(item.name)
                if not alias_key or alias_key == name_key or alias_key in conflicts:
                    continue
                if item.basis == "explicit" or _clamp(item.confidence) >= self.alias_threshold:
                    uf.union(name_key, alias_key)

        grouped: dict[str, _Cluster] = {}
        for observation in observations:
            name_key = _norm(observation.draft.name)
            if not name_key:
                continue
            root = uf.find(name_key)
            cluster = grouped.setdefault(root, _Cluster(key=root))
            cluster.observations.append(observation)
            cluster.names[observation.draft.name] += 1
            for item in observation.draft.aliases:
                alias_key = _norm(item.name)
                if not alias_key or alias_key == name_key or alias_key in conflicts:
                    continue
                owner = uf.find(name_key)
                if owner != root:
                    continue
                best = alias_best.get(alias_key)
                if best is None:
                    continue
                existing = cluster.aliases.get(alias_key)
                if existing is None or _clamp(best.confidence) > _clamp(existing.get("confidence")):
                    cluster.aliases[alias_key] = {
                        "name": _clean_name(best.name),
                        "confidence": _clamp(best.confidence),
                        "basis": best.basis,
                        "chapter_id": observation.chapter_id,
                        "evidence": _evidence_dicts(best.evidence, observation.chapter_id, 4),
                        "source": "ai",
                    }
        # 已并入聚类的别名键不再以独立角色出现在 names 中。
        for cluster in grouped.values():
            for alias_key, item in list(cluster.aliases.items()):
                cluster.names.pop(alias_key, None)
                if _norm(cluster.canonical_name) == alias_key:
                    cluster.names.pop(alias_key, None)
        clusters = list(grouped.values())
        clusters.sort(key=lambda item: (min(item.chapter_sequences or [0]), item.canonical_name))
        conflict_display = {
            key: [_display_for_key(names, owner) for owner in owners]
            for key, owners in conflicts.items()
        }
        return clusters, conflict_display, warnings

    def _candidate_payload(
        self, cluster: _Cluster, total_chapters: int
    ) -> dict[str, Any]:
        by_chapter: dict[int, dict[str, Any]] = {}
        for observation in cluster.observations:
            current = by_chapter.get(observation.chapter_sequence)
            draft = observation.draft
            row = {
                "sequence": observation.chapter_sequence,
                "age_stage": draft.age_range,
                "role_hint": _clean_text(draft.role_hint, 80),
                "arc_note": _clean_text(draft.arc_note, 100),
                "time_mode": observation.timeline.time_mode,
                "story_time_label": observation.timeline.story_time_label,
            }
            if current is None or _clamp(draft.confidence) > _clamp(current.get("_confidence")):
                row["_confidence"] = _clamp(draft.confidence)
                by_chapter[observation.chapter_sequence] = row
        chapters = sorted(by_chapter.values(), key=lambda item: int(item["sequence"]))
        for row in chapters:
            row.pop("_confidence", None)
        mention_chapters = len(chapters)
        if len(chapters) > MERGE_CHAPTERS_PER_CHARACTER:
            count = MERGE_CHAPTERS_PER_CHARACTER
            step = (len(chapters) - 1) / max(1, count - 1)
            kept = [chapters[int(round(index * step))] for index in range(count)]
            chapters = list({id(row): row for row in kept}.values())
        return {
            "name": cluster.canonical_name,
            "aliases": [item["name"] for item in cluster.aliases.values()],
            "mention_chapters": mention_chapters,
            "total_chapters": total_chapters,
            "chapters": chapters,
            "identity": next(
                (
                    _clean_text(item.draft.identity_description, 120)
                    for item in cluster.observations
                    if item.draft.identity_description
                ),
                None,
            ),
            "personality": list(
                dict.fromkeys(
                    tag
                    for item in cluster.observations
                    for tag in item.draft.personality
                    if tag
                )
            )[:8],
            "confidence": cluster.confidence,
        }

    def _merge_llm_results(
        self,
        clusters: list[_Cluster],
        results: list[BibleMergeResult],
    ) -> tuple[list[_Cluster], list[str]]:
        warnings: list[str] = []
        index: dict[str, _Cluster] = {}
        for cluster in clusters:
            index[_norm(cluster.canonical_name)] = cluster
            for alias_key in cluster.aliases:
                index.setdefault(alias_key, cluster)
        removed: set[int] = set()
        for result in results:
            for item in result.characters:
                target = index.get(_norm(item.name))
                if target is None:
                    warnings.append(
                        f"归并结果中的角色「{item.name}」不在候选表中，已忽略。"
                    )
                    continue
                for merge_name in item.merge_names:
                    key = _norm(merge_name)
                    source = index.get(key)
                    if source is None or source is target:
                        continue
                    target.observations.extend(source.observations)
                    target.names.update(source.names)
                    for alias_key, alias in source.aliases.items():
                        existing = target.aliases.get(alias_key)
                        if existing is None or _clamp(alias.get("confidence")) > _clamp(
                            existing.get("confidence")
                        ):
                            target.aliases[alias_key] = alias
                    target.role_tier = (
                        item.role_tier
                        if _ROLE_RANK.get(item.role_tier, 0)
                        > _ROLE_RANK.get(target.role_tier, 0)
                        else target.role_tier
                    )
                    target.overview = target.overview or _clean_text(item.overview, 800)
                    if item.arcs:
                        target.merge_arcs = [arc.model_copy(deep=True) for arc in item.arcs]
                    target.preferred_name = _clean_name(item.name) or target.preferred_name
                    removed.add(id(source))
                    for alias_key in source.aliases:
                        index[alias_key] = target
                    for source_name in source.names:
                        index[_norm(source_name)] = target
                    index[_norm(source.canonical_name)] = target
                if _ROLE_RANK.get(item.role_tier, 0) > _ROLE_RANK.get(target.role_tier, 0):
                    target.role_tier = item.role_tier
                target.overview = target.overview or _clean_text(item.overview, 800)
                if item.arcs:
                    target.merge_arcs = [arc.model_copy(deep=True) for arc in item.arcs]
            if result.notes:
                for note in result.notes:
                    cleaned = _clean_text(note, 300)
                    if cleaned and cleaned not in warnings:
                        warnings.append(cleaned)
        clusters = [cluster for cluster in clusters if id(cluster) not in removed]
        return clusters, warnings

    def _match_existing_characters(
        self,
        session: Session,
        book_id: str,
        clusters: list[_Cluster],
    ) -> tuple[list[_Cluster], dict[str, list[str]], list[str]]:
        characters = self._active_characters(session, book_id)
        lookup: dict[str, list[Character]] = defaultdict(list)
        for character in characters:
            lookup[_norm(character.canonical_name)].append(character)
            for alias in normalize_aliases(character.aliases_json):
                lookup[_norm(alias["name"])].append(character)
        warnings: list[str] = []
        conflicts: dict[str, list[str]] = {}
        by_character: dict[str, _Cluster] = {}
        remaining: list[_Cluster] = []
        for cluster in clusters:
            keys = {_norm(cluster.canonical_name), *cluster.aliases.keys(), *(
                _norm(name) for name in cluster.names
            )}
            matches: dict[str, Character] = {}
            for key in keys:
                for character in lookup.get(key, []):
                    matches[character.id] = character
            if not matches:
                remaining.append(cluster)
                continue
            selected = next(
                (
                    character
                    for character in matches.values()
                    if _norm(character.canonical_name) == _norm(cluster.canonical_name)
                ),
                sorted(matches.values(), key=lambda item: item.created_at)[0],
            )
            if len(matches) > 1:
                cluster.match_conflicts = sorted(matches)
                conflicts[cluster.canonical_name] = [
                    item.canonical_name for item in matches.values()
                ]
                warnings.append(
                    f"候选角色「{cluster.canonical_name}」可能对应多个已有角色："
                    + "、".join(item.canonical_name for item in matches.values())
                )
            target = by_character.get(selected.id)
            if target is None:
                by_character[selected.id] = cluster
                cluster.character_id = selected.id
                remaining.append(cluster)
            else:
                target.observations.extend(cluster.observations)
                target.names.update(cluster.names)
                for alias_key, alias in cluster.aliases.items():
                    existing = target.aliases.get(alias_key)
                    if existing is None or _clamp(alias.get("confidence")) > _clamp(
                        existing.get("confidence")
                    ):
                        target.aliases[alias_key] = alias
                target.merge_arcs.extend(cluster.merge_arcs)
                target.overview = target.overview or cluster.overview
                if _ROLE_RANK.get(cluster.role_tier, 0) > _ROLE_RANK.get(target.role_tier, 0):
                    target.role_tier = cluster.role_tier
        remaining.sort(key=lambda item: (min(item.chapter_sequences or [0]), item.canonical_name))
        return remaining, conflicts, warnings

    # ----------------------------------------------------------------- arcs

    def _observations_in_range(
        self, cluster: _Cluster, start: int | None, end: int | None
    ) -> list[_CharacterObservation]:
        if start is None and end is None:
            return list(cluster.observations)
        low = start if start is not None else min(
            (item.chapter_sequence for item in cluster.observations), default=0
        )
        high = end if end is not None else max(
            (item.chapter_sequence for item in cluster.observations), default=0
        )
        selected = [
            item
            for item in cluster.observations
            if low <= item.chapter_sequence <= high
        ]
        return selected or list(cluster.observations)

    def _arc_payload(
        self,
        cluster: _Cluster,
        arc: BibleArcDraft | None,
        observations: list[_CharacterObservation],
        chapter_ids: dict[int, str],
        order_map: dict[int, int],
    ) -> dict[str, Any]:
        sequences = sorted({item.chapter_sequence for item in observations})
        start = arc.chapter_start if arc is not None else (sequences[0] if sequences else None)
        end = arc.chapter_end if arc is not None else (sequences[-1] if sequences else None)
        stage = arc.age_stage if arc is not None and arc.age_stage != "unknown" else "unknown"
        if stage == "unknown":
            stages = [item.draft.age_range for item in observations if item.draft.age_range != "unknown"]
            if stages:
                stage = Counter(stages).most_common(1)[0][0]
        labels = [
            item.timeline.story_time_label
            for item in observations
            if item.timeline.story_time_label
        ]
        story_start = arc.story_time_start if arc is not None else None
        story_end = arc.story_time_end if arc is not None else None
        if story_start is None and labels:
            story_start = labels[0]
        if story_end is None and labels:
            story_end = labels[-1]
        orders = [order_map[seq] for seq in sequences if seq in order_map]
        notes = [
            _clean_text(item.draft.arc_note, 160)
            for item in observations
            if item.draft.arc_note
        ]
        summary = _clean_text(arc.summary, 1000) if arc is not None else None
        if not summary:
            summary = _clean_text(" ".join(dict.fromkeys(notes)), 1000)
        if not summary:
            summary = _clean_text(
                " ".join(
                    dict.fromkeys(
                        item.draft.identity_description or item.draft.role_hint or ""
                        for item in observations
                    )
                ),
                1000,
            )
        tags = list(
            dict.fromkeys(
                tag
                for item in observations
                for tag in item.draft.personality
                if tag
            )
        )[:12]
        appearance = next(
            (
                _clean_text(item.draft.appearance_desc, 800)
                for item in observations
                if item.draft.appearance_desc
            ),
            None,
        )
        voice = arc.voice_hint if arc is not None else None
        if not voice:
            voice = next(
                (
                    _clean_text(item.draft.voice_description, 800)
                    for item in observations
                    if item.draft.voice_description
                ),
                None,
            )
        evidence: list[dict[str, Any]] = []
        for item in observations:
            evidence = _append_evidence(
                evidence,
                item.draft.evidence,
                item.chapter_id,
                limit=20,
            )
        confidence = _clamp(arc.confidence) if arc is not None else 0.0
        if confidence <= 0 and observations:
            confidence = sum(_clamp(item.draft.confidence) for item in observations) / len(observations)
        label = _clean_name(arc.label, 120) if arc is not None else ""
        if not label:
            label = _stage_label(stage)
        chapters = sorted({item.chapter_id for item in observations})
        return {
            "label": label,
            "age_stage": stage,
            "chapter_start_id": chapter_ids.get(start) if start else None,
            "chapter_end_id": chapter_ids.get(end) if end else None,
            "story_time_start": _clean_text(story_start, 120),
            "story_time_end": _clean_text(story_end, 120),
            "story_order_start": min(orders) if orders else None,
            "story_order_end": max(orders) if orders else None,
            "summary": summary,
            "traits_json": tags,
            "appearance_json": {"description": appearance} if appearance else {},
            "voice_traits_json": {
                **({"description": voice} if voice else {}),
                "age_stage": stage,
            },
            "confidence": confidence,
            "evidence_json": evidence,
            "source": "ai",
            "chapter_ids": chapters,
            "sequences": sequences,
        }

    def _build_arcs(
        self,
        cluster: _Cluster,
        chapter_ids: dict[int, str],
        order_map: dict[int, int],
    ) -> list[dict[str, Any]]:
        if cluster.merge_arcs:
            arcs: list[dict[str, Any]] = []
            for arc in cluster.merge_arcs:
                observations = self._observations_in_range(
                    cluster, arc.chapter_start, arc.chapter_end
                )
                if not observations:
                    continue
                arcs.append(
                    self._arc_payload(
                        cluster, arc, observations, chapter_ids, order_map
                    )
                )
            if arcs:
                arcs.sort(
                    key=lambda item: (
                        item["story_order_start"] or 10**9,
                        item["sequences"][0] if item["sequences"] else 10**9,
                    )
                )
                return arcs
        grouped: dict[str, list[_CharacterObservation]] = defaultdict(list)
        for observation in cluster.observations:
            stage = observation.draft.age_range
            if stage not in _AGE_STAGE_LABELS:
                stage = "unknown"
            grouped[stage].append(observation)
        arcs = [
            self._arc_payload(cluster, None, rows, chapter_ids, order_map)
            for rows in grouped.values()
        ]
        arcs.sort(
            key=lambda item: (
                item["story_order_start"] or 10**9,
                item["sequences"][0] if item["sequences"] else 10**9,
            )
        )
        return arcs

    # ------------------------------------------------------------ persistence

    def _review(
        self,
        session: Session,
        *,
        book_id: str,
        entity_type: str,
        entity_id: str | None,
        code: str,
        severity: str,
        title: str,
        message: str | None,
        payload_json: dict[str, Any],
    ) -> ReviewItem:
        existing = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.entity_type == entity_type,
                ReviewItem.entity_id == entity_id,
                ReviewItem.code == code,
                ReviewItem.status == "open",
            )
        )
        if existing is None:
            item = ReviewItem(
                id=new_id("review"),
                book_id=book_id,
                entity_type=entity_type,
                entity_id=entity_id,
                code=code,
                severity=severity,
                title=title[:255],
                message=message,
                payload_json=payload_json,
                status="open",
                source="ai",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(item)
            session.flush()
            return item
        existing.severity = severity
        existing.title = title[:255]
        existing.message = message
        existing.payload_json = payload_json
        existing.updated_at = utc_now()
        return existing

    def _profile_updates(self, cluster: _Cluster, total_chapters: int) -> dict[str, Any]:
        observations = sorted(
            cluster.observations, key=lambda item: (-_clamp(item.draft.confidence), item.chapter_sequence)
        )
        updates: dict[str, Any] = {}
        gender_votes: Counter[str] = Counter()
        type_votes: Counter[str] = Counter()
        for observation in observations:
            draft = observation.draft
            if draft.gender != "unknown":
                weight = 2 if draft.gender_basis == "explicit" else 1
                gender_votes[draft.gender] += weight
            if draft.character_type != "unknown":
                type_votes[draft.character_type] += 1
        if gender_votes:
            updates["gender"] = gender_votes.most_common(1)[0][0]
            explicit = any(
                item.draft.gender == updates["gender"] and item.draft.gender_basis == "explicit"
                for item in observations
            )
            updates["gender_basis"] = "explicit" if explicit else "inferred"
        if type_votes:
            updates["character_type"] = type_votes.most_common(1)[0][0]
        for field_name in (
            "identity_description",
            "personality_desc",
            "appearance_desc",
            "voice_description",
        ):
            value = next(
                (
                    _clean_text(getattr(item.draft, field_name), 1000)
                    for item in observations
                    if getattr(item.draft, field_name)
                ),
                None,
            )
            if value:
                updates[field_name] = value
        tags = list(
            dict.fromkeys(
                tag
                for item in observations
                for tag in item.draft.personality
                if tag
            )
        )[:20]
        if tags:
            updates["personality_json"] = tags
        age_votes = Counter(
            item.draft.age_range
            for item in observations
            if item.draft.age_range != "unknown"
        )
        if age_votes:
            updates["age_range"] = age_votes.most_common(1)[0][0]
            updates["age_basis"] = "inferred"
        updates["role_tier"] = (
            cluster.role_tier
            if cluster.role_tier != "unknown"
            else _role_from_counts(len(cluster.chapter_sequences), total_chapters)
        )
        updates["confidence"] = cluster.confidence
        updates["is_provisional"] = cluster.confidence < self.speaker_threshold
        updates["first_chapter_id"] = cluster.observations[0].chapter_id if cluster.observations else None
        return updates

    def _persist_characters(
        self,
        session: Session,
        book: Book,
        clusters: list[_Cluster],
        total_chapters: int,
    ) -> tuple[dict[str, Character], list[dict[str, Any]], list[str]]:
        characters = self._active_characters(session, book.id)
        by_id = {item.id: item for item in characters}
        by_name: dict[str, Character] = {}
        for character in characters:
            by_name.setdefault(_norm(character.canonical_name), character)
        warnings: list[str] = []
        created_or_updated: list[dict[str, Any]] = []
        for cluster in clusters:
            character = by_id.get(cluster.character_id or "")
            if character is None:
                for key in {_norm(cluster.canonical_name), *cluster.names, *cluster.aliases}:
                    character = by_name.get(key)
                    if character is not None:
                        break
            if character is None:
                name = _clean_name(cluster.canonical_name)
                character = Character(
                    id=new_id("char"),
                    book_id=book.id,
                    canonical_name=name,
                    aliases_json=[],
                    alias_candidates_json=[],
                    gender="unknown",
                    gender_basis="unknown",
                    age_range="unknown",
                    age_basis="unknown",
                    character_type="unknown",
                    personality_json=[],
                    evidence_json=[],
                    confidence=0.0,
                    role_tier="unknown",
                    source="ai",
                    is_provisional=True,
                    user_locked=False,
                    merge_log_json=[],
                    first_chapter_id=cluster.observations[0].chapter_id if cluster.observations else None,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(character)
                by_id[character.id] = character
                by_name[_norm(character.canonical_name)] = character
            cluster.character_id = character.id
            if character.merged_into_id:
                target = by_id.get(character.merged_into_id)
                if target is not None:
                    character = target
                    cluster.character_id = target.id

            if not character.user_locked:
                updates = self._profile_updates(cluster, total_chapters)
                for field_name, value in updates.items():
                    if field_name in {"role_tier", "gender", "character_type", "age_range"}:
                        if getattr(character, field_name) == "unknown":
                            setattr(character, field_name, value)
                        continue
                    if field_name in {"gender_basis", "age_basis"}:
                        if getattr(character, field_name) == "unknown":
                            setattr(character, field_name, value)
                        continue
                    if field_name in {"confidence", "is_provisional", "first_chapter_id"}:
                        continue
                    if field_name == "personality_json":
                        merged = list(
                            dict.fromkeys([*(character.personality_json or []), *value])
                        )
                        character.personality_json = merged[:20]
                        continue
                    if not getattr(character, field_name):
                        setattr(character, field_name, value)
                role = updates.get("role_tier")
                if role and _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(
                    character.role_tier, 0
                ):
                    character.role_tier = role
                character.confidence = max(
                    _clamp(character.confidence), _clamp(updates.get("confidence"))
                )
                if not updates.get("is_provisional", True):
                    character.is_provisional = False
                if not character.first_chapter_id:
                    character.first_chapter_id = updates.get("first_chapter_id")
                character.evidence_json = _append_evidence(
                    character.evidence_json,
                    [
                        evidence
                        for observation in cluster.observations
                        for evidence in observation.draft.evidence
                    ],
                    cluster.observations[0].chapter_id if cluster.observations else None,
                    limit=40,
                )
                existing_aliases = normalize_aliases(character.aliases_json)
                seen_aliases = {_norm(item["name"]) for item in existing_aliases}
                seen_aliases.add(_norm(character.canonical_name))
                candidates = normalize_aliases(character.alias_candidates_json)
                candidate_by_key = {_norm(item["name"]): item for item in candidates}
                for alias_key, alias in cluster.aliases.items():
                    name = _clean_name(alias.get("name"))
                    if (
                        not name
                        or not _alias_is_name(name)
                        or alias_key == _norm(character.canonical_name)
                    ):
                        continue
                    confidence = _clamp(alias.get("confidence"))
                    if alias_key in seen_aliases:
                        continue
                    if confidence >= self.alias_threshold or alias.get("basis") == "explicit":
                        existing_aliases.append(
                            {
                                "name": name,
                                "confidence": confidence,
                                "basis": alias.get("basis") or "inferred",
                                "chapter_id": alias.get("chapter_id"),
                                "evidence": alias.get("evidence") or [],
                                "source": "ai",
                            }
                        )
                        seen_aliases.add(alias_key)
                        candidate_by_key.pop(alias_key, None)
                        continue
                    if confidence < ALIAS_REVIEW_MIN_CONFIDENCE:
                        continue
                    candidate = candidate_by_key.get(alias_key) or {
                        "name": name,
                        "confidence": confidence,
                        "basis": alias.get("basis") or "unknown",
                        "chapter_id": alias.get("chapter_id"),
                        "evidence": alias.get("evidence") or [],
                        "source": "ai",
                    }
                    candidate["confidence"] = max(
                        _clamp(candidate.get("confidence")), confidence
                    )
                    candidate["evidence"] = _append_evidence(
                        candidate.get("evidence"),
                        alias.get("evidence"),
                        alias.get("chapter_id"),
                        limit=8,
                    )
                    candidate["reason"] = "置信度不足，需人工确认后加入别名"
                    candidate_by_key[alias_key] = candidate
                character.aliases_json = existing_aliases
                character.alias_candidates_json = list(candidate_by_key.values())
                character.updated_at = utc_now()
            created_or_updated.append(
                {
                    "character_id": character.id,
                    "name": character.canonical_name,
                    "role_tier": character.role_tier,
                    "age_range": character.age_range,
                    "aliases": [
                        item["name"] for item in normalize_aliases(character.aliases_json)
                    ],
                    "confidence": character.confidence,
                }
            )
        session.flush()
        return by_id, created_or_updated, warnings

    def _persist_arcs(
        self,
        session: Session,
        book: Book,
        bible: BookBible,
        clusters: list[_Cluster],
        chapter_ids: dict[int, str],
        order_map: dict[int, int],
    ) -> tuple[int, dict[str, list[dict[str, Any]]]]:
        total = 0
        by_character: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for cluster in clusters:
            if not cluster.character_id:
                continue
            arcs = self._build_arcs(cluster, chapter_ids, order_map)
            for index, payload in enumerate(arcs):
                arc = CharacterArc(
                    id=new_id("arc"),
                    book_id=book.id,
                    bible_id=bible.id,
                    character_id=cluster.character_id,
                    arc_index=index,
                    label=payload["label"][:120],
                    age_stage=payload["age_stage"],
                    chapter_start_id=payload["chapter_start_id"],
                    chapter_end_id=payload["chapter_end_id"],
                    story_time_start=payload["story_time_start"],
                    story_time_end=payload["story_time_end"],
                    story_order_start=payload["story_order_start"],
                    story_order_end=payload["story_order_end"],
                    summary=payload["summary"],
                    traits_json=payload["traits_json"],
                    appearance_json=payload["appearance_json"],
                    voice_traits_json=payload["voice_traits_json"],
                    confidence=payload["confidence"],
                    evidence_json=payload["evidence_json"],
                    source="ai",
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(arc)
                by_character[cluster.character_id].append(
                    {**payload, "id": arc.id, "arc_index": index}
                )
                total += 1
        session.flush()
        return total, by_character

    def _persist_relationships(
        self,
        session: Session,
        book: Book,
        clusters: list[_Cluster],
        relationships: list[_RelationshipObservation],
    ) -> tuple[int, list[dict[str, Any]]]:
        key_to_character: dict[str, str] = {}
        for cluster in clusters:
            if not cluster.character_id:
                continue
            key_to_character[_norm(cluster.canonical_name)] = cluster.character_id
            for name in cluster.names:
                key_to_character.setdefault(_norm(name), cluster.character_id)
            for alias_key in cluster.aliases:
                key_to_character.setdefault(alias_key, cluster.character_id)
        existing = {
            (item.source_character_id, item.target_character_id): item
            for item in session.scalars(
                select(CharacterRelationship).where(
                    CharacterRelationship.book_id == book.id
                )
            )
        }
        added = 0
        summaries: list[dict[str, Any]] = []
        for observation in relationships:
            draft = observation.draft
            source_id = key_to_character.get(_norm(draft.source_name))
            target_id = key_to_character.get(_norm(draft.target_name))
            if not source_id or not target_id or source_id == target_id:
                continue
            relationship_type = _normalize_relationship_type(draft.relationship_type)
            evidence = _evidence_dicts(draft.evidence, observation.chapter_id)
            pair = (source_id, target_id)
            reverse = (target_id, source_id)
            row = existing.get(pair)
            reversed_match = False
            if row is None:
                candidate = existing.get(reverse)
                if candidate is not None and (
                    relationship_type == "unknown"
                    or candidate.relationship_type == "unknown"
                    or candidate.relationship_type == relationship_type
                ):
                    row = candidate
                    reversed_match = True
            confidence = _clamp(draft.confidence)
            if row is not None:
                row.evidence_json = _append_evidence(
                    row.evidence_json, evidence, observation.chapter_id
                )
                previous_confidence = _clamp(row.confidence)
                row.confidence = max(previous_confidence, confidence)
                if not row.description or (
                    not reversed_match and confidence >= previous_confidence
                ):
                    row.description = _clean_text(draft.description, 1000)
                if row.relationship_type == "unknown" and relationship_type != "unknown":
                    row.relationship_type = relationship_type
                if not draft.is_provisional:
                    row.is_provisional = False
                row.updated_at = utc_now()
                continue
            row = CharacterRelationship(
                id=new_id("rel"),
                book_id=book.id,
                source_character_id=source_id,
                target_character_id=target_id,
                relationship_type=relationship_type,
                description=_clean_text(draft.description, 1000),
                evidence_json=evidence,
                confidence=confidence,
                is_provisional=draft.is_provisional
                or confidence < self.speaker_threshold,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(row)
            existing[pair] = row
            added += 1
            summaries.append(
                {
                    "source_character_id": source_id,
                    "target_character_id": target_id,
                    "relationship_type": relationship_type,
                    "confidence": confidence,
                }
            )
        session.flush()
        return added, summaries

    # -------------------------------------------------------------- timeline

    def _timeline_view(
        self,
        chapter_timelines: list[_ChapterTimeline],
        observations: list[_CharacterObservation],
        timeline: dict[str, Any],
    ) -> list[dict[str, Any]]:
        names_by_chapter: dict[str, list[str]] = defaultdict(list)
        for observation in observations:
            if observation.draft.name not in names_by_chapter[observation.chapter_id]:
                names_by_chapter[observation.chapter_id].append(observation.draft.name)
        chapters: list[dict[str, Any]] = []
        for item in chapter_timelines:
            row = {
                "chapter_id": item.chapter_id,
                "sequence": item.sequence,
                "title": item.title,
                "summary": item.summary,
                "story_time_label": item.story_time_label,
                "time_mode": item.time_mode,
                "confidence": item.confidence,
                "characters": names_by_chapter.get(item.chapter_id, []),
            }
            override = next(
                (
                    entry
                    for entry in timeline.get("chapters") or []
                    if isinstance(entry, dict) and int(entry.get("sequence") or 0) == item.sequence
                ),
                None,
            )
            if override is not None:
                if override.get("story_time_label"):
                    row["story_time_label"] = override["story_time_label"]
                if override.get("time_mode") in _TIME_MODES:
                    row["time_mode"] = override["time_mode"]
                row["story_order"] = override.get("story_order")
            chapters.append(row)
        return chapters

    def _serialize_character_observation(
        self, observation: _CharacterObservation
    ) -> dict[str, Any]:
        return {
            "chapter_id": observation.chapter_id,
            "sequence": observation.chapter_sequence,
            "title": observation.chapter_title,
            "character": observation.draft.model_dump(),
            "timeline": observation.timeline.model_dump(),
        }

    def _serialize_relationship_observation(
        self, observation: _RelationshipObservation
    ) -> dict[str, Any]:
        return {
            "chapter_id": observation.chapter_id,
            "sequence": observation.chapter_sequence,
            "title": observation.chapter_title,
            "relationship": observation.draft.model_dump(),
        }

    def _serialize_timeline_row(self, timeline: _ChapterTimeline) -> dict[str, Any]:
        return {
            "chapter_id": timeline.chapter_id,
            "sequence": timeline.sequence,
            "title": timeline.title,
            "summary": timeline.summary,
            "story_time_label": timeline.story_time_label,
            "time_mode": timeline.time_mode,
            "note": timeline.note,
            "confidence": timeline.confidence,
        }

    def _resume_bible(
        self, session: Session, book_id: str, chapters: list[Chapter]
    ) -> tuple[BookBible, set[str]] | None:
        """找最近一个“有部分章节已完成”的版本，用于断点续跑。"""
        known = {chapter.id for chapter in chapters}
        rows = list(
            session.scalars(
                select(BookBible)
                .where(BookBible.book_id == book_id)
                .order_by(BookBible.version.desc())
            )
        )
        best: tuple[int, BookBible, set[str]] | None = None
        for bible in rows:
            content = bible.content_json or {}
            covered = {
                str(row.get("chapter_id"))
                for row in (content.get("chapter_timelines") or [])
                if isinstance(row, dict)
            } & known
            if not covered or len(covered) >= len(chapters):
                continue
            # 取覆盖章节最多的那个版本，避免“刚取消的小任务”把更完整的进度顶掉。
            if best is None or len(covered) > best[0]:
                best = (len(covered), bible, covered)
        if best is None:
            return None
        return best[1], best[2]

    def _load_previous_state(
        self,
        session: Session,
        book: Book,
        chapters: list[Chapter],
        reuse_ids: list[str],
        base_id: str | None = None,
    ) -> tuple[
        list[_CharacterObservation],
        list[_RelationshipObservation],
        list[_ChapterTimeline],
        str | None,
    ]:
        if not reuse_ids:
            return [], [], [], None
        base = (
            session.get(BookBible, base_id)
            if base_id
            else self._active_bible(session, book.id)
        )
        if base is None:
            raise ValidationError(
                "选定章节重跑需要已有角色圣经结果",
                code="bible_base_missing",
            )
        reuse = set(reuse_ids)
        chapter_by_id = {chapter.id: chapter for chapter in chapters}
        content = base.content_json or {}
        observations: list[_CharacterObservation] = []
        for row in content.get("chapter_observations") or []:
            if not isinstance(row, dict) or str(row.get("chapter_id")) not in reuse:
                continue
            chapter = chapter_by_id.get(str(row.get("chapter_id")))
            if chapter is None:
                continue
            try:
                draft = BibleCharacterDraft.model_validate(row.get("character") or {})
                timeline = BibleTimelineDraft.model_validate(row.get("timeline") or {})
            except Exception:  # noqa: BLE001
                continue
            draft.aliases = [
                item for item in draft.aliases if _alias_is_name(item.name)
            ]
            observations.append(
                _CharacterObservation(
                    chapter_id=chapter.id,
                    chapter_sequence=chapter.sequence,
                    chapter_title=chapter.title,
                    draft=draft,
                    timeline=timeline,
                )
            )
        relationships: list[_RelationshipObservation] = []
        for row in content.get("relationship_observations") or []:
            if not isinstance(row, dict) or str(row.get("chapter_id")) not in reuse:
                continue
            chapter = chapter_by_id.get(str(row.get("chapter_id")))
            if chapter is None:
                continue
            try:
                draft = RelationshipDraft.model_validate(row.get("relationship") or {})
            except Exception:  # noqa: BLE001
                continue
            relationships.append(
                _RelationshipObservation(
                    chapter_id=chapter.id,
                    chapter_sequence=chapter.sequence,
                    chapter_title=chapter.title,
                    draft=draft,
                )
            )
        timelines: list[_ChapterTimeline] = []
        for row in content.get("chapter_timelines") or []:
            if not isinstance(row, dict) or str(row.get("chapter_id")) not in reuse:
                continue
            chapter = chapter_by_id.get(str(row.get("chapter_id")))
            if chapter is None:
                continue
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            timelines.append(
                _ChapterTimeline(
                    chapter_id=chapter.id,
                    sequence=chapter.sequence,
                    title=chapter.title,
                    summary=_clean_text(row.get("summary") or chapter.summary, 400),
                    story_time_label=row.get("story_time_label"),
                    time_mode=(
                        str(row.get("time_mode"))
                        if row.get("time_mode") in _TIME_MODES
                        else "unknown"
                    ),
                    note=row.get("note"),
                    confidence=_clamp(confidence),
                )
            )
        return observations, relationships, timelines, base.id

    # ------------------------------------------------------------------ run

    def start_book_bible(
        self, book_id: str, chapter_ids: list[str] | None = None
    ) -> AnalysisJobOut:
        requested = list(dict.fromkeys(chapter_ids or []))
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            active = self._active_job(session, book.id)
            if active is not None:
                raise ConflictError(
                    "本书已有正在生成的角色圣经任务",
                    code="bible_already_running",
                    details={"job_id": active.id, "status": active.status},
                )
            chapters = self._chapters(session, book.id)
            if not chapters:
                raise ValidationError("书籍没有章节，无法生成角色圣经", code="book_has_no_chapters")
            known_ids = {chapter.id for chapter in chapters}
            unknown = [item for item in requested if item not in known_ids]
            if unknown:
                raise ValidationError(
                    "指定的章节不属于本书",
                    code="bible_chapter_not_found",
                    details={"chapter_ids": unknown[:10]},
                )
            target_ids = set(requested) if requested else known_ids
            targets = [chapter for chapter in chapters if chapter.id in target_ids]
            reuse_ids = [chapter.id for chapter in chapters if chapter.id not in target_ids]
            resume_mode = False
            base_bible_id: str | None = None
            progress_base = 0
            if not requested:
                # 没有显式选章时，优先从未完成的旧版本接着跑，而不是从第 1 章重来。
                resume = self._resume_bible(session, book.id, chapters)
                if resume is not None:
                    base, covered = resume
                    missing = [chapter for chapter in chapters if chapter.id not in covered]
                    if missing:
                        resume_mode = True
                        base_bible_id = base.id
                        target_ids = {chapter.id for chapter in missing}
                        targets = missing
                        reuse_ids = [
                            chapter.id for chapter in chapters if chapter.id not in target_ids
                        ]
                        progress_base = len(covered)
            if reuse_ids:
                base = (
                    session.get(BookBible, base_bible_id)
                    if base_bible_id
                    else self._active_bible(session, book.id)
                )
                stored = {
                    str(row.get("chapter_id"))
                    for row in ((base.content_json or {}).get("chapter_timelines") or [])
                    if isinstance(row, dict)
                } if base is not None else set()
                if not set(reuse_ids).issubset(stored):
                    raise ValidationError(
                        "选定章节重跑需要已有完整的角色圣经结果；请先执行一次全书生成",
                        code="bible_base_missing",
                        details={"missing_chapters": sorted(set(reuse_ids) - stored)[:10]},
                    )
            version = int(
                session.scalar(
                    select(func.max(BookBible.version)).where(
                        BookBible.book_id == book.id
                    )
                )
                or 0
            ) + 1
            bible = BookBible(
                id=new_id("bible"),
                book_id=book.id,
                version=version,
                status="running",
                source="ai",
                prompt_version=BIBLE_PROMPT_VERSION,
                model=self.registry.llm.model,
                summary=None,
                content_json={
                    "schema_version": 1,
                    "status": "running",
                    "prompt_versions": {
                        "characters": BIBLE_CHARACTER_PROMPT_VERSION,
                        "merge": BIBLE_MERGE_PROMPT_VERSION,
                        "timeline": BIBLE_TIMELINE_PROMPT_VERSION,
                    },
                    "chapter_observations": [],
                    "relationship_observations": [],
                    "chapter_timelines": [],
                    "warnings": [],
                },
                timeline_json={},
                confidence=0.0,
                is_active=False,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(bible)
            session.flush()
            progress_total = len(chapters) if resume_mode else len(targets)
            progress_base = min(progress_base, progress_total)
            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=bible.id,
                target_type="book",
                stage="bible",
                job_type=JOB_TYPE,
                status="pending",
                total_items=progress_total,
                completed_items=progress_base,
                failed_items=0,
                progress=(
                    progress_base / progress_total if progress_total else 0.0
                ),
                attempt_count=0,
                max_attempts=1,
                params_json={
                    "bible_id": bible.id,
                    "version": version,
                    "chapter_ids": [chapter.id for chapter in targets],
                    "reused_chapter_ids": reuse_ids,
                    "resume_mode": resume_mode,
                    "base_bible_id": base_bible_id,
                    "progress_base": progress_base,
                    "progress_total": progress_total,
                },
                result_json={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            session.flush()
            book.bible_status = "running"
            book.updated_at = utc_now()
            output = to_job_out(job)

        thread = threading.Thread(
            target=self._run_job,
            args=(output.id,),
            name=f"novelvoice-bible-{output.id}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, output.id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = "thread_start_failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                self._fail_bible(session, output.id, exc)
            raise AppError(
                "无法启动角色圣经后台线程",
                status_code=500,
                code="bible_thread_failed",
            ) from exc
        return output

    def _fail_bible(self, session: Session, job_id: str, error: Exception) -> None:
        job = session.get(GenerationJob, job_id)
        if job is None:
            return
        bible = session.get(BookBible, job.target_id or "")
        if bible is not None and bible.status == "running":
            bible.status = "failed"
            content = dict(bible.content_json or {})
            content["status"] = "failed"
            content["error"] = {
                "code": str(getattr(error, "code", "bible_failed")),
                "message": str(getattr(error, "message", error))[:1200],
                "at": _iso_now(),
            }
            bible.content_json = content
            bible.updated_at = utc_now()
        book = session.get(Book, job.book_id)
        if book is not None:
            self._restore_book_status(session, book)

    def _restore_book_status(self, session: Session, book: Book) -> None:
        active = session.scalar(
            select(BookBible)
            .where(BookBible.book_id == book.id, BookBible.is_active.is_(True))
            .order_by(BookBible.version.desc())
            .limit(1)
        )
        if active is not None and active.status in {"approved", "needs_review"}:
            book.bible_status = active.status
        else:
            book.bible_status = "failed"

    def _prepare_chapter(
        self,
        *,
        book_id: str,
        book_title: str,
        source: Any,
        chapter_id: str,
        chapter_title: str,
        known_extra: list[tuple[str, list[str]]],
        previous_summary: str | None,
    ) -> _ChapterWork:
        """在线程里完成单章的摘要与抽取，只读数据库，不写库。"""
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            chapter_row = session.get(Chapter, chapter_id)
            if chapter_row is None:
                raise NotFoundError(f"章节「{chapter_title}」在生成过程中被删除")
            chapter_source = self._read_chapter(session, book, chapter_row, source)
            characters = self._active_characters(session, book.id)
            known = self._known_payload(
                characters, known_extra, chapter_source.text
            )
        windows = self._windows(chapter_source)
        summary, summary_meta = self._summarize(chapter_source, known, windows)
        chapter_source = _ChapterSource(
            id=chapter_source.id,
            title=chapter_source.title,
            sequence=chapter_source.sequence,
            start=chapter_source.start,
            end=chapter_source.end,
            text=chapter_source.text,
            paragraphs=chapter_source.paragraphs,
            summary=summary,
        )
        results: list[BibleChapterResult] = []
        extraction_calls: list[dict[str, Any]] = []
        for window in windows:
            result = self.registry.llm.extract_bible_characters(
                window.text,
                known,
                {
                    "book_title": book_title,
                    "chapter_title": chapter_source.title,
                    "chapter_sequence": chapter_source.sequence,
                    "chapter_summary": chapter_source.summary,
                    "previous_summary": previous_summary,
                    "window": {"index": window.index, "total": window.total},
                },
            )
            results.append(result)
            extraction_calls.append(
                {
                    "chapter_id": chapter_source.id,
                    "window": window.index,
                    "call": result.meta,
                }
            )
        return _ChapterWork(
            source=chapter_source,
            summary_meta=summary_meta,
            results=results,
            extraction_calls=extraction_calls,
        )

    def _run_job(self, job_id: str) -> None:
        started = _iso_now()
        try:
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = utc_now()
                job.attempt_count = 1
                job.updated_at = utc_now()
                bible = session.get(BookBible, job.target_id or "")
                if bible is None:
                    raise AppError("角色圣经版本不存在", status_code=500)
                book = self._book(session, job.book_id)
                book_id = book.id
                book_title = book.title
                chapters = self._chapters(session, book.id)
                params = dict(job.params_json or {})
                target_ids = {
                    str(item) for item in (params.get("chapter_ids") or []) if item
                }
                reuse_ids = [
                    str(item) for item in (params.get("reused_chapter_ids") or []) if item
                ]
                targets = (
                    [chapter for chapter in chapters if chapter.id in target_ids]
                    if target_ids
                    else list(chapters)
                )
                if not targets:
                    raise ValidationError(
                        "没有需要重跑的章节", code="bible_no_target_chapters"
                    )
                source = BookService(self.settings, session).read_source(book)
                (
                    previous_observations,
                    previous_relationships,
                    previous_timelines,
                    base_bible_id,
                ) = self._load_previous_state(
                    session,
                    book,
                    chapters,
                    reuse_ids,
                    params.get("base_bible_id"),
                )
                if previous_observations or previous_relationships or previous_timelines:
                    content = dict(bible.content_json or {})
                    content["chapter_observations"] = [
                        self._serialize_character_observation(item)
                        for item in previous_observations
                    ]
                    content["relationship_observations"] = [
                        self._serialize_relationship_observation(item)
                        for item in previous_relationships
                    ]
                    content["chapter_timelines"] = [
                        self._serialize_timeline_row(item)
                        for item in previous_timelines
                    ]
                    content["base_bible_id"] = base_bible_id
                    content["reused_chapters"] = len(
                        {item.chapter_id for item in previous_timelines}
                    )
                    bible.content_json = content
                    bible.updated_at = utc_now()
            total = len(targets)
            progress_base = int(params.get("progress_base") or 0)
            progress_total = int(params.get("progress_total") or total)
            failed_chapters: list[dict[str, Any]] = []
            observations_all: list[_CharacterObservation] = list(previous_observations)
            relationships_all: list[_RelationshipObservation] = list(
                previous_relationships
            )
            chapter_timelines: list[_ChapterTimeline] = list(previous_timelines)
            summary_calls: list[dict[str, Any]] = []
            extraction_calls: list[dict[str, Any]] = []
            summary_by_sequence = {
                chapter.sequence: chapter.summary for chapter in chapters
            }
            known_extra: list[tuple[str, list[str]]] = []
            reused_chapters = len(
                {item.chapter_id for item in previous_observations}
                | {item.chapter_id for item in previous_timelines}
            )

            workers = max(1, int(self.settings.jobs.bible_parallel_chapters))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for batch_start in range(0, total, workers):
                    with session_scope(self.session_factory) as session:
                        job = session.get(GenerationJob, job_id)
                        if job is None or job.status == "cancelled":
                            return
                    batch = targets[batch_start : batch_start + workers]
                    futures = [
                        pool.submit(
                            self._prepare_chapter,
                            book_id=book_id,
                            book_title=book_title,
                            source=source,
                            chapter_id=chapter.id,
                            chapter_title=chapter.title,
                            known_extra=list(known_extra),
                            previous_summary=summary_by_sequence.get(
                                chapter.sequence - 1
                            ),
                        )
                        for chapter in batch
                    ]
                    works: list[_ChapterWork | None] = []
                    for chapter, future in zip(batch, futures):
                        try:
                            works.append(future.result())
                        except Exception as exc:  # noqa: BLE001
                            # 单章失败不拖垮整本书：记录后跳过，后续可断点续跑补齐。
                            logger.warning(
                                "章节 %s「%s」抽取失败，已跳过：%s",
                                chapter.sequence,
                                chapter.title,
                                exc,
                            )
                            failed_chapters.append(
                                {
                                    "chapter_id": chapter.id,
                                    "sequence": chapter.sequence,
                                    "title": chapter.title,
                                    "error_code": str(
                                        getattr(exc, "code", "bible_chapter_failed")
                                    ),
                                    "error": str(getattr(exc, "message", exc))[:500],
                                }
                            )
                            works.append(None)
                    for offset, work in enumerate(works):
                        index = batch_start + offset
                        if work is None:
                            completed = progress_base + index + 1
                            with session_scope(self.session_factory) as session:
                                job = session.get(GenerationJob, job_id)
                                if job is None or job.status == "cancelled":
                                    return
                                job.failed_items = int(job.failed_items or 0) + 1
                                job.completed_items = min(
                                    completed, progress_total
                                )
                                job.progress = min(
                                    1.0, completed / max(1, progress_total)
                                )
                                job.updated_at = utc_now()
                                job.result_json = {
                                    **dict(job.result_json or {}),
                                    "failed_chapters": failed_chapters[-20:],
                                }
                            continue
                        chapter_source = work.source
                        if not work.summary_meta.get("reused"):
                            with session_scope(self.session_factory) as session:
                                row = session.get(Chapter, chapter_source.id)
                                if row is not None and not row.summary:
                                    row.summary = chapter_source.summary
                                    row.summary_json = work.summary_meta
                                    row.updated_at = utc_now()
                            summary_calls.append(
                                {
                                    "chapter_id": chapter_source.id,
                                    "call": work.summary_meta,
                                }
                            )
                        summary_by_sequence[chapter_source.sequence] = (
                            chapter_source.summary or ""
                        )
                        extraction_calls.extend(work.extraction_calls)
                        (
                            chapter_observations,
                            chapter_relationships,
                            chapter_timeline,
                        ) = self._merge_chapter_result(
                            chapter_source, work.results
                        )
                        observations_all.extend(chapter_observations)
                        relationships_all.extend(chapter_relationships)
                        chapter_timelines.append(chapter_timeline)
                        for observation in chapter_observations:
                            aliases = [
                                alias.name
                                for alias in observation.draft.aliases
                                if alias.name
                            ]
                            if aliases:
                                known_extra.append(
                                    (observation.draft.name, aliases)
                                )
                        with session_scope(self.session_factory) as session:
                            job = session.get(GenerationJob, job_id)
                            bible = (
                                session.get(BookBible, job.target_id or "")
                                if job
                                else None
                            )
                            if job is None or job.status == "cancelled":
                                return
                            if bible is not None:
                                content = dict(bible.content_json or {})
                                rows = list(content.get("chapter_observations") or [])
                                for observation in chapter_observations:
                                    rows.append(
                                        self._serialize_character_observation(
                                            observation
                                        )
                                    )
                                content["chapter_observations"] = rows
                                relationship_rows = list(
                                    content.get("relationship_observations") or []
                                )
                                for observation in chapter_relationships:
                                    relationship_rows.append(
                                        self._serialize_relationship_observation(
                                            observation
                                        )
                                    )
                                content["relationship_observations"] = (
                                    relationship_rows
                                )
                                timeline_rows = list(
                                    content.get("chapter_timelines") or []
                                )
                                timeline_rows.append(
                                    self._serialize_timeline_row(chapter_timeline)
                                )
                                content["chapter_timelines"] = timeline_rows
                                content["progress"] = {
                                    "chapter_id": chapter_source.id,
                                    "sequence": chapter_source.sequence,
                                    "completed": min(
                                        progress_base + index + 1,
                                        progress_total,
                                    ),
                                    "total": progress_total,
                                    "reused_chapters": reused_chapters,
                                }
                                bible.content_json = content
                                bible.updated_at = utc_now()
                            completed = progress_base + index + 1
                            job.completed_items = min(
                                completed, progress_total
                            )
                            job.progress = min(
                                1.0, completed / max(1, progress_total)
                            )
                            job.updated_at = utc_now()
                            job.result_json = {
                                **dict(job.result_json or {}),
                                "last_chapter": {
                                    "id": chapter_source.id,
                                    "sequence": chapter_source.sequence,
                                    "characters": len(chapter_observations),
                                },
                            }

            self._finalize(
                job_id,
                observations_all,
                relationships_all,
                chapter_timelines,
                chapters,
                summary_calls,
                extraction_calls,
                started,
                reused_chapters=reused_chapters,
                base_bible_id=base_bible_id,
                failed_chapters=failed_chapters,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("角色圣经任务 %s 异常终止", job_id)
            try:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None:
                        return
                    if job.status == "cancelled":
                        self._fail_bible(session, job_id, exc)
                        return
                    job.status = "failed"
                    job.error_code = str(getattr(exc, "code", "bible_failed"))
                    job.error_message = str(getattr(exc, "message", exc))[:2000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                    self._fail_bible(session, job_id, exc)
            except Exception:  # noqa: BLE001
                logger.exception("写入角色圣经失败状态时出错")

    # ---------------------------------------------------------------- final

    def _finalize(
        self,
        job_id: str,
        observations: list[_CharacterObservation],
        relationships: list[_RelationshipObservation],
        chapter_timelines: list[_ChapterTimeline],
        chapters: list[Chapter],
        summary_calls: list[dict[str, Any]],
        extraction_calls: list[dict[str, Any]],
        started: str,
        *,
        reused_chapters: int = 0,
        base_bible_id: str | None = None,
        failed_chapters: list[dict[str, Any]] | None = None,
    ) -> None:
        failed = list(failed_chapters or [])
        chapter_timelines = list(
            {
                item.chapter_id: item
                for item in sorted(chapter_timelines, key=lambda row: row.sequence)
            }.values()
        )
        observations = sorted(
            observations,
            key=lambda item: (item.chapter_sequence, _norm(item.draft.name)),
        )
        self._sanitize_observations(observations)
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return
            book = self._book(session, job.book_id)
            bible = session.get(BookBible, job.target_id or "")
            if bible is None:
                raise AppError("角色圣经版本不存在", status_code=500)
            total_chapters = len(chapters)
            book_title = book.title

        clusters, alias_conflicts, cluster_warnings = self._build_clusters(observations)
        warnings = list(cluster_warnings)
        covered_count = len({item.chapter_id for item in chapter_timelines})
        if covered_count < total_chapters:
            warnings.append(
                f"本版本只覆盖 {covered_count}/{total_chapters} 章；"
                "后续可继续生成，只补剩余章节。"
            )
        if failed:
            sequences = "、".join(str(item.get("sequence")) for item in failed[:20])
            warnings.append(
                f"有 {len(failed)} 章抽取失败（第 {sequences} 章）已跳过；"
                "再次点击生成会从断点继续，只补跑这些章节。"
            )
        merge_calls: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(clusters):
            payloads: list[dict[str, Any]] = []
            chars = 0
            while cursor + len(payloads) < len(clusters) and len(
                payloads
            ) < MERGE_BATCH_SIZE:
                payload = self._candidate_payload(
                    clusters[cursor + len(payloads)], total_chapters
                )
                cost = len(json.dumps(payload, ensure_ascii=False, default=str))
                if payloads and chars + cost > MERGE_BATCH_CHARS:
                    break
                payloads.append(payload)
                chars += cost
            try:
                result = self.registry.llm.merge_bible_candidates(
                    payloads,
                    {"book_title": book_title, "total_chapters": total_chapters},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("角色圣经归并批次失败：%s", exc)
                warnings.append(
                    "角色层级与人物弧的 AI 归并失败，已保留确定性合并结果；"
                    f"原因：{str(getattr(exc, 'message', exc))[:300]}"
                )
                cursor += len(payloads)
                continue
            merge_calls.append(result.meta)
            merged_clusters, merge_warnings = self._merge_llm_results(
                clusters, [result]
            )
            warnings.extend(merge_warnings)
            removed = len(clusters) - len(merged_clusters)
            clusters = merged_clusters
            cursor += max(1, len(payloads) - removed)

        clusters.sort(key=lambda item: (min(item.chapter_sequences or [0]), item.canonical_name))
        with session_scope(self.session_factory) as session:
            clusters, match_conflicts, match_warnings = self._match_existing_characters(
                session, book.id, clusters
            )
        warnings.extend(match_warnings)
        total = len(chapters)
        for cluster in clusters:
            if cluster.role_tier == "unknown":
                cluster.role_tier = _role_from_counts(len(cluster.chapter_sequences), total)

        timeline_input = [
            {
                "sequence": item.sequence,
                "title": item.title,
                "summary": _clean_text(item.summary, 120),
                "story_time_label": item.story_time_label,
                "time_mode": item.time_mode,
            }
            for item in chapter_timelines
        ]
        timeline_result: BibleTimelineResult | None = None
        timeline_ok = (
            len(timeline_input) <= TIMELINE_MAX_CHAPTERS
            and len(str(timeline_input)) <= TIMELINE_MAX_CHARS
        )
        if timeline_ok and timeline_input:
            try:
                timeline_result = self.registry.llm.synthesize_bible_timeline(
                    timeline_input,
                    {"book_title": book_title},
                )
                warnings.extend(
                    _clean_text(note, 300)
                    for note in timeline_result.notes
                    if _clean_text(note, 300)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("角色圣经时间线综合失败：%s", exc)
                warnings.append(
                    "剧情时间线综合失败，已按章节顺序回退；"
                    f"原因：{str(getattr(exc, 'message', exc))[:300]}"
                )
        elif timeline_input:
            warnings.append(
                "章节数量或摘要长度超过时间线单次综合上限，已按章节顺序回退。"
            )

        timeline_chapters = self._timeline_view(
            chapter_timelines,
            observations,
            {"chapters": [
                item.model_dump() for item in (timeline_result.chapters if timeline_result else [])
            ]},
        )
        provided_orders = {
            int(item["sequence"]): int(item["story_order"])
            for item in timeline_chapters
            if item.get("story_order")
        }
        next_order = max(provided_orders.values(), default=0)
        for item in timeline_chapters:
            sequence = int(item["sequence"])
            if sequence in provided_orders:
                item["story_order"] = provided_orders[sequence]
            else:
                next_order += 1
                item["story_order"] = next_order
        # 统一为 1..N，避免模型给出重复/跳号；相同序号时以章节顺序稳定排序。
        ordered = sorted(
            timeline_chapters,
            key=lambda item: (int(item["story_order"]), int(item["sequence"])),
        )
        for index, item in enumerate(ordered, start=1):
            item["story_order"] = index
        timeline_chapters = ordered
        order_map = {
            int(item["sequence"]): int(item["story_order"])
            for item in timeline_chapters
        }
        narrative_modes = {
            item.get("time_mode") for item in timeline_chapters if item.get("time_mode")
        }
        narrative_mode = (
            "nonlinear"
            if any(mode in {"flashback", "flashforward", "dream"} for mode in narrative_modes)
            else (timeline_result.narrative_mode if timeline_result else "linear")
        )
        if narrative_mode == "unknown":
            narrative_mode = "linear"
        timeline_json = {
            "narrative_mode": narrative_mode,
            "chapters": timeline_chapters,
            "notes": list(
                dict.fromkeys(
                    [
                        *(timeline_result.notes if timeline_result else []),
                    ]
                )
            )[:30],
            "source": "ai",
        }

        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return
            book = self._book(session, job.book_id)
            bible = session.get(BookBible, job.target_id or "")
            if bible is None:
                raise AppError("角色圣经版本不存在", status_code=500)
            chapter_rows = self._chapters(session, book.id)
            chapter_ids = {item.sequence: item.id for item in chapter_rows}
            total_chapters = len(chapter_rows)
            by_id, character_summaries, character_warnings = self._persist_characters(
                session,
                book,
                clusters,
                total_chapters,
            )
            warnings.extend(character_warnings)
            arc_total, arcs_by_character = self._persist_arcs(
                session, book, bible, clusters, chapter_ids, order_map
            )
            relationship_total, relationship_summaries = self._persist_relationships(
                session, book, clusters, relationships
            )
            # 角色列表补充最终角色信息。
            for entry in character_summaries:
                character = by_id.get(entry["character_id"])
                if character is not None:
                    entry["name"] = character.canonical_name
                    entry["role_tier"] = character.role_tier
                    entry["age_range"] = character.age_range
                    entry["aliases"] = [
                        item["name"]
                        for item in normalize_aliases(character.aliases_json)
                    ]
                    entry["arcs"] = arcs_by_character.get(character.id, [])
                    entry["overview"] = _clean_text(character.identity_description, 500)
            merge_summary = _overview_from_characters(
                character_summaries, total_chapters
            )

            # 异常收件箱。
            review_count = 0
            review_run_start = utc_now()
            character_by_id = by_id
            for alias, owners in alias_conflicts.items():
                owner_keys = {_norm(owner) for owner in owners}
                affected: list[Character] = []
                for cluster in clusters:
                    if not cluster.character_id:
                        continue
                    keys = {_norm(name) for name in cluster.names} | set(cluster.aliases)
                    if not (keys & owner_keys):
                        continue
                    character = character_by_id.get(cluster.character_id)
                    if character is not None and all(
                        item.id != character.id for item in affected
                    ):
                        affected.append(character)
                for character in affected:
                    self._review(
                        session,
                        book_id=book.id,
                        entity_type="character",
                        entity_id=character.id,
                        code=f"alias_conflict:{alias}"[:64],
                        severity="warning",
                        title=f"称谓「{alias}」指向多个角色",
                        message=(
                            "该称谓在不同章节被归到不同角色。系统未自动合并，"
                            "请在异常收件箱确认归属。"
                        ),
                        payload_json={
                            "alias": alias,
                            "owners": owners,
                            "character_ids": [item.id for item in affected],
                        },
                    )
                    review_count += 1
            for name, names in match_conflicts.items():
                character = next(
                    (
                        item
                        for item in character_by_id.values()
                        if _norm(item.canonical_name) == _norm(name)
                    ),
                    None,
                )
                if character is None:
                    continue
                self._review(
                    session,
                    book_id=book.id,
                    entity_type="character",
                    entity_id=character.id,
                    code="suspected_same_person",
                    severity="warning",
                    title=f"角色「{name}」可能对应多个已有角色",
                    message="请确认是否需要合并；系统已暂用其中一个角色。",
                    payload_json={"candidates": names},
                )
                review_count += 1
            for cluster in clusters:
                if not cluster.character_id:
                    continue
                character = character_by_id.get(cluster.character_id)
                if character is None:
                    continue
                if (
                    cluster.confidence < self.speaker_threshold
                    and character.role_tier in {"protagonist", "major"}
                ):
                    self._review(
                        session,
                        book_id=book.id,
                        entity_type="character",
                        entity_id=character.id,
                        code="low_confidence_character",
                        severity="info",
                        title=f"角色「{character.canonical_name}」置信度偏低",
                        message="AI 结果已按默认策略接受，可在异常收件箱抽查。",
                        payload_json={"confidence": cluster.confidence},
                    )
                    review_count += 1
            for index, note in enumerate(warnings[:MERGE_NOTE_LIMIT]):
                self._review(
                    session,
                    book_id=book.id,
                    entity_type="bible",
                    entity_id=bible.id,
                    code=f"bible_note:{index:02d}",
                    severity="info",
                    title="角色圣经疑点",
                    message=note,
                    payload_json={"note": note},
                )
                review_count += 1
            if timeline_result is None:
                self._review(
                    session,
                    book_id=book.id,
                    entity_type="bible",
                    entity_id=bible.id,
                    code="timeline_fallback",
                    severity="warning",
                    title="剧情时间线按章节顺序回退",
                    message="时间线 AI 综合未完成，已按章节顺序记录。",
                    payload_json={"chapters": len(chapter_timelines)},
                )
                review_count += 1
            if not clusters:
                self._review(
                    session,
                    book_id=book.id,
                    entity_type="bible",
                    entity_id=bible.id,
                    code="no_characters",
                    severity="warning",
                    title="未识别到角色",
                    message="本书未提取到任何角色，请检查文本或重新生成。",
                    payload_json={},
                )
                review_count += 1

            session.flush()
            scope_ids = {
                cluster.character_id for cluster in clusters if cluster.character_id
            }
            stale_scope = [ReviewItem.entity_id == bible.id]
            if scope_ids:
                stale_scope.append(ReviewItem.entity_id.in_(scope_ids))
            stale_items = list(
                session.scalars(
                    select(ReviewItem).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                        ReviewItem.source == "ai",
                        ReviewItem.updated_at < review_run_start,
                        or_(*stale_scope),
                        or_(
                            ReviewItem.code.in_(
                                (
                                    "suspected_same_person",
                                    "low_confidence_character",
                                    "timeline_fallback",
                                    "no_characters",
                                )
                            ),
                            ReviewItem.code.startswith("alias_conflict:"),
                            ReviewItem.code.startswith("bible_note:"),
                        ),
                    )
                )
            )
            for item in stale_items:
                item.status = "ignored"
                item.resolution_note = "重新生成角色圣经后该疑点未再出现，已自动忽略。"
                item.resolved_at = utc_now()
                item.updated_at = utc_now()

            entity_conditions = [ReviewItem.entity_id == bible.id]
            if scope_ids:
                entity_conditions.append(ReviewItem.entity_id.in_(scope_ids))
            blocking = int(
                session.scalar(
                    select(func.count(ReviewItem.id)).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                        ReviewItem.severity.in_(("blocking", "warning")),
                        or_(*entity_conditions),
                    )
                )
                or 0
            )
            final_status = "needs_review" if (blocking or failed) else "approved"
            content = dict(bible.content_json or {})
            content.update(
                {
                    "schema_version": 1,
                    "status": final_status,
                    "prompt_versions": {
                        "characters": BIBLE_CHARACTER_PROMPT_VERSION,
                        "merge": BIBLE_MERGE_PROMPT_VERSION,
                        "timeline": BIBLE_TIMELINE_PROMPT_VERSION,
                    },
                    "stats": {
                        "chapters": total_chapters,
                        "covered_chapters": covered_count,
                        "analyzed_chapters": max(
                            0, total_chapters - reused_chapters - len(failed)
                        ),
                        "reused_chapters": reused_chapters,
                        "failed_chapters": len(failed),
                        "partial": covered_count < total_chapters,
                        "characters": len(character_summaries),
                        "arcs": arc_total,
                        "relationships": relationship_total,
                        "review_items": review_count,
                        "windows": len(extraction_calls),
                    },
                    "chapters": timeline_chapters,
                    "characters": character_summaries,
                    "relationships": relationship_summaries,
                    "timeline": timeline_json,
                    "chapter_observations": [
                        self._serialize_character_observation(item)
                        for item in observations
                    ],
                    "relationship_observations": [
                        self._serialize_relationship_observation(item)
                        for item in relationships
                    ],
                    "chapter_timelines": [
                        self._serialize_timeline_row(item) for item in chapter_timelines
                    ],
                    "base_bible_id": base_bible_id,
                    "warnings": list(dict.fromkeys(warnings)),
                    "calls": {
                        "summary": summary_calls,
                        "characters": extraction_calls,
                        "merge": merge_calls,
                    },
                    "accepted_by": "auto",
                    "finished_at": _iso_now(),
                }
            )
            bible.status = final_status
            bible.summary = merge_summary
            bible.content_json = content
            bible.timeline_json = timeline_json
            bible.confidence = (
                sum(_clamp(item["confidence"]) for item in character_summaries)
                / len(character_summaries)
                if character_summaries
                else 0.0
            )
            bible.is_active = False
            previous = list(
                session.scalars(
                    select(BookBible).where(
                        BookBible.book_id == book.id,
                        BookBible.id != bible.id,
                        BookBible.is_active.is_(True),
                    )
                )
            )
            for item in previous:
                item.is_active = False
                if item.status in {"approved", "needs_review"}:
                    item.status = "superseded"
                item.updated_at = utc_now()
            if previous:
                stale_bible_reviews = list(
                    session.scalars(
                        select(ReviewItem).where(
                            ReviewItem.book_id == book.id,
                            ReviewItem.entity_type == "bible",
                            ReviewItem.entity_id.in_([item.id for item in previous]),
                            ReviewItem.status == "open",
                            ReviewItem.source == "ai",
                        )
                    )
                )
                for item in stale_bible_reviews:
                    item.status = "ignored"
                    item.resolution_note = (
                        "角色圣经已生成新版本，旧版本疑点已自动失效。"
                    )
                    item.resolved_at = utc_now()
                    item.updated_at = utc_now()
            session.flush()
            bible.is_active = True
            bible.approved_at = utc_now() if final_status == "approved" else None
            bible.updated_at = utc_now()
            book.bible_status = final_status
            book.updated_at = utc_now()
            job = session.get(GenerationJob, job_id)
            if job is not None:
                job.status = "completed"
                job.progress = (
                    covered_count / max(1, total_chapters)
                    if (job.params_json or {}).get("finalize_only")
                    else 1.0
                )
                if failed:
                    job.failed_items = len(failed)
                job.finished_at = utc_now()
                job.updated_at = utc_now()
                job.result_json = {
                    **dict(job.result_json or {}),
                    "started_at": started,
                    "finished_at": _iso_now(),
                    "bible_id": bible.id,
                    "version": bible.version,
                    "status": final_status,
                    "characters": len(character_summaries),
                    "arcs": arc_total,
                    "relationships": relationship_total,
                    "review_items": review_count,
                    "reused_chapters": reused_chapters,
                    "failed_chapters": len(failed),
                    "warnings": len(content["warnings"]),
                }

    # ------------------------------------------------------------------ read

    def _serialize_arc(self, arc: CharacterArc) -> CharacterArcOut:
        return CharacterArcOut(
            id=arc.id,
            arc_index=arc.arc_index,
            label=arc.label,
            age_stage=arc.age_stage,
            chapter_start_id=arc.chapter_start_id,
            chapter_end_id=arc.chapter_end_id,
            story_time_start=arc.story_time_start,
            story_time_end=arc.story_time_end,
            story_order_start=arc.story_order_start,
            story_order_end=arc.story_order_end,
            summary=arc.summary,
            traits=[str(item) for item in (arc.traits_json or [])],
            appearance=arc.appearance_json or {},
            voice_traits=arc.voice_traits_json or {},
            confidence=arc.confidence,
            evidence=arc.evidence_json or [],
            source=arc.source,
            created_at=arc.created_at,
            updated_at=arc.updated_at,
        )

    def _serialize_bible(
        self,
        session: Session,
        bible: BookBible,
        *,
        include_details: bool,
    ) -> BibleOut:
        arcs: list[CharacterArc] = []
        characters: list[Character] = []
        relationships: list[CharacterRelationship] = []
        review_items: list[ReviewItem] = []
        if include_details:
            arcs = list(
                session.scalars(
                    select(CharacterArc)
                    .where(CharacterArc.bible_id == bible.id)
                    .order_by(CharacterArc.character_id, CharacterArc.arc_index)
                )
            )
            character_ids = {item.character_id for item in arcs}
            if character_ids:
                characters = list(
                    session.scalars(
                        select(Character)
                        .where(Character.id.in_(character_ids))
                        .order_by(Character.canonical_name)
                    )
                )
            review_scope = [
                (ReviewItem.entity_type == "bible")
                & (ReviewItem.entity_id == bible.id)
            ]
            if character_ids:
                review_scope.append(
                    (ReviewItem.entity_type == "character")
                    & ReviewItem.entity_id.in_(character_ids)
                )
            review_items = list(
                session.scalars(
                    select(ReviewItem)
                    .where(
                        ReviewItem.book_id == bible.book_id,
                        ReviewItem.status == "open",
                        or_(*review_scope),
                    )
                    .order_by(ReviewItem.severity, ReviewItem.created_at.desc())
                )
            )
            relationships = list(
                session.scalars(
                    select(CharacterRelationship)
                    .where(
                        CharacterRelationship.book_id == bible.book_id,
                        CharacterRelationship.source_character_id.in_(character_ids),
                        CharacterRelationship.target_character_id.in_(character_ids),
                    )
                    .order_by(CharacterRelationship.created_at)
                )
            ) if character_ids else []
        arcs_by_character: dict[str, list[CharacterArc]] = defaultdict(list)
        for arc in arcs:
            arcs_by_character[arc.character_id].append(arc)
        character_out = [
            BibleCharacterOut(
                id=item.id,
                canonical_name=item.canonical_name,
                role_tier=item.role_tier,
                gender=item.gender,
                age_range=item.age_range,
                character_type=item.character_type,
                identity_description=item.identity_description,
                personality=[str(tag) for tag in (item.personality_json or [])],
                appearance_desc=item.appearance_desc,
                voice_description=item.voice_description,
                aliases=[
                    {
                        "name": alias["name"],
                        "confidence": alias["confidence"],
                        "basis": alias["basis"],
                        "chapter_id": alias.get("chapter_id"),
                        "evidence": alias.get("evidence") or [],
                        "source": alias.get("source") or "ai",
                    }
                    for alias in normalize_aliases(item.aliases_json)
                ],
                confidence=item.confidence,
                is_provisional=item.is_provisional,
                user_locked=item.user_locked,
                arcs=[
                    self._serialize_arc(arc)
                    for arc in arcs_by_character.get(item.id, [])
                ],
                evidence=item.evidence_json or [],
            )
            for item in characters
        ]
        timeline_raw = bible.timeline_json or {}
        timeline = BibleTimelineOut(
            narrative_mode=str(timeline_raw.get("narrative_mode") or "unknown"),
            notes=[str(item) for item in (timeline_raw.get("notes") or [])],
            chapters=[
                TimelineChapterOut(
                    chapter_id=str(item.get("chapter_id") or ""),
                    sequence=int(item.get("sequence") or 0),
                    title=str(item.get("title") or ""),
                    summary=item.get("summary"),
                    story_time_label=item.get("story_time_label"),
                    story_order=item.get("story_order"),
                    time_mode=str(item.get("time_mode") or "unknown"),
                    confidence=float(item.get("confidence") or 0.0),
                    characters=[str(name) for name in (item.get("characters") or [])],
                )
                for item in (timeline_raw.get("chapters") or [])
                if isinstance(item, dict)
            ],
        )
        return BibleOut(
            id=bible.id,
            book_id=bible.book_id,
            version=bible.version,
            status=bible.status,
            source=bible.source,
            prompt_version=bible.prompt_version,
            model=bible.model,
            summary=bible.summary,
            confidence=bible.confidence,
            is_active=bible.is_active,
            approved_at=bible.approved_at,
            stats=(bible.content_json or {}).get("stats") or {},
            warnings=[
                str(item) for item in ((bible.content_json or {}).get("warnings") or [])
            ],
            characters=character_out,
            relationships=[
                to_relationship_out(
                    item,
                    {
                        character.id: character.canonical_name
                        for character in characters
                    },
                )
                for item in relationships
            ],
            timeline=timeline,
            review_items=[
                BibleReviewItemOut(
                    id=item.id,
                    entity_type=item.entity_type,
                    entity_id=item.entity_id,
                    code=item.code,
                    severity=item.severity,
                    title=item.title,
                    message=item.message,
                    payload=item.payload_json or {},
                    status=item.status,
                    source=item.source,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
                for item in review_items
            ],
            created_at=bible.created_at,
            updated_at=bible.updated_at,
        )

    def _active_bible(self, session: Session, book_id: str) -> BookBible | None:
        return session.scalar(
            select(BookBible)
            .where(BookBible.book_id == book_id, BookBible.is_active.is_(True))
            .order_by(BookBible.version.desc())
            .limit(1)
        )

    def _latest_job(self, session: Session, book_id: str) -> GenerationJob | None:
        return session.scalar(
            select(GenerationJob)
            .where(GenerationJob.book_id == book_id, GenerationJob.job_type == JOB_TYPE)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )

    def bible_status(self, book_id: str) -> BibleStatusResponse:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            active = self._active_bible(session, book.id)
            job = self._latest_job(session, book.id)
            open_reviews = int(
                session.scalar(
                    select(func.count(ReviewItem.id)).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                    )
                )
                or 0
            )
            return BibleStatusResponse(
                book_id=book.id,
                bible_status=book.bible_status,
                open_reviews=open_reviews,
                bible_id=active.id if active else None,
                version=active.version if active else None,
                job=to_job_out(job) if job else None,
            )

    def get_bible(self, book_id: str) -> BibleStateResponse:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            active = self._active_bible(session, book.id)
            job = self._latest_job(session, book.id)
            open_reviews = int(
                session.scalar(
                    select(func.count(ReviewItem.id)).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.status == "open",
                    )
                )
                or 0
            )
            return BibleStateResponse(
                book_id=book.id,
                bible_status=book.bible_status,
                open_reviews=open_reviews,
                bible=self._serialize_bible(session, active, include_details=True)
                if active
                else None,
                job=to_job_out(job) if job else None,
            )

    def finalize_partial(self, book_id: str) -> AnalysisJobOut:
        """停止当前任务，并用已完成章节先合并出一版可用的设定集。"""
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            chapters = self._chapters(session, book.id)
            running = self._active_job(session, book.id)
            bible: BookBible | None = None
            if running is not None:
                if (running.params_json or {}).get("finalize_only"):
                    raise ConflictError(
                        "设定集正在收尾，请稍候", code="bible_finalizing"
                    )
                bible = session.get(BookBible, running.target_id or "")
                # 只停任务，不把版本标成 failed：接下来按已完成章节收尾。
                running.status = "cancelled"
                running.finished_at = utc_now()
                running.updated_at = utc_now()
            else:
                resume = self._resume_bible(session, book.id, chapters)
                if resume is not None:
                    bible = resume[0]
            if bible is None:
                raise ValidationError(
                    "没有可收尾的设定集进度", code="bible_no_partial"
                )
            content = dict(bible.content_json or {})
            covered = {
                str(row.get("chapter_id"))
                for row in (content.get("chapter_timelines") or [])
                if isinstance(row, dict)
            } & {chapter.id for chapter in chapters}
            if not covered:
                raise ValidationError(
                    "当前设定集还没有完成任何章节", code="bible_no_partial"
                )
            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=bible.id,
                target_type="book",
                stage="bible",
                job_type=JOB_TYPE,
                status="pending",
                total_items=len(chapters),
                completed_items=len(covered),
                failed_items=0,
                progress=len(covered) / max(1, len(chapters)),
                attempt_count=0,
                max_attempts=1,
                params_json={
                    "bible_id": bible.id,
                    "finalize_only": True,
                    "covered": len(covered),
                },
                result_json={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            session.flush()
            book.bible_status = "running"
            book.updated_at = utc_now()
            output = to_job_out(job)
        thread = threading.Thread(
            target=self._run_finalize_job,
            args=(output.id,),
            name=f"novelvoice-bible-finalize-{output.id}",
            daemon=True,
        )
        thread.start()
        return output

    def _run_finalize_job(self, job_id: str) -> None:
        started = _iso_now()
        try:
            # 给刚被取消的抽取线程一点时间退出，避免它再写一章。
            time.sleep(2)
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = utc_now()
                job.updated_at = utc_now()
                book = self._book(session, job.book_id)
                chapters = self._chapters(session, book.id)
                bible = session.get(BookBible, job.target_id or "")
                if bible is None:
                    raise AppError("角色圣经版本不存在", status_code=500)
                covered = {
                    str(row.get("chapter_id"))
                    for row in ((bible.content_json or {}).get("chapter_timelines") or [])
                    if isinstance(row, dict)
                } & {chapter.id for chapter in chapters}
                reuse_ids = [chapter.id for chapter in chapters if chapter.id in covered]
                (
                    observations,
                    relationships,
                    timelines,
                    base_id,
                ) = self._load_previous_state(
                    session, book, chapters, reuse_ids, bible.id
                )
            self._finalize(
                job_id,
                observations,
                relationships,
                timelines,
                chapters,
                [],
                [],
                started,
                reused_chapters=len(covered),
                base_bible_id=base_id,
                failed_chapters=[],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("角色圣经收尾任务 %s 异常终止", job_id)
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "failed"
                job.error_code = str(getattr(exc, "code", "bible_finalize_failed"))
                job.error_message = str(getattr(exc, "message", exc))[:2000]
                job.finished_at = utc_now()
                job.updated_at = utc_now()

    def cancel_book_bible(self, book_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            job = self._active_job(session, book.id)
            if job is None:
                raise ConflictError("本书没有正在生成的角色圣经任务", code="no_active_job")
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            bible = session.get(BookBible, job.target_id or "")
            if bible is not None and bible.status == "running":
                bible.status = "failed"
                content = dict(bible.content_json or {})
                content["status"] = "cancelled"
                bible.content_json = content
                bible.updated_at = utc_now()
            self._restore_book_status(session, book)
            return to_job_out(job)


def _display_for_key(names: dict[str, Counter[str]], key: str) -> str:
    counter = names.get(key)
    if counter:
        return sorted(
            counter.items(), key=lambda item: (-item[1], -len(item[0]), item[0])
        )[0][0]
    return key


def recover_interrupted_bible(session: Session) -> dict[str, int]:
    """启动时恢复 P2 中断状态：任务、圣经版本与书籍状态。"""
    jobs = list(
        session.scalars(
            select(GenerationJob).where(
                GenerationJob.job_type == JOB_TYPE,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    )
    now = utc_now()
    for job in jobs:
        job.status = "failed"
        job.error_code = "interrupted"
        job.error_message = "应用在生成角色圣经过程中退出，任务已中断；请重新生成。"
        job.finished_at = now
        job.updated_at = now
    bibles = list(
        session.scalars(
            select(BookBible).where(BookBible.status == "running")
        )
    )
    for bible in bibles:
        bible.status = "failed"
        content = dict(bible.content_json or {})
        content["status"] = "failed"
        content["error"] = {
            "code": "interrupted",
            "message": "应用退出，角色圣经生成中断。",
            "at": _iso_now(),
        }
        bible.content_json = content
        bible.updated_at = now
    books = list(
        session.scalars(
            select(Book).where(Book.bible_status == "running")
        )
    )
    for book in books:
        active = session.scalar(
            select(BookBible)
            .where(BookBible.book_id == book.id, BookBible.is_active.is_(True))
            .order_by(BookBible.version.desc())
            .limit(1)
        )
        book.bible_status = (
            active.status
            if active is not None and active.status in {"approved", "needs_review"}
            else "failed"
        )
    return {"jobs": len(jobs), "bibles": len(bibles)}