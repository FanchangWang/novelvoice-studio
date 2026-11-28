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
import json
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
    ScriptUnitSpeakerUpdate,
    ScriptVersionOut,
    ScriptVersionListResponse,
)
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.paths import new_id, validate_id
from novelvoice_studio.services.production_status import (
    effective_stage_status,
    effective_unit_status,
    effective_version_status,
    has_open_blocking,
    stage_review_scope,
)
from novelvoice_studio.services.review_policy import policy_for
from novelvoice_studio.services.scenes import SceneService
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


_QUOTE_EQUIVALENTS = str.maketrans({
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'", "「": '"', "」": '"',
    "『": '"', "』": '"',
})


def _source_match_index(text: str) -> tuple[str, list[int], list[int]]:
    """建立容错匹配文本到原始字符位置的映射，不改写最终写入的原文。"""
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        normalized.append(char.translate(_QUOTE_EQUIVALENTS))
        starts.append(index)
        ends.append(index + 1)
    return "".join(normalized), starts, ends


def _within_one_edit(left: str, right: str) -> bool:
    """只接受最多一次字符替换、遗漏或额外插入，避免宽泛模糊匹配。"""
    difference = len(left) - len(right)
    if abs(difference) > 1:
        return False
    if difference == 0:
        return sum(a != b for a, b in zip(left, right, strict=True)) <= 1
    shorter, longer = (right, left) if difference > 0 else (left, right)
    offset = 0
    while offset < len(shorter) and shorter[offset] == longer[offset]:
        offset += 1
    return shorter[offset:] == longer[offset + 1 :]


def _fuzzy_source_span(
    quote: str,
    normalized_source: str,
    normalized_cursor: int,
    source_starts: list[int],
    source_ends: list[int],
) -> tuple[int, int] | None:
    """为长引文的一次抄写偏差定位；候选不唯一时放弃，交由原文补齐。"""
    if len(quote) < 24:
        return None
    anchor_size = min(8, len(quote) // 3)
    anchors = {
        (quote[:anchor_size], 0),
        (quote[len(quote) // 2 : len(quote) // 2 + anchor_size], len(quote) // 2),
        (quote[-anchor_size:], len(quote) - anchor_size),
    }
    candidates: set[tuple[int, int]] = set()
    for source_length in (len(quote) - 1, len(quote), len(quote) + 1):
        for anchor, quote_offset in anchors:
            position = normalized_source.find(anchor, normalized_cursor)
            while position >= 0:
                # 锚点前若发生一次替换/增删，真实起点会偏移一个字符。
                anchor_start = position - quote_offset
                for start in (anchor_start - 1, anchor_start, anchor_start + 1):
                    if start >= normalized_cursor and start + source_length <= len(normalized_source):
                        candidate = normalized_source[start : start + source_length]
                        if _within_one_edit(quote, candidate):
                            candidates.add((start, start + source_length))
                position = normalized_source.find(anchor, position + 1)
    if len(candidates) != 1:
        return None
    start, end = next(iter(candidates))
    return source_starts[start], source_ends[end - 1]


def _find_source_span(
    quote: str,
    source: str,
    cursor: int,
    match_index: tuple[str, list[int], list[int]] | None = None,
) -> tuple[int, int] | None:
    """严格优先定位原文；容错仅处理空白、引号和长引文的一次抄写偏差。"""
    strict = source.find(quote, cursor)
    if strict >= 0:
        return strict, strict + len(quote)
    normalized, starts, ends = match_index or _source_match_index(source)
    normalized_quote = "".join(quote.split()).translate(_QUOTE_EQUIVALENTS)
    if not normalized_quote:
        return None
    normalized_cursor = next(
        (index for index, start in enumerate(starts) if start >= cursor), len(starts)
    )
    local = normalized.find(normalized_quote, normalized_cursor)
    if local >= 0:
        return starts[local], ends[local + len(normalized_quote) - 1]
    return _fuzzy_source_span(
        normalized_quote, normalized, normalized_cursor, starts, ends
    )


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
    """按引号外的句末标点切块，避免把一句对白或船名拦腰截断。"""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    length = len(text)
    closing = {"“": "”", "「": "」", "『": "』", "‘": "’"}
    while cursor < length:
        hard_end = min(length, cursor + limit)
        if hard_end >= length:
            end = length
        else:
            quote: str | None = None
            cut = -1
            for position in range(cursor, hard_end):
                char = text[position]
                if quote is not None:
                    if char == quote:
                        quote = None
                    continue
                if char in closing:
                    quote = closing[char]
                    continue
                if char in "。！？!?；;\n":
                    cut = position + 1
            if cut < 0:
                # 硬上限落在引号内时，优先延长到闭合引号，再寻找句末。
                if quote is not None:
                    close_at = text.find(quote, hard_end)
                    if close_at >= 0:
                        extended = min(length, close_at + 1)
                        for position in range(close_at + 1, min(length, extended + 80)):
                            if text[position] in "。！？!?；;\n":
                                extended = position + 1
                                break
                        cut = extended
                if cut < 0:
                    cut = hard_end
            end = cut
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


_QUOTE_SPAN = re.compile(r"[“「『‘]([^”」』’]*)[”」』’]")
_INNER_MARKERS = ("心想", "心里", "暗想", "默念", "暗自")
_NARRATIVE_QUOTE_CUES = re.compile(
    r"(?:喊|叫|称|唤|提及|说起|名叫|名为|昵称|绰号|夸|笑称)"
)
_OBJECT_QUOTE_CUES = re.compile(
    r"(?:船名|舰名|飞船|战舰|舰|船名|名叫|名为|叫做|称为|称作|提名|书名|篇名|名为|题名)"
)
_SPEECH_CUES = re.compile(
    r"(?:说|问|答|喊|叫|称|道|笑|骂|吼|啜|哽咽|回答|反问|自语)"
)
_SENTENCE_END = set("。！？!?；;\n")


def _quote_spans(text: str) -> list[tuple[int, int, str, str]]:
    return [
        (match.start(), match.end(), match.group(0), match.group(1))
        for match in _QUOTE_SPAN.finditer(text)
    ]


def _is_direct_quote(text: str, start: int, end: int, inner: str) -> bool:
    prefix = text[max(0, start - 28) : start]
    suffix = text[end : end + 36]
    cleaned = inner.strip()
    if len(cleaned) <= 40 and (
        _OBJECT_QUOTE_CUES.search(prefix[-24:])
        or re.match(r"\s*(?:号|舰|船|轮|机|艇)", suffix[:8])
    ):
        return False
    title_like = bool(
        re.fullmatch(r".{1,4}(?:姐|哥|姨|叔|爷|奶|先生|小姐|老师|同学)", cleaned)
        or re.fullmatch(r"(.)\1", cleaned)
    )
    # 引号常用来标记被呼喊/提及的称谓，不能仅凭引号判成对白。
    if (
        len(cleaned) <= 12
        and title_like
        and (_NARRATIVE_QUOTE_CUES.search(prefix) or _NARRATIVE_QUOTE_CUES.search(suffix))
    ):
        return False
    if text[start] not in {"‘"}:
        return True
    # 单引号常用来标记被呼喊/提及的称谓，不能仅凭引号判成对白。
    if (
        len(cleaned) <= 12
        and not any(mark in cleaned for mark in "，。！？!?；;")
        and (_NARRATIVE_QUOTE_CUES.search(prefix) or _NARRATIVE_QUOTE_CUES.search(suffix))
    ):
        return False
    # 句首单引号且后文出现声音/说话动作时，仍按直接对白处理。
    if not prefix.strip() and _SPEECH_CUES.search(suffix):
        return True
    return not bool(_NARRATIVE_QUOTE_CUES.search(prefix))


def _direct_quote_spans(text: str) -> list[tuple[int, int, str, str]]:
    return [
        span
        for span in _quote_spans(text)
        if _is_direct_quote(text, span[0], span[1], span[3])
    ]


def _candidate_type(text: str) -> str:
    if _direct_quote_spans(text):
        return "dialogue"
    if any(marker in text for marker in _INNER_MARKERS):
        return "inner_monologue"
    return "narration"


def _has_only_narrative_quotes(text: str) -> bool:
    spans = _quote_spans(text)
    return bool(spans) and not any(
        _is_direct_quote(text, start, end, inner) for start, end, _raw, inner in spans
    )


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """引号感知切句；问号、叹号留在完整对白内部，不拆断台词。"""
    spans: list[tuple[int, int]] = []
    cursor = 0
    quote: str | None = None
    closing = {"“": "”", "「": "」", "『": "』", "‘": "’"}
    for index, char in enumerate(text):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in closing:
            quote = closing[char]
            continue
        if char in _SENTENCE_END:
            spans.append((cursor, index + 1))
            cursor = index + 1
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans


def _atomic_source_segments(text: str) -> list[tuple[int, int, str | None]]:
    """返回连续原文片段；直接引号整体作为一个对白单元。"""
    direct = _direct_quote_spans(text)
    if not direct:
        return [(start, end, None) for start, end in _sentence_spans(text)]
    segments: list[tuple[int, int, str | None]] = []
    cursor = 0
    for start, end, _raw, inner in direct:
        for piece_start, piece_end in _sentence_spans(text[cursor:start]):
            segments.append((cursor + piece_start, cursor + piece_end, None))
        segments.append((start, end, inner))
        cursor = end
    for piece_start, piece_end in _sentence_spans(text[cursor:]):
        segments.append((cursor + piece_start, cursor + piece_end, None))
    return segments


def _derived_aliases(name: str) -> tuple[str, ...]:
    cleaned = " ".join((name or "").split())
    if not cleaned or any(char in cleaned for char in "·• "):
        return ()
    found: list[str] = []
    # 角色名中的“（已故）/（青年）”等整理标注不属于正文称呼；仅在本书
    # 派生称谓唯一时才启用，避免与另一个正式角色名冲突。
    annotated = re.fullmatch(r"(.{2,12})[（(][^）)]{1,12}[）)]", cleaned)
    if annotated:
        found.append(annotated.group(1))
    if not 2 <= len(cleaned) <= 4:
        return tuple(dict.fromkeys(item for item in found if item != cleaned))
    last = cleaned[-1]
    if last in {"姐", "哥", "姨", "叔", "爷", "奶", "母", "父", "亲"}:
        return tuple(found)
    found.extend([f"{last}姐", f"{last}哥", f"{last}{last}", f"{cleaned[0]}同学"])
    # “民警老周”等职业前缀姓名在正文中常直接称呼为“老周”。仅在本书
    # 派生称谓唯一时才会启用，避免把常见姓氏或称谓误绑到多个角色。
    role_prefix = re.match(
        r"^(?:民警|警察|医生|护士|老师|师傅|老板|经理|店长|队长|校长|教授)"
        r"(老.{1,2}|小.{1,2})$",
        cleaned,
    )
    if role_prefix:
        found.append(role_prefix.group(1))
    return tuple(dict.fromkeys(item for item in found if item != cleaned))


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
    aligned_chars: int = 0


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
        if not chapter.analysis_included:
            raise ValidationError(
                "该章节当前未纳入分析；如确需生成剧本，请先在章节设置中开启。",
                code="chapter_excluded",
            )
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
        self, session: Session, book_id: str, chapter_text: str = ""
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
        full: list[dict[str, Any]] = []
        for row in characters:
            aliases: list[str] = []
            for alias in row.aliases_json or []:
                name = alias.get("name") if isinstance(alias, dict) else alias
                cleaned = _clean_name(name)
                if cleaned and cleaned != row.canonical_name and cleaned not in aliases:
                    aliases.append(cleaned)
            for derived in _derived_aliases(row.canonical_name):
                if derived not in aliases:
                    aliases.append(derived)
            item = {
                "canonical_name": row.canonical_name,
                "aliases": aliases,
                "gender": row.gender,
                "age_range": row.age_range,
                "identity_description": row.identity_description,
                "voice_description": row.voice_description,
                "role_tier": row.role_tier,
            }
            hits = 0
            for name in (row.canonical_name, *aliases):
                if name and len(name) >= 2:
                    hits += chapter_text.count(name)
            full.append({**item, "_hits": hits})
        candidates = [item for item in full if item["_hits"] > 0]
        candidates.sort(key=lambda item: -int(item["_hits"]))
        if len(candidates) < 8:
            for tier in ("protagonist", "major", "supporting"):
                for item in full:
                    if item["role_tier"] == tier and item not in candidates:
                        candidates.append(item)
                    if len(candidates) >= 24:
                        break
                if len(candidates) >= 24:
                    break
        budget = 9000
        for item in candidates[:64]:
            cleaned = {
                "canonical_name": item["canonical_name"],
                "aliases": list(item["aliases"])[:12],
                "gender": item["gender"],
                "age_range": item["age_range"],
                "identity_description": _clean_text(item["identity_description"], 60),
                "voice_hint": _clean_text(item["voice_description"], 40),
                "role_tier": item["role_tier"],
            }
            cost = len(json.dumps(cleaned, ensure_ascii=False, default=str))
            if payload and budget - cost < 0:
                break
            budget -= cost
            payload.append(cleaned)
        return characters, payload

    def _character_index(
        self, characters: list[Character]
    ) -> tuple[dict[str, list[Character]], dict[str, list[Character]]]:
        canonical: dict[str, list[Character]] = {}
        aliases: dict[str, list[Character]] = {}
        derived: dict[str, list[Character]] = {}
        for row in characters:
            canonical.setdefault(_norm(row.canonical_name), []).append(row)
            for alias in row.aliases_json or []:
                name = alias.get("name") if isinstance(alias, dict) else alias
                cleaned = _clean_name(name)
                if cleaned:
                    aliases.setdefault(_norm(cleaned), []).append(row)
            for name in _derived_aliases(row.canonical_name):
                derived.setdefault(_norm(name), []).append(row)
        # 仅当派生称谓在本书唯一时才自动归并，避免“X姐/X哥”误绑多人。
        for key, rows in derived.items():
            unique = {row.id: row for row in rows}
            if len(unique) == 1:
                aliases[key] = list(unique.values())
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

    @staticmethod
    def _mask_quotes(text: str) -> str:
        chars = list(text)
        for start, end, _raw, _inner in _direct_quote_spans(text):
            for index in range(start, end):
                if chars[index] != "\n":
                    chars[index] = " "
        return "".join(chars)

    def _infer_dialogue_speaker(
        self,
        source_text: str,
        quote_start: int,
        quote_end: int,
        context_text: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
    ) -> tuple[Character | None, str | None]:
        """从前置/后置说话动作及紧邻上下文消解说话人。"""
        names: dict[str, Character] = {}
        for key, rows in {**aliases, **canonical}.items():
            unique = {row.id: row for row in rows}
            if len(unique) == 1:
                names[key] = next(iter(unique.values()))
        if not names:
            return None, "no_registered_names"
        pattern = re.compile("|".join(re.escape(key) for key in sorted(names, key=len, reverse=True)))
        scored: dict[str, tuple[int, Character]] = {}

        def scan(
            text: str,
            *,
            local: bool,
            quote_start_override: int | None = None,
            quote_end_override: int | None = None,
        ) -> None:
            masked = self._mask_quotes(text)
            local_start = quote_start if quote_start_override is None else quote_start_override
            local_end = quote_end if quote_end_override is None else quote_end_override
            matches = list(pattern.finditer(masked))
            for position, match in enumerate(matches):
                character = names[match.group(0)]
                score = 0
                if local:
                    if match.end() <= local_start:
                        gap = text[match.end() : local_start]
                        # “他看向陆远：……”里的陆远是听话人，不是说话人。
                        # 向前寻找最近的角色动作主体；允许跨一个完整句子，以覆盖
                        # “老周拿出手机说明情况。等待时，他看向陆远：……”这类写法。
                        addressed = bool(
                            re.fullmatch(r"\s*[:：，,]\s*", gap)
                            and re.search(
                                r"(?:看向|望向|望着|看着|转向|朝向|朝|对|向|问|询问)\s*$",
                                text[max(0, match.start() - 16) : match.start()],
                            )
                        )
                        if addressed:
                            previous = next(
                                (
                                    item
                                    for item in reversed(matches[:position])
                                    if item.end() <= match.start()
                                ),
                                None,
                            )
                            if previous is not None:
                                bridge = text[previous.end() : match.start()]
                                if len(bridge) <= 160 and sum(
                                    char in bridge for char in "。！？!?；;"
                                ) <= 2:
                                    speaker = names[previous.group(0)]
                                    scored[speaker.id] = (6, speaker)
                            continue
                        explicit_tag = bool(_SPEECH_CUES.search(gap[-30:])) or bool(
                            re.fullmatch(r"\s*[:：，,]\s*", gap)
                        )
                        if not any(mark in gap for mark in "。！？!?；;\n") and explicit_tag:
                            score = 6
                    elif match.start() >= local_end:
                        gap = text[local_end : match.end() + 4]
                        # 下一句的说话动作不能反过来绑定上一句台词；句末标点可能位于后引号内。
                        quote_ends_sentence = (
                            local_end >= 2 and text[local_end - 2] in "。！？!?；;"
                        )
                        if (
                            not quote_ends_sentence
                            and not any(mark in gap for mark in "。！？!?；;\n")
                            and _SPEECH_CUES.search(gap[:30])
                        ):
                            score = 6
                else:
                    before = text[max(0, match.start() - 30) : match.start()]
                    after = text[match.end() : match.end() + 30]
                    if _SPEECH_CUES.search(before):
                        score = 6
                    elif _SPEECH_CUES.search(after):
                        score = 5
                if score <= 0:
                    continue
                previous = scored.get(character.id)
                if previous is None or score > previous[0]:
                    scored[character.id] = (score, character)

        scan(source_text, local=True)
        if not any(score >= 3 for score, _character in scored.values()):
            scored.clear()
            context_start = context_text.find(source_text)
            if context_start >= 0:
                context_quote_start = context_start + quote_start
                context_quote_end = context_start + quote_end
                scan(
                    context_text,
                    local=True,
                    quote_start_override=context_quote_start,
                    quote_end_override=context_quote_end,
                )
                if not any(score >= 3 for score, _character in scored.values()):
                    # 常见写法是上一句先写角色动作，下一句直接给台词。只接受最近且
                    # 没有跨越多句的角色提及，避免把整章上下文中的角色混在一起。
                    masked = self._mask_quotes(context_text)
                    before = masked[:context_quote_start]
                    matches = list(pattern.finditer(before))
                    if matches:
                        nearest = matches[-1]
                        gap = before[nearest.end() :]
                        if len(gap) <= 100 and sum(
                            mark in "。！？!?；;" for mark in gap
                        ) <= 1:
                            character = names[nearest.group(0)]
                            scored[character.id] = (4, character)
            if not scored:
                scored.clear()
                scan(context_text, local=False)
        if not scored:
            return None, "no_context_speaker"
        ranked = sorted(scored.values(), key=lambda item: item[0], reverse=True)
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            return None, "ambiguous_context_speaker"
        return ranked[0][1], "context_speaker_resolved"

    def _expand_source_draft(
        self,
        draft: ScriptUnitDraft,
        source_text: str,
        context_text: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
    ) -> list[ScriptUnitDraft]:
        """把模型大单元中的直接引号拆开；多引号时不复制模型说话人。"""
        direct = _direct_quote_spans(source_text)
        if draft.unit_type in SCRIPT_CUE_TYPES or not direct:
            return [draft]
        if draft.unit_type == "narration" and _has_only_narrative_quotes(source_text):
            return [draft]
        model_speaker: Character | None = None
        if len(direct) == 1 and draft.speaker_name:
            model_speaker, _ = self._resolve_character(
                draft.speaker_name, canonical, aliases
            )
        expanded: list[ScriptUnitDraft] = []
        for start, end, inner in _atomic_source_segments(source_text):
            piece = source_text[start:end]
            if not piece.strip():
                continue
            if inner is None:
                expanded.append(
                    draft.model_copy(
                        update={
                            "unit_type": "narration",
                            "source_text": piece,
                            "text": piece,
                            "display_text": piece,
                            "speaker_type": "narrator",
                            "speaker_name": None,
                        }
                    )
                )
                continue
            speaker = model_speaker
            if speaker is None:
                speaker, reason = self._infer_dialogue_speaker(
                    source_text, start, end, context_text, canonical, aliases
                )
            confidence = max(
                float(draft.confidence or 0.0),
                0.9 if model_speaker else 0.85
                if speaker and _SPEECH_CUES.search(source_text)
                else 0.75 if speaker else 0.5,
            )
            expanded.append(
                draft.model_copy(
                    update={
                        "unit_type": "dialogue",
                        "source_text": piece,
                        "text": " ".join(inner.split()),
                        "display_text": piece,
                        "speaker_type": "character" if speaker else "unknown",
                        "speaker_name": speaker.canonical_name if speaker else None,
                        "confidence": confidence,
                    }
                )
            )
        return expanded

    @staticmethod
    def _inherit_performance(units: list[_UnitPlan]) -> None:
        previous: _UnitPlan | None = None
        for unit in units:
            if unit.unit_type != "dialogue" or not unit.speaker_id:
                if (
                    previous is not None
                    and unit.unit_type in {"narration", "inner_monologue"}
                    and len(unit.source_text.strip()) <= 60
                ):
                    continue
                previous = None
                continue
            if previous is not None and previous.speaker_id == unit.speaker_id:
                if unit.emotion == "neutral" and previous.emotion != "neutral":
                    unit.emotion = previous.emotion
                if unit.delivery == "normal" and previous.delivery != "normal":
                    unit.delivery = previous.delivery
                if unit.intensity <= 1 and previous.intensity > 1:
                    unit.intensity = previous.intensity
                if not unit.tts_instruction and previous.tts_instruction:
                    unit.tts_instruction = previous.tts_instruction
            previous = unit

    # ------------------------------------------------------------ start/cancel

    def start_script(
        self,
        chapter_id: str,
        adaptation_level: str = "light",
        *,
        retry_scenes: bool = False,
    ) -> AnalysisJobOut:
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
            scenes = self._scene_rows(session, chapter)
            if not scenes and not retry_scenes:
                if chapter.scene_version <= 0:
                    raise ValidationError(
                        "请先生成并确认本章分场，再生成有声剧本",
                        code="scenes_required",
                    )
                raise ValidationError(
                    "当前分场版本没有可用场景，请重新生成分场",
                    code="scenes_required",
                )
            if scenes:
                source = BookService(self.settings, session).read_source(book)
                scene_sources = self._scene_sources(session, chapter, source, scenes)
                chunks = self._chunks(scene_sources)
                if not chunks:
                    raise ValidationError("章节没有可用原文，无法生成剧本", code="chapter_empty")
            else:
                chunks = []
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
                    "scenes": len(scene_sources) if scenes else 0,
                    "retry_scenes": retry_scenes and not scenes,
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
            _, conditions = stage_review_scope(session, chapter, "script", version)
            open_reviews = sum(
                1
                for item in session.scalars(
                    select(ReviewItem).where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.category == "content",
                        ReviewItem.status == "open",
                        ReviewItem.code.startswith("script_"),
                        or_(*conditions),
                    )
                )
                if policy_for(item.code, category=item.category).actionable
            )
            blocking = has_open_blocking(session, book.id, "script", conditions)
            return ScriptStatusResponse(
                chapter_id=chapter.id,
                book_id=book.id,
                script_status=effective_stage_status(
                    chapter.script_status,
                    active=active,
                    blocking=blocking,
                ),
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
            _, conditions = stage_review_scope(session, chapter, "script", row)
            reviews = [
                item
                for item in session.scalars(
                    select(ReviewItem)
                    .where(
                        ReviewItem.book_id == book.id,
                        ReviewItem.category == "content",
                        ReviewItem.status == "open",
                        ReviewItem.code.startswith("script_"),
                        or_(*conditions),
                    )
                    .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
                )
                if policy_for(item.code, category=item.category).actionable
            ]
            blocking_unit_ids = {
                item.entity_id
                for item in reviews
                if item.severity == "blocking"
                and item.entity_type == "script_unit"
                and item.entity_id
            }
            blocking = any(item.severity == "blocking" for item in reviews)
            return ScriptStateResponse(
                chapter_id=chapter.id,
                book_id=book.id,
                script_status=effective_stage_status(
                    chapter.script_status,
                    active=active,
                    blocking=blocking,
                ),
                script_version=row.version if row else 0,
                adaptation_level=row.adaptation_level if row else None,
                open_reviews=len(reviews),
                job=to_job_out(job) if job else None,
                version=(
                    self._version_out(
                        row,
                        len(units),
                        blocking=blocking,
                    )
                    if row
                    else None
                ),
                units=[
                    self._unit_out(
                        unit,
                        names.get(unit.speaker_id),
                        voices.get(unit.id),
                        blocking=unit.id in blocking_unit_ids,
                    )
                    for unit in units
                ],
                review_items=[self._review_out(item) for item in reviews],
            )

    def update_unit_speaker(
        self, unit_id: str, payload: ScriptUnitSpeakerUpdate
    ) -> ScriptUnitOut:
        """用户确认问题台词的说话人；只允许当前版本与本书角色。"""
        validate_id(unit_id, "unit")
        with session_scope(self.session_factory) as session:
            unit = session.get(ScriptUnit, unit_id)
            if unit is None:
                raise NotFoundError("剧本单元不存在")
            version = session.get(ScriptVersion, unit.script_version_id)
            if version is None or not version.is_active:
                raise ConflictError("历史剧本版本不可修改", code="script_version_inactive")
            if unit.unit_type not in {"dialogue", "inner_monologue", "narration"}:
                raise ValidationError("该剧本单元不支持设置说话人")
            speaker_name = "未知"
            if payload.speaker_type == "character":
                validate_id(payload.speaker_id or "", "char")
                character = session.get(Character, payload.speaker_id)
                if (
                    character is None
                    or character.book_id != unit.book_id
                    or character.merged_into_id is not None
                ):
                    raise ValidationError("说话人必须是本书的有效角色")
                speaker_name = character.canonical_name
            elif payload.speaker_type == "narrator":
                if payload.speaker_id:
                    raise ValidationError("旁白不需要角色 ID")
                speaker_name = "旁白"
            elif payload.speaker_id:
                raise ValidationError("未知说话人不能绑定角色")
            unit.speaker_type = payload.speaker_type
            unit.speaker_id = (
                payload.speaker_id if payload.speaker_type == "character" else None
            )
            unit.speaker_name_snapshot = speaker_name
            unit.voice_cast_id = None
            unit.review_status = "approved"
            unit.source = "user"
            locked = list(unit.user_locked_fields_json or [])
            for field in ("speaker_type", "speaker_id"):
                if field not in locked:
                    locked.append(field)
            unit.user_locked_fields_json = locked
            unit.updated_at = utc_now()
            refresh_stale_units(session, [unit])
            session.flush()
            unresolved = session.scalar(
                select(func.count(ScriptUnit.id)).where(
                    ScriptUnit.script_version_id == version.id,
                    ScriptUnit.unit_type == "dialogue",
                    ScriptUnit.speaker_type != "character",
                )
            )
            if not unresolved:
                reviews = list(
                    session.scalars(
                        select(ReviewItem).where(
                            ReviewItem.book_id == unit.book_id,
                            ReviewItem.entity_type == "chapter",
                            ReviewItem.entity_id == unit.chapter_id,
                            ReviewItem.code == "script_unresolved_speaker",
                            ReviewItem.status == "open",
                        )
                    )
                )
                for review in reviews:
                    review.status = "resolved"
                    review.resolution_note = "所有问题台词的说话人均已确认。"
                    review.resolved_at = review.updated_at = utc_now()
            names = {unit.speaker_id: speaker_name} if unit.speaker_id else {}
            voices = resolve_unit_voices(session, [unit])
            session.commit()
            return self._unit_out(
                unit, names.get(unit.speaker_id), voices.get(unit.id)
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
                items=[
                    self._version_out(
                        row,
                        int(counts.get(row.id, 0)),
                        blocking=has_open_blocking(
                            session,
                            row.book_id,
                            "script",
                            stage_review_scope(session, chapter, "script", row)[1],
                        ),
                    )
                    for row in rows
                ],
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
            conditions = stage_review_scope(session, chapter, "script", row)[1]
            blocking = has_open_blocking(session, row.book_id, "script", conditions)
            if approve:
                row.status = "approved"
                row.approved_at = utc_now()
            else:
                row.status = effective_version_status(row.status, blocking=blocking)
            row.updated_at = utc_now()
            chapter.script_status = effective_stage_status(
                chapter.script_status,
                blocking=blocking,
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

    def _retry_scene_prerequisite(self, job_id: str) -> bool:
        """手动重试时分场与剧本串行执行；返回 False 表示父剧本任务已取消。"""
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return False
            params = dict(job.params_json or {})
            if not params.get("retry_scenes"):
                return True
            chapter = session.get(Chapter, job.target_id or "")
            if chapter is None:
                raise NotFoundError("章节不存在")
            if self._scene_rows(session, chapter):
                return True
        scene_service = SceneService(self.settings, self.session_factory, self.registry)
        child = scene_service.start_scene_analysis(chapter.id)
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                try:
                    scene_service.cancel_scene_analysis(chapter.id)
                except ConflictError:
                    pass
                return False
            params = dict(job.params_json or {})
            params["scene_prerequisite_job_id"] = child.id
            job.params_json = params
            job.result_json = {
                **dict(job.result_json or {}),
                "stage": "scenes",
                "scene_job_id": child.id,
            }
        while True:
            time.sleep(0.1)
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    try:
                        scene_service.cancel_scene_analysis(chapter.id)
                    except ConflictError:
                        pass
                    return False
                scene_job = session.get(GenerationJob, child.id)
                if scene_job is None:
                    raise AppError("分场重试任务丢失", status_code=502, code="scenes_required")
                if scene_job.status not in ACTIVE_JOB_STATUSES:
                    break
                job.result_json = {
                    **dict(job.result_json or {}),
                    "stage": "scenes",
                    "scene_progress": scene_job.progress,
                }
        if scene_job.status != "completed":
            reason = scene_job.error_message or "本章分场重试失败"
            raise AppError(reason, status_code=502, code="scene_retry_failed")
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is not None:
                job.result_json = {**dict(job.result_json or {}), "stage": "script"}
        return True

    def _run_job(self, job_id: str) -> None:
        started = _iso_now()
        started_perf = time.perf_counter()
        try:
            if not self._retry_scene_prerequisite(job_id):
                return
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
                characters, known = self._load_characters(
                    session, book.id, "".join(chunk.text for chunk in chunks)
                )
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
            last_failure: str | None = None
            chunks_by_scene: dict[str, list[Any]] = {}
            for item in chunks:
                chunks_by_scene.setdefault(item.scene.id, []).append(item)

            for chunk in chunks:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                scene_chunks = chunks_by_scene.get(chunk.scene.id, [])
                chunk_position = next(
                    (position for position, item in enumerate(scene_chunks) if item.index == chunk.index),
                    0,
                )
                previous_chunk = scene_chunks[chunk_position - 1] if chunk_position > 0 else None
                next_chunk = scene_chunks[chunk_position + 1] if chunk_position + 1 < len(scene_chunks) else None
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
                    "adjacent_source": {
                        "before": previous_chunk.text[-500:] if previous_chunk else "",
                        "after": next_chunk.text[:500] if next_chunk else "",
                    },
                }
                result: ChapterScriptResult | None = None
                failure_reason: str | None = None
                for attempt in range(2):
                    try:
                        result = self.registry.llm.adapt_scene_script(
                            chunk.text, known, context
                        )
                        failure_reason = None
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        result = None
                        failure_reason = (
                            f"{type(exc).__name__}: "
                            f"{_clean_text(getattr(exc, 'message', str(exc)), 300)}"
                        )
                        if attempt == 0:
                            logger.warning("剧本分块 %s/%s 首次调用失败，自动重试：%s", chunk.index, chunk.total, failure_reason)
                            time.sleep(0.25)
                if result is not None and not result.units:
                    failure_reason = "模型未返回剧本单元"
                if result is not None and result.units:
                    outcome = self._plan_chunk(
                        result,
                        chunk,
                        chapter.id,
                        canonical,
                        alias_index,
                        start_index=len(all_units),
                    )
                    source_chars = len(chunk.text.strip())
                    if (
                        not outcome.units
                        or outcome.aligned_chars < max(1, int(source_chars * 0.5))
                    ):
                        failure_reason = (
                            "模型剧本原文对齐率不足"
                            f"（{outcome.aligned_chars}/{source_chars} 字符），已回退"
                        )
                        last_failure = failure_reason
                        result = None
                    else:
                        valid_chunks += 1
                        calls.append(
                            {
                                "scene": chunk.scene.scene_index,
                                "chunk": chunk.index,
                                "call": dict(result.meta or {}),
                                "aligned_chars": outcome.aligned_chars,
                                "source_chars": source_chars,
                            }
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
                        chunk,
                        chapter.id,
                        canonical,
                        alias_index,
                        {
                            str(state.get("character_id") or ""): state
                            for state in chunk.scene.states
                            if state.get("character_id")
                        },
                        start_index=len(all_units),
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
                reason = last_failure or (
                    str(last_error) if last_error is not None else "模型未返回可用的剧本结果"
                )
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
        aligned = 0
        states_by_character = {
            str(state.get("character_id") or ""): state
            for state in chunk.scene.states
            if state.get("character_id")
        }
        match_index = _source_match_index(chunk.text)
        for draft in result.units:
            quote = (draft.source_text or "").strip()
            if not quote:
                invalid += 1
                continue
            span = _find_source_span(quote, chunk.text, cursor, match_index)
            if span is None:
                invalid += 1
                continue
            local, source_end = span
            if local > cursor:
                gap = chunk.text[cursor:local]
                if gap.strip():
                    fill_units = self._fill_gap(
                        chunk,
                        cursor,
                        local,
                        chapter_id,
                        canonical,
                        aliases,
                        states_by_character,
                        start_index=start_index + len(units),
                    )
                    units.extend(fill_units)
                    filled += len(gap.strip())
            source_text = chunk.text[local:source_end]
            source_start = chunk.abs_start + local
            context_text = chunk.text[
                max(0, local - 220) : min(len(chunk.text), source_end + 320)
            ]
            piece_cursor = 0
            for piece_draft in self._expand_source_draft(
                draft, source_text, context_text, canonical, aliases
            ):
                piece = piece_draft.source_text or ""
                piece_local = source_text.find(piece, piece_cursor)
                if piece_local < 0:
                    invalid += 1
                    continue
                piece_start = source_start + piece_local
                plan = self._plan_unit(
                    piece_draft,
                    chunk,
                    piece,
                    piece_start,
                    piece_start + len(piece),
                    chapter_id,
                    canonical,
                    aliases,
                    states_by_character,
                )
                if plan is not None:
                    plan.meta["unit_index"] = start_index + len(units)
                    units.append(plan)
                    aligned += len(piece.strip())
                piece_cursor = piece_local + len(piece)
            cursor = source_end
        if cursor < len(chunk.text):
            tail = chunk.text[cursor:]
            if tail.strip():
                fill_units = self._fill_gap(
                    chunk,
                    cursor,
                    len(chunk.text),
                    chapter_id,
                    canonical,
                    aliases,
                    states_by_character,
                    start_index=start_index + len(units),
                )
                units.extend(fill_units)
                filled += len(tail.strip())
        self._inherit_performance(units)
        return _ChunkOutcome(
            units=units,
            reviews=reviews,
            calls=[],
            notes=[],
            invalid_quotes=invalid,
            filled_chars=filled,
            aligned_chars=aligned,
        )

    def _unquoted_dialogue(
        self,
        source_text: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
    ) -> tuple[Character, str] | None:
        """只识别带明确说话动作的无引号对白，避免把普通叙述误判为台词。"""
        if _quote_spans(source_text):
            return None
        names: dict[str, Character] = {}
        for rows in [*aliases.values(), *canonical.values()]:
            unique = {row.id: row for row in rows}
            if len(unique) == 1:
                row = next(iter(unique.values()))
                names[row.canonical_name] = row
        for name in sorted(names, key=len, reverse=True):
            escaped = re.escape(name)
            prefix = re.search(
                rf"(?:^|[。！？!?]\s*){escaped}[^，。！？!?]{{0,10}}"
                rf"(?:说|问|答|喊|叫|道)[：:，]\s*(?P<speech>[^。！？!?]+[。！？!?]?)",
                source_text,
            )
            if (
                prefix
                and any(char.isalnum() for char in prefix.group("speech"))
            ):
                return names[name], prefix.group("speech").strip()
            suffix = re.search(
                rf"(?P<speech>[^。！？!?]+?)[，,]?\s*{escaped}"
                rf"[^，。！？!?]{{0,8}}(?:说|问|答|喊|叫|道)[。！？!?]",
                source_text,
            )
            if (
                suffix
                and any(char.isalnum() for char in suffix.group("speech"))
                and not suffix.group("speech").lstrip().startswith(("”", "」", "』", "’"))
            ):
                return names[name], suffix.group("speech").strip()
        return None

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
        if unit_type == "dialogue" and _has_only_narrative_quotes(source_text):
            unit_type = "narration"
        is_cue = unit_type in SCRIPT_CUE_TYPES
        unquoted = None
        if unit_type == "narration":
            unquoted = self._unquoted_dialogue(source_text, canonical, aliases)
            if unquoted is not None:
                unit_type = "dialogue"
                is_cue = False
        raw_text = (draft.text or "").strip()
        if unquoted is not None:
            raw_text = unquoted[1]
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
        speaker_candidate = _clean_name(
            unquoted[0].canonical_name if unquoted is not None else draft.speaker_name or ""
        )
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
            confidence=max(_clamp(draft.confidence), 0.8 if unquoted is not None else 0.0),
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
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
        states_by_character: dict[str, dict[str, Any]],
        *,
        start_index: int,
    ) -> list[_UnitPlan]:
        gap_text = chunk.text[start:end]
        if not gap_text.strip():
            return []
        context_text = chunk.text[
            max(0, start - 220) : min(len(chunk.text), end + 320)
        ]
        units: list[_UnitPlan] = []
        for local_start, local_end, inner in _atomic_source_segments(gap_text):
            content = gap_text[local_start:local_end]
            lead = len(content) - len(content.lstrip())
            trail = len(content.rstrip())
            content = content[lead:trail]
            if not content:
                continue
            local_start += lead
            local_end = local_start + len(content)
            speaker_name = None
            if inner is not None:
                unit_type = "dialogue"
                speaker, _reason = self._infer_dialogue_speaker(
                    gap_text,
                    local_start,
                    local_end,
                    context_text,
                    canonical,
                    aliases,
                )
                if speaker is not None:
                    speaker_name = speaker.canonical_name
                text = " ".join(inner.split())
                confidence = 0.85 if speaker is not None else 0.55
            else:
                unit_type = _candidate_type(content)
                text = content
                confidence = 0.55
            draft = ScriptUnitDraft(
                unit_type=unit_type,
                source_text=content,
                text=text,
                display_text=content,
                speaker_type=(
                    "character"
                    if inner is not None and speaker_name
                    else "unknown"
                    if inner is not None
                    else "narrator"
                ),
                speaker_name=speaker_name,
                confidence=confidence,
            )
            plan = self._plan_unit(
                draft,
                chunk,
                content,
                chunk.abs_start + start + local_start,
                chunk.abs_start + start + local_end,
                chapter_id,
                canonical,
                aliases,
                states_by_character,
            )
            if plan is not None:
                plan.source = "system"
                plan.meta = {
                    **(plan.meta or {}),
                    "fill": True,
                    "unit_index": start_index + len(units),
                }
                units.append(plan)
        self._inherit_performance(units)
        return units

    def _fallback_chunk(
        self,
        chunk: _Chunk,
        chapter_id: str,
        canonical: dict[str, list[Character]],
        aliases: dict[str, list[Character]],
        states_by_character: dict[str, dict[str, Any]],
        *,
        start_index: int,
    ) -> _ChunkOutcome:
        units = self._fill_gap(
            chunk,
            0,
            len(chunk.text),
            chapter_id,
            canonical,
            aliases,
            states_by_character,
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
                review.severity == "blocking"
                for review in chapter_reviews
            )
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

    def _version_out(
        self,
        row: ScriptVersion,
        unit_count: int,
        *,
        blocking: bool = False,
    ) -> ScriptVersionOut:
        return ScriptVersionOut(
            id=row.id,
            book_id=row.book_id,
            chapter_id=row.chapter_id,
            version=row.version,
            status=effective_version_status(row.status, blocking=blocking),
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
        *,
        blocking: bool = False,
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
            review_status=effective_unit_status(
                row.review_status,
                blocking=blocking,
            ),
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
            policy = policy_for(review.code)
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
                category=policy.category,
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