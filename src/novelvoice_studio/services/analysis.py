"""阶段 2：逐章 AI 分析、审核数据持久化与后台任务编排。"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.base import (
    CharacterAnalysisResult,
    SegmentAnalysisResult,
    SegmentDraft,
)
from novelvoice_studio.ai.prompts import (
    CHARACTER_PROMPT_VERSION,
    SEGMENT_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
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
    Chapter,
    Character,
    CharacterRelationship,
    GenerationJob,
    Segment,
)
from novelvoice_studio.schemas.analysis import (
    AnalysisChapterStatus,
    AnalysisJobOut,
    AnalysisStatusResponse,
    ChapterAnalysisResponse,
)
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.character_ops import normalize_aliases
from novelvoice_studio.services.chapters import ParagraphSlice, split_paragraphs
from novelvoice_studio.services.paths import new_id, validate_id

logger = logging.getLogger(__name__)

JOB_TYPE = "chapter_analysis"
ACTIVE_JOB_STATUSES = ("pending", "running")
FINISHED_CHAPTER_STATUSES = ("completed", "needs_review")
_QUOTE_MARKERS = ("「", "“", "『", '"')
_ALIAS_BASES = {"explicit", "inferred", "unknown"}


@dataclass(frozen=True)
class _ChapterContext:
    book_id: str
    book_title: str
    chapter_id: str
    chapter_title: str
    sequence: int
    chapter_start: int
    text: str
    paragraphs: list[ParagraphSlice]
    previous_summary: str | None
    started_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _clean_name(value: str, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_text(value: str | None, limit: int = 2000) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:limit] if cleaned else None


def _evidence_dicts(items: Any, chapter_id: str, limit: int = 12) -> list[dict[str, Any]]:
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
    existing: Any, items: Any, chapter_id: str, limit: int = 30
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


def _alias_key_set(character: Character) -> set[str]:
    names = {_norm(character.canonical_name)}
    names.update(_norm(item["name"]) for item in normalize_aliases(character.aliases_json))
    return names


def _registry_payload(
    characters: list[Character], text: str | None = None, limit: int = 60
) -> list[dict[str, Any]]:
    selected = characters
    if text:
        mentioned: list[Character] = []
        remaining: list[Character] = []
        for character in characters:
            keys = _alias_key_set(character)
            if any(key and key in text.casefold() for key in keys):
                mentioned.append(character)
            else:
                remaining.append(character)
        selected = [*mentioned, *remaining]
    payload: list[dict[str, Any]] = []
    for character in selected[:limit]:
        payload.append(
            {
                "name": character.canonical_name,
                "aliases": [
                    item["name"] for item in normalize_aliases(character.aliases_json)
                ],
                "gender": character.gender,
                "age_range": character.age_range,
                "character_type": character.character_type,
                "identity": _clean_text(character.identity_description, 80),
                "personality": [
                    str(item) for item in (character.personality_json or [])[:5]
                ],
                "voice": _clean_text(character.voice_description, 100),
            }
        )
    return payload


def _speaker_maps(
    characters: list[Character],
) -> tuple[dict[str, Character], set[str]]:
    """返回可唯一解析的姓名/别名映射，以及存在歧义的键。"""
    mapping: dict[str, Character] = {}
    ambiguous: set[str] = set()

    def add(name: str, character: Character) -> None:
        key = _norm(name)
        if not key:
            return
        existing = mapping.get(key)
        if existing is not None and existing.id != character.id:
            ambiguous.add(key)
            return
        mapping[key] = character

    for character in characters:
        add(character.canonical_name, character)
    for character in characters:
        for item in normalize_aliases(character.aliases_json):
            add(item["name"], character)
    return mapping, ambiguous


def _collect_history(meta: Any) -> list[dict[str, Any]]:
    if not isinstance(meta, dict):
        return []
    history = meta.get("history")
    history = list(history) if isinstance(history, list) else []
    if meta.get("status") != "running" and (meta.get("model") or meta.get("finished_at")):
        previous = {key: value for key, value in meta.items() if key != "history"}
        history.append(previous)
    return history[-5:]


class AnalysisService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        registry: ProviderRegistry,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry
        self.speaker_threshold = settings.analysis.speaker_confidence_threshold
        self.emotion_threshold = settings.analysis.emotion_confidence_threshold
        self.alias_threshold = settings.analysis.alias_confidence_threshold

    # --------------------------------------------------------------- helpers

    def _read_context(self, session: Session, chapter_id: str) -> tuple[_ChapterContext, Chapter]:
        validate_id(chapter_id, "ch")
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            raise NotFoundError("章节不存在")
        if not chapter.analysis_included:
            raise ValidationError(
                "该章节当前未纳入分析；如确需纳入，请先在章节设置中开启。",
                code="chapter_excluded",
            )
        book = session.get(Book, chapter.book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("章节所属书籍不存在或已在回收站")
        source = BookService(self.settings, session).read_source(book)
        start, end = chapter.source_start_offset, chapter.source_end_offset
        if start < 0 or end <= start or end > len(source):
            raise AppError(
                "章节偏移与源文本不一致，可能文件已被修改",
                status_code=500,
                code="source_offset_mismatch",
            )
        text = source[start:end]
        if not text.strip():
            raise ValidationError("章节没有正文，无法分析", code="chapter_empty")
        limit = self.settings.analysis.max_chapter_chars
        if len(text) > limit:
            raise ValidationError(
                f"章节长度 {len(text)} 字，超过单次分析上限 {limit} 字；"
                "请先在章节视图拆分本章，或调大 analysis.max_chapter_chars。",
                code="chapter_too_long",
                details={"length": len(text), "limit": limit},
            )
        paragraphs = split_paragraphs(source, start, end)
        if not paragraphs:
            raise ValidationError("章节没有可用段落，无法分析", code="chapter_empty")
        previous_summary = session.scalar(
            select(Chapter.summary)
            .where(
                Chapter.book_id == book.id,
                Chapter.analysis_included.is_(True),
                Chapter.sequence < chapter.sequence,
                Chapter.summary.is_not(None),
                Chapter.analysis_status.in_(FINISHED_CHAPTER_STATUSES),
            )
            .order_by(Chapter.sequence.desc())
            .limit(1)
        )
        return (
            _ChapterContext(
                book_id=book.id,
                book_title=book.title,
                chapter_id=chapter.id,
                chapter_title=chapter.title,
                sequence=chapter.sequence,
                chapter_start=start,
                text=text,
                paragraphs=paragraphs,
                previous_summary=previous_summary,
                started_at=_iso_now(),
            ),
            chapter,
        )

    def _prepare(self, chapter_id: str) -> _ChapterContext:
        with session_scope(self.session_factory) as session:
            context, chapter = self._read_context(session, chapter_id)
            if chapter.analysis_status == "running":
                raise ConflictError("该章节正在分析中")
            history = _collect_history(chapter.analysis_meta_json)
            chapter.analysis_status = "running"
            chapter.analysis_meta_json = {
                "status": "running",
                "provider": self.registry.llm.name,
                "model": self.registry.llm.model,
                "is_mock": self.registry.llm.is_mock,
                "prompt_versions": {
                    "characters": CHARACTER_PROMPT_VERSION,
                    "segments": SEGMENT_PROMPT_VERSION,
                    "summary": SUMMARY_PROMPT_VERSION,
                },
                "started_at": context.started_at,
                "history": history,
            }
            chapter.updated_at = utc_now()
            return context

    def _mark_failed(self, chapter_id: str, error: Exception) -> None:
        try:
            with session_scope(self.session_factory) as session:
                chapter = session.get(Chapter, chapter_id)
                if chapter is None:
                    return
                meta = dict(chapter.analysis_meta_json or {})
                history = _collect_history(meta)
                meta.update(
                    {
                        "status": "failed",
                        "finished_at": _iso_now(),
                        "history": history,
                        "error": {
                            "code": getattr(error, "code", "analysis_failed"),
                            "message": str(getattr(error, "message", error))[:1000],
                        },
                    }
                )
                chapter.analysis_meta_json = meta
                chapter.analysis_status = "failed"
                chapter.updated_at = utc_now()
        except Exception:  # noqa: BLE001
            logger.exception("更新章节 %s 失败状态时出错", chapter_id)

    def _active_characters(self, session: Session, book_id: str) -> list[Character]:
        return list(
            session.scalars(
                select(Character)
                .where(Character.book_id == book_id, Character.merged_into_id.is_(None))
                .order_by(Character.created_at.asc())
            )
        )

    # ------------------------------------------------------- character merge

    def _persist_characters(
        self,
        session: Session,
        context: _ChapterContext,
        result: CharacterAnalysisResult,
    ) -> dict[str, int]:
        characters = self._active_characters(session, context.book_id)
        canonical_map: dict[str, Character] = {}
        alias_map: dict[str, Character] = {}
        ambiguous_aliases: set[str] = set()
        for character in characters:
            key = _norm(character.canonical_name)
            canonical_map.setdefault(key, character)
            for item in normalize_aliases(character.aliases_json):
                alias_key = _norm(item["name"])
                if not alias_key:
                    continue
                existing = alias_map.get(alias_key)
                if existing is not None and existing.id != character.id:
                    ambiguous_aliases.add(alias_key)
                else:
                    alias_map[alias_key] = character

        def match(name: str) -> Character | None:
            key = _norm(name)
            if key in canonical_map:
                return canonical_map[key]
            if key in ambiguous_aliases:
                return None
            return alias_map.get(key)

        def offer_alias(
            character: Character, name: str, confidence: float, basis: str, evidence: Any
        ) -> None:
            cleaned = _clean_name(name)
            key = _norm(cleaned)
            if not cleaned or key == _norm(character.canonical_name):
                return
            aliases = normalize_aliases(character.aliases_json)
            if any(_norm(item["name"]) == key for item in aliases):
                return
            candidates = normalize_aliases(character.alias_candidates_json)
            conflict = None
            owner = canonical_map.get(key)
            if owner is None and key not in ambiguous_aliases:
                owner = alias_map.get(key)
            if owner is not None and owner.id != character.id:
                conflict = owner.id
            alias_basis = basis if basis in _ALIAS_BASES else "unknown"
            if conflict is None and confidence >= self.alias_threshold:
                aliases.append(
                    {
                        "name": cleaned,
                        "confidence": confidence,
                        "basis": alias_basis,
                        "chapter_id": context.chapter_id,
                        "evidence": _evidence_dicts(evidence, context.chapter_id, limit=4),
                        "source": "ai",
                    }
                )
                character.aliases_json = aliases
                alias_map[key] = character
                character.alias_candidates_json = [
                    item for item in candidates if _norm(item["name"]) != key
                ]
                return
            existing_candidate = next(
                (item for item in candidates if _norm(item["name"]) == key), None
            )
            if existing_candidate is not None:
                existing_candidate["confidence"] = max(
                    float(existing_candidate.get("confidence") or 0.0), confidence
                )
                existing_candidate["evidence"] = _append_evidence(
                    existing_candidate.get("evidence"),
                    _evidence_dicts(evidence, context.chapter_id, limit=4),
                    context.chapter_id,
                    limit=8,
                )
                if conflict:
                    existing_candidate["conflict_character_id"] = conflict
                    existing_candidate["reason"] = "该称谓可能属于另一个已登记角色，需人工确认是否合并"
            else:
                candidates.append(
                    {
                        "name": cleaned,
                        "confidence": confidence,
                        "basis": alias_basis,
                        "chapter_id": context.chapter_id,
                        "evidence": _evidence_dicts(evidence, context.chapter_id, limit=4),
                        "source": "ai",
                        "conflict_character_id": conflict,
                        "reason": (
                            "该称谓可能属于另一个已登记角色，需人工确认是否合并"
                            if conflict
                            else "置信度不足，需人工确认后加入别名"
                        ),
                    }
                )
            character.alias_candidates_json = candidates

        created_count = 0
        for draft in result.characters:
            name = _clean_name(draft.name)
            if not name:
                continue
            character = match(name)
            is_new = character is None
            if character is None:
                character = Character(
                    id=new_id("char"),
                    book_id=context.book_id,
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
                    is_provisional=True,
                    user_locked=False,
                    merge_log_json=[],
                    first_chapter_id=context.chapter_id,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(character)
                canonical_map[_norm(name)] = character
                created_count += 1

            if character.user_locked:
                continue

            # 若通过别名命中，把本章返回的主名称也作为别名候选/别名记录。
            if _norm(name) != _norm(character.canonical_name):
                offer_alias(
                    character,
                    name,
                    max(0.0, min(1.0, draft.confidence)),
                    draft.gender_basis,
                    draft.evidence,
                )
            for alias in draft.aliases:
                offer_alias(
                    character,
                    alias.name,
                    max(0.0, min(1.0, alias.confidence)),
                    alias.basis,
                    alias.evidence,
                )

            if character.gender == "unknown" and draft.gender != "unknown":
                character.gender = draft.gender
                character.gender_basis = draft.gender_basis
            if character.age_range == "unknown" and draft.age_range != "unknown":
                character.age_range = draft.age_range
                character.age_basis = draft.age_basis
            if character.character_type == "unknown" and draft.character_type != "unknown":
                character.character_type = draft.character_type

            # 高置信的新章节资料可以刷新 AI 描述，修正早期低置信度造成的偏差；
            # 用户锁定的角色已在上面跳过，人工资料不会被覆盖。
            draft_confidence = max(0.0, min(1.0, float(draft.confidence or 0.0)))
            refresh_profile = draft_confidence >= self.speaker_threshold
            for field_name, raw_value in (
                ("identity_description", draft.identity_description),
                ("personality_desc", draft.personality_desc),
                ("appearance_desc", draft.appearance_desc),
                ("voice_description", draft.voice_description),
            ):
                cleaned = _clean_text(raw_value, 1000)
                if not cleaned:
                    continue
                if refresh_profile or not getattr(character, field_name):
                    setattr(character, field_name, cleaned)

            personality = [str(item) for item in (character.personality_json or [])]
            seen_tags = {item.casefold() for item in personality}
            for tag in draft.personality:
                cleaned = _clean_name(tag, 60)
                if cleaned and cleaned.casefold() not in seen_tags:
                    seen_tags.add(cleaned.casefold())
                    personality.append(cleaned)
            character.personality_json = personality[:20]

            character.evidence_json = _append_evidence(
                character.evidence_json, draft.evidence, context.chapter_id
            )
            character.confidence = max(
                float(character.confidence or 0.0), float(draft.confidence or 0.0)
            )
            if not draft.is_provisional and draft.confidence >= self.speaker_threshold:
                character.is_provisional = False
            if is_new:
                character.is_provisional = draft.is_provisional or draft.confidence < 0.5
            if not character.first_chapter_id:
                character.first_chapter_id = context.chapter_id
            character.updated_at = utc_now()

        session.flush()

        # 新角色写入别名映射
        for character in self._active_characters(session, context.book_id):
            canonical_map.setdefault(_norm(character.canonical_name), character)
            for item in normalize_aliases(character.aliases_json):
                key = _norm(item["name"])
                if key and key not in ambiguous_aliases and key not in alias_map:
                    alias_map[key] = character

        relationships = {
            (item.source_character_id, item.target_character_id): item
            for item in session.scalars(
                select(CharacterRelationship).where(
                    CharacterRelationship.book_id == context.book_id
                )
            )
        }
        relationships_added = 0
        for draft in result.relationships:
            source = match(draft.source_name)
            target = match(draft.target_name)
            if source is None or target is None or source.id == target.id:
                continue
            relationship_type = _clean_text(draft.relationship_type, 64) or "unknown"
            evidence = _evidence_dicts(draft.evidence, context.chapter_id)
            key = (source.id, target.id)
            existing = relationships.get(key)
            reversed_match = False
            if existing is None:
                reverse = relationships.get((target.id, source.id))
                # 模型经常把同一条关系正反各报一次；同类型（或一方未定）时合并为一条，
                # 保留首次出现的方向。方向语义不同的关系（如师徒）仍分别保留。
                if reverse is not None and (
                    relationship_type == "unknown"
                    or reverse.relationship_type == "unknown"
                    or reverse.relationship_type == relationship_type
                ):
                    existing = reverse
                    reversed_match = True
            if existing is not None:
                previous_confidence = float(existing.confidence or 0.0)
                new_confidence = max(0.0, min(1.0, float(draft.confidence or 0.0)))
                existing.evidence_json = _append_evidence(
                    existing.evidence_json, evidence, context.chapter_id
                )
                existing.confidence = max(previous_confidence, new_confidence)
                # 反向条目可能把主客体对调，描述未必可逆，只在缺失时补全。
                if not existing.description or (
                    not reversed_match and new_confidence >= previous_confidence
                ):
                    existing.description = _clean_text(draft.description, 1000)
                if existing.relationship_type == "unknown" and relationship_type != "unknown":
                    existing.relationship_type = relationship_type
                if not draft.is_provisional:
                    existing.is_provisional = False
                existing.updated_at = utc_now()
                continue
            relationship = CharacterRelationship(
                id=new_id("rel"),
                book_id=context.book_id,
                source_character_id=source.id,
                target_character_id=target.id,
                relationship_type=relationship_type,
                description=_clean_text(draft.description, 1000),
                evidence_json=evidence,
                confidence=max(0.0, min(1.0, draft.confidence)),
                is_provisional=(
                    draft.is_provisional or draft.confidence < self.speaker_threshold
                ),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(relationship)
            relationships[key] = relationship
            relationships_added += 1

        return {
            "characters_added": created_count,
            "relationships_added": relationships_added,
        }

    # --------------------------------------------------------- segment merge

    def _paragraph_for_span(
        self, context: _ChapterContext, start: int, end: int
    ) -> ParagraphSlice | None:
        for paragraph in context.paragraphs:
            if paragraph.start <= start < end <= paragraph.end:
                return paragraph
        return None

    def _locked_rows(self, session: Session, chapter_id: str) -> list[Segment]:
        rows = list(
            session.scalars(
                select(Segment)
                .where(Segment.chapter_id == chapter_id)
                .order_by(Segment.segment_index.asc())
            )
        )
        return [item for item in rows if item.user_locked_fields_json]

    def _gap_piece(
        self, context: _ChapterContext, start: int, end: int
    ) -> dict[str, Any] | None:
        raw = context.text[start - context.chapter_start : end - context.chapter_start]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        content = raw.strip()
        if not content:
            return None
        start += leading
        end -= trailing
        first = content[0]
        has_quote = any(marker in content for marker in _QUOTE_MARKERS)
        if first in _QUOTE_MARKERS:
            segment_type = "dialogue"
        elif has_quote:
            segment_type = "unknown"
        else:
            segment_type = "narration"
        needs_review = True  # 保底片段未被 AI 结果覆盖，原文边界与说话人只能推断。
        return {
            "source_text": content,
            "segment_type": segment_type,
            "speaker_type": (
                "unknown" if segment_type in {"dialogue", "unknown"} else "narrator"
            ),
            "speaker_id": None,
            "speaker_name_snapshot": None,
            "speaker_confidence": (
                0.0 if segment_type in {"dialogue", "unknown"} else 1.0
            ),
            "emotion": "neutral",
            "emotion_confidence": 0.1,
            "intensity": 1,
            "delivery": "normal",
            "tts_instruction": None,
            "review_status": "needs_review" if needs_review else "auto",
            "evidence_json": [
                {
                    "chapter_id": context.chapter_id,
                    "quote": content[:200],
                    "reason": "AI 分段未覆盖这段原文，系统已回退为原文片段，需人工复核。",
                }
            ],
            "source_start_offset": start,
            "source_end_offset": end,
            "fallback": True,
        }

    def _draft_piece(
        self,
        context: _ChapterContext,
        draft: SegmentDraft,
        start: int,
        end: int,
        speaker_map: dict[str, Character],
        ambiguous: set[str],
        notes: list[str],
    ) -> dict[str, Any]:
        source_text = context.text[
            start - context.chapter_start : end - context.chapter_start
        ]
        segment_type = draft.segment_type
        speaker_type = "narrator"
        speaker_id: str | None = None
        speaker_name: str | None = None
        speaker_confidence = 1.0
        needs_review = False
        reason = None

        if segment_type in {"narration", "inner_monologue"}:
            if draft.speaker_type == "character":
                notes.append("叙述或内心独白被模型分配给角色，已按旁白处理。")
        else:
            if segment_type == "unknown":
                needs_review = True
                reason = "片段类型无法确定"
            requested = _clean_name(draft.speaker_name or "")
            key = _norm(requested)
            if draft.speaker_type == "character" and requested and key not in ambiguous:
                character = speaker_map.get(key)
                if character is not None:
                    speaker_type = "character"
                    speaker_id = character.id
                    speaker_name = character.canonical_name
                    speaker_confidence = max(
                        0.0, min(1.0, float(draft.speaker_confidence or 0.0))
                    )
                    if speaker_confidence < self.speaker_threshold:
                        needs_review = True
                        reason = "说话人置信度不足"
                else:
                    speaker_type = "unknown"
                    speaker_confidence = min(
                        0.3, max(0.0, float(draft.speaker_confidence or 0.0))
                    )
                    needs_review = True
                    reason = "说话人不在已登记角色表中"
                    notes.append(f"未登记的说话人：{requested}")
            elif segment_type == "dialogue":
                speaker_type = "unknown"
                speaker_confidence = min(
                    0.3, max(0.0, float(draft.speaker_confidence or 0.0))
                )
                needs_review = True
                reason = "无法可靠判断直接对白的说话人"
            else:
                speaker_confidence = 0.0

        evidence = _evidence_dicts(draft.evidence, context.chapter_id)
        emotion_confidence = max(
            0.0, min(1.0, float(draft.emotion_confidence or 0.0))
        )
        if emotion_confidence < self.emotion_threshold:
            needs_review = True
            reason = f"{reason}；情绪置信度不足" if reason else "情绪置信度不足"
        if reason:
            evidence.append(
                {
                    "chapter_id": context.chapter_id,
                    "quote": source_text[:200],
                    "reason": f"{reason}，需人工确认。",
                }
            )
        return {
            "source_text": source_text,
            "segment_type": segment_type,
            "speaker_type": speaker_type,
            "speaker_id": speaker_id,
            "speaker_name_snapshot": speaker_name,
            "speaker_confidence": speaker_confidence,
            "emotion": draft.emotion,
            "emotion_confidence": emotion_confidence,
            "intensity": draft.intensity,
            "delivery": draft.delivery,
            "tts_instruction": _clean_text(draft.tts_instruction, 500),
            "review_status": "needs_review" if needs_review else "auto",
            "evidence_json": evidence[:20],
            "source_start_offset": start,
            "source_end_offset": end,
            "fallback": False,
        }

    def _new_segment(self, context: _ChapterContext, payload: dict[str, Any]) -> Segment:
        return Segment(
            id=new_id("seg"),
            book_id=context.book_id,
            chapter_id=context.chapter_id,
            segment_index=-100000,
            source_text=payload["source_text"],
            speech_text=payload["source_text"],
            segment_type=payload["segment_type"],
            speaker_type=payload["speaker_type"],
            speaker_id=payload["speaker_id"],
            speaker_name_snapshot=payload["speaker_name_snapshot"],
            speaker_confidence=payload["speaker_confidence"],
            emotion=payload["emotion"],
            emotion_confidence=payload["emotion_confidence"],
            intensity=payload["intensity"],
            delivery=payload["delivery"],
            tts_instruction=payload["tts_instruction"],
            speed=1.0,
            pause_before_ms=0,
            pause_after_ms=250,
            review_status=payload["review_status"],
            audio_status="none",
            user_locked_fields_json=[],
            text_version=1,
            source_start_offset=payload["source_start_offset"],
            source_end_offset=payload["source_end_offset"],
            evidence_json=payload["evidence_json"],
            created_at=utc_now(),
            updated_at=utc_now(),
        )

    def _persist_segments(
        self,
        session: Session,
        context: _ChapterContext,
        result: SegmentAnalysisResult,
    ) -> dict[str, Any]:
        existing = list(
            session.scalars(
                select(Segment)
                .where(Segment.chapter_id == context.chapter_id)
                .order_by(Segment.segment_index.asc())
            )
        )
        locked = [item for item in existing if item.user_locked_fields_json]
        locked_by_paragraph: dict[int, list[Segment]] = {}
        locked_ids: set[str] = set()
        for row in locked:
            if row.source_start_offset is None or row.source_end_offset is None:
                raise ConflictError(
                    "存在已锁定的片段缺少原文偏移，无法安全重跑；请导出数据后联系维护者。"
                )
            paragraph = self._paragraph_for_span(
                context, row.source_start_offset, row.source_end_offset
            )
            if paragraph is None:
                raise ConflictError(
                    "已锁定片段与当前段落边界不一致，无法安全重跑；请先解锁或拆分相关段落。"
                )
            expected = context.text[
                row.source_start_offset
                - context.chapter_start : row.source_end_offset
                - context.chapter_start
            ]
            if expected != row.source_text:
                raise ConflictError(
                    "已锁定片段的原文已变化，无法安全重跑；请检查源文件或先解锁相关片段。"
                )
            locked_by_paragraph.setdefault(paragraph.index, []).append(row)
            locked_ids.add(row.id)

        for items in locked_by_paragraph.values():
            items.sort(key=lambda item: int(item.source_start_offset or 0))
            for previous, current in zip(items, items[1:]):
                if int(current.source_start_offset or 0) < int(previous.source_end_offset or 0):
                    raise ConflictError("已锁定片段的原文区间互相重叠，无法安全重跑。")

        characters = self._active_characters(session, context.book_id)
        speaker_map, ambiguous = _speaker_maps(characters)
        drafts_by_paragraph: dict[int, list[SegmentDraft]] = {}
        for draft in result.segments:
            drafts_by_paragraph.setdefault(draft.paragraph_index, []).append(draft)

        used_drafts: set[tuple[int, int]] = set()
        notes: list[str] = []
        pieces: list[dict[str, Any] | Segment] = []
        fallback_count = 0

        for paragraph in context.paragraphs:
            paragraph_locks = locked_by_paragraph.get(paragraph.index, [])
            free_intervals: list[tuple[int, int]] = []
            cursor = paragraph.start
            for row in paragraph_locks:
                lock_start = int(row.source_start_offset or 0)
                lock_end = int(row.source_end_offset or 0)
                if cursor < lock_start:
                    free_intervals.append((cursor, lock_start))
                cursor = max(cursor, lock_end)
            if cursor < paragraph.end:
                free_intervals.append((cursor, paragraph.end))

            drafts = drafts_by_paragraph.get(paragraph.index, [])
            for free_start, free_end in free_intervals:
                interval_cursor = free_start
                for index, draft in enumerate(drafts):
                    marker = (paragraph.index, index)
                    if marker in used_drafts:
                        continue
                    needle = draft.source_text.strip()
                    if not needle:
                        continue
                    relative = paragraph.text.find(
                        needle,
                        interval_cursor - paragraph.start,
                        free_end - paragraph.start,
                    )
                    if relative < 0:
                        continue
                    absolute = paragraph.start + relative
                    gap = self._gap_piece(context, interval_cursor, absolute)
                    if gap is not None:
                        pieces.append(gap)
                        fallback_count += 1
                    end = absolute + len(needle)
                    pieces.append(
                        self._draft_piece(
                            context,
                            draft,
                            absolute,
                            end,
                            speaker_map,
                            ambiguous,
                            notes,
                        )
                    )
                    used_drafts.add(marker)
                    interval_cursor = end
                tail = self._gap_piece(context, interval_cursor, free_end)
                if tail is not None:
                    pieces.append(tail)
                    fallback_count += 1

        total_drafts = sum(len(items) for items in drafts_by_paragraph.values())
        unmatched = total_drafts - len(used_drafts)
        if unmatched > 0:
            notes.append(
                f"{unmatched} 个 AI 片段无法与原文逐字对齐，已丢弃并由原文回退片段覆盖。"
            )
        unknown_paragraphs = sorted(
            key for key in drafts_by_paragraph if key < 0 or key >= len(context.paragraphs)
        )
        if unknown_paragraphs:
            notes.append("部分 AI 片段返回了无效段落编号，已忽略。")

        # 删除未锁定旧片段；锁定片段保留原 ID 和全部人工字段。
        for index, row in enumerate(locked):
            row.segment_index = -(index + 1)
        session.flush()
        for row in existing:
            if row.id not in locked_ids:
                session.delete(row)
        session.flush()

        created: list[Segment] = []
        for index, piece in enumerate(pieces):
            if isinstance(piece, Segment):
                continue
            segment = self._new_segment(context, piece)
            segment.segment_index = -(100000 + index)
            session.add(segment)
            created.append(segment)
        session.flush()

        combined: list[Segment] = [*locked, *created]
        combined.sort(
            key=lambda item: (
                int(item.source_start_offset or 0),
                int(item.source_end_offset or 0),
                item.id,
            )
        )
        for final_index, segment in enumerate(combined):
            session.execute(
                update(Segment)
                .where(Segment.id == segment.id)
                .values(segment_index=final_index)
            )
        session.flush()

        return {
            "segments_written": len(combined),
            "fallback_segments": fallback_count,
            "unresolved_speakers": sum(
                1 for item in created if item.speaker_type == "unknown"
            ),
            "notes": notes,
        }

    # ------------------------------------------------------------ chapter run

    def _chapter_status(self, session: Session, chapter: Chapter) -> AnalysisChapterStatus:
        total = int(
            session.scalar(
                select(func.count(Segment.id)).where(Segment.chapter_id == chapter.id)
            )
            or 0
        )
        review = int(
            session.scalar(
                select(func.count(Segment.id)).where(
                    Segment.chapter_id == chapter.id,
                    Segment.review_status == "needs_review",
                )
            )
            or 0
        )
        meta = chapter.analysis_meta_json or {}
        error = meta.get("error") if isinstance(meta, dict) else None
        error_message = error.get("message") if isinstance(error, dict) else None
        return AnalysisChapterStatus(
            id=chapter.id,
            title=chapter.title,
            sequence=chapter.sequence,
            volume_number=chapter.volume_number,
            volume_title=chapter.volume_title,
            chapter_kind=chapter.chapter_kind,
            analysis_included=chapter.analysis_included,
            analysis_status=chapter.analysis_status,
            has_summary=bool(chapter.summary),
            summary=chapter.summary,
            segment_count=total,
            review_count=review,
            error=error_message,
        )

    def analyze_chapter(self, chapter_id: str) -> ChapterAnalysisResponse:
        try:
            context = self._prepare(chapter_id)
        except AppError as exc:
            if getattr(exc, "code", "") in {"chapter_too_long", "chapter_empty"}:
                self._mark_failed(chapter_id, exc)
            raise
        stats: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            with session_scope(self.session_factory) as session:
                known = _registry_payload(
                    self._active_characters(session, context.book_id),
                    context.text,
                )
            character_result = self.registry.llm.analyze_characters(
                context.text,
                known,
                {
                    "book_title": context.book_title,
                    "chapter_title": context.chapter_title,
                    "chapter_sequence": context.sequence,
                    "previous_summary": context.previous_summary,
                },
            )
            with session_scope(self.session_factory) as session:
                stats.update(self._persist_characters(session, context, character_result))
                known = _registry_payload(
                    self._active_characters(session, context.book_id),
                    context.text,
                )

            segment_result = self.registry.llm.analyze_segments(
                context.text,
                known,
                {
                    "book_title": context.book_title,
                    "chapter_title": context.chapter_title,
                    "chapter_sequence": context.sequence,
                    "previous_summary": context.previous_summary,
                    "paragraphs": [
                        {"index": item.index, "text": item.text}
                        for item in context.paragraphs
                    ],
                },
            )
            with session_scope(self.session_factory) as session:
                stats.update(self._persist_segments(session, context, segment_result))
                known = _registry_payload(
                    self._active_characters(session, context.book_id),
                    context.text,
                )

            summary_result = self.registry.llm.summarize_chapter(context.text, known)
            duration_ms = int((time.perf_counter() - started) * 1000)
            with session_scope(self.session_factory) as session:
                chapter = session.get(Chapter, context.chapter_id)
                if chapter is None:
                    raise NotFoundError("章节在分析过程中被删除")
                total, review = (
                    int(
                        session.scalar(
                            select(func.count(Segment.id)).where(
                                Segment.chapter_id == chapter.id
                            )
                        )
                        or 0
                    ),
                    int(
                        session.scalar(
                            select(func.count(Segment.id)).where(
                                Segment.chapter_id == chapter.id,
                                Segment.review_status == "needs_review",
                            )
                        )
                        or 0
                    ),
                )
                final_status = "needs_review" if review else "completed"
                chapter.summary = summary_result.summary.strip()
                chapter.summary_json = {
                    "summary": summary_result.summary.strip(),
                    "key_events": summary_result.key_events,
                    "characters": summary_result.characters,
                    "meta": summary_result.meta,
                }
                meta = dict(chapter.analysis_meta_json or {})
                history = _collect_history(meta)
                chapter.analysis_meta_json = {
                    "status": final_status,
                    "provider": self.registry.llm.name,
                    "model": self.registry.llm.model,
                    "is_mock": self.registry.llm.is_mock,
                    "prompt_versions": {
                        "characters": CHARACTER_PROMPT_VERSION,
                        "segments": SEGMENT_PROMPT_VERSION,
                        "summary": SUMMARY_PROMPT_VERSION,
                    },
                    "started_at": context.started_at,
                    "finished_at": _iso_now(),
                    "duration_ms": duration_ms,
                    "chapter_chars": len(context.text),
                    "characters_added": stats.get("characters_added", 0),
                    "relationships_added": stats.get("relationships_added", 0),
                    "segments_written": total,
                    "review_segments": review,
                    "fallback_segments": stats.get("fallback_segments", 0),
                    "unresolved_speakers": stats.get("unresolved_speakers", 0),
                    "warnings": list(dict.fromkeys(stats.get("notes") or [])),
                    "calls": {
                        "characters": character_result.meta,
                        "segments": segment_result.meta,
                        "summary": summary_result.meta,
                    },
                    "history": history,
                }
                chapter.analysis_status = final_status
                chapter.updated_at = utc_now()
                status = self._chapter_status(session, chapter)
                return ChapterAnalysisResponse(
                    chapter=status,
                    characters_added=int(stats.get("characters_added", 0)),
                    relationships_added=int(stats.get("relationships_added", 0)),
                    segments_written=total,
                    meta=chapter.analysis_meta_json,
                )
        except Exception as exc:
            self._mark_failed(chapter_id, exc)
            raise

    # ------------------------------------------------------------ book job

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

    def _book(self, session: Session, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def assert_chapter_available(self, chapter_id: str) -> str:
        with session_scope(self.session_factory) as session:
            validate_id(chapter_id, "ch")
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise NotFoundError("章节不存在")
            if not chapter.analysis_included:
                raise ValidationError(
                    "该章节当前未纳入分析；如确需纳入，请先在章节设置中开启。",
                    code="chapter_excluded",
                )
            self._book(session, chapter.book_id)
            if self._active_job(session, chapter.book_id):
                raise ConflictError(
                    "本书正在执行全书分析，请等待完成或取消后再重跑单章。"
                )
            if chapter.analysis_status == "running":
                raise ConflictError("该章节正在分析中")
            return chapter.book_id

    def start_book_analysis(
        self, book_id: str, chapter_ids: list[str] | None = None
    ) -> AnalysisJobOut:
        requested = [validate_id(item, "ch") for item in (chapter_ids or [])]
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            active = self._active_job(session, book.id)
            if active is not None:
                raise ConflictError(
                    "本书已有正在运行的分析任务",
                    code="analysis_already_running",
                    details={"job_id": active.id, "status": active.status},
                )
            chapters = list(
                session.scalars(
                    select(Chapter)
                    .where(
                        Chapter.book_id == book.id,
                        Chapter.analysis_included.is_(True),
                    )
                    .order_by(Chapter.sequence.asc())
                )
            )
            if not chapters:
                raise ValidationError("书籍没有可分析的章节", code="book_has_no_chapters")
            if requested:
                by_id = {item.id: item for item in chapters}
                missing = [item for item in requested if item not in by_id]
                if missing:
                    raise ValidationError(
                        "部分章节不属于当前书籍",
                        details={"chapter_ids": missing},
                    )
                chapters = [by_id[item] for item in requested]
            if any(item.analysis_status == "running" for item in chapters):
                raise ConflictError("所选章节中有正在分析的任务")
            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=None,
                job_type=JOB_TYPE,
                status="pending",
                total_items=len(chapters),
                completed_items=0,
                failed_items=0,
                progress=0.0,
                attempt_count=0,
                max_attempts=1,
                params_json={"chapter_ids": [item.id for item in chapters]},
                result_json={},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            session.flush()
            output = to_job_out(job)

        thread = threading.Thread(
            target=self._run_job,
            args=(output.id,),
            name=f"novelvoice-analysis-{output.id}",
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
            raise AppError(
                "无法启动后台分析线程",
                status_code=500,
                code="analysis_thread_failed",
            ) from exc
        return output

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
                chapter_ids = [str(item) for item in (job.params_json or {}).get("chapter_ids", [])]
                total = len(chapter_ids)
                job.total_items = total
            success = 0
            for index, chapter_id in enumerate(chapter_ids):
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                try:
                    response = self.analyze_chapter(chapter_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("章节 %s 分析失败", chapter_id)
                    with session_scope(self.session_factory) as session:
                        job = session.get(GenerationJob, job_id)
                        if job is None:
                            return
                        job.status = "failed"
                        job.failed_items = 1
                        job.error_code = str(getattr(exc, "code", "analysis_failed"))
                        job.error_message = str(
                            getattr(exc, "message", exc)
                        )[:2000]
                        job.finished_at = utc_now()
                        job.updated_at = utc_now()
                        job.progress = min(1.0, (success + 1) / max(1, total))
                        job.result_json = {
                            **dict(job.result_json or {}),
                            "started_at": started,
                            "failed_chapter_id": chapter_id,
                        }
                    return
                success += 1
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None:
                        return
                    cancelled = job.status == "cancelled"
                    job.completed_items = success
                    job.progress = min(1.0, success / max(1, total))
                    job.updated_at = utc_now()
                    job.result_json = {
                        **dict(job.result_json or {}),
                        "last_chapter": {
                            "id": chapter_id,
                            "status": response.chapter.analysis_status,
                            "segments": response.segments_written,
                        },
                    }
                    if cancelled:
                        return

            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                review_chapters = int(
                    session.scalar(
                        select(func.count(Chapter.id)).where(
                            Chapter.book_id == job.book_id,
                            Chapter.analysis_status == "needs_review",
                        )
                    )
                    or 0
                )
                review_segments = int(
                    session.scalar(
                        select(func.count(Segment.id)).where(
                            Segment.book_id == job.book_id,
                            Segment.review_status == "needs_review",
                        )
                    )
                    or 0
                )
                characters = int(
                    session.scalar(
                        select(func.count(Character.id)).where(
                            Character.book_id == job.book_id,
                            Character.merged_into_id.is_(None),
                        )
                    )
                    or 0
                )
                job.status = "completed"
                job.progress = 1.0
                job.finished_at = utc_now()
                job.updated_at = utc_now()
                job.result_json = {
                    **dict(job.result_json or {}),
                    "started_at": started,
                    "finished_at": _iso_now(),
                    "review_chapters": review_chapters,
                    "review_segments": review_segments,
                    "characters": characters,
                }
        except Exception as exc:  # noqa: BLE001
            logger.exception("全书分析任务 %s 异常终止", job_id)
            try:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is not None and job.status != "cancelled":
                        job.status = "failed"
                        job.error_code = "analysis_runner_error"
                        job.error_message = str(exc)[:2000]
                        job.finished_at = utc_now()
                        job.updated_at = utc_now()
            except Exception:  # noqa: BLE001
                logger.exception("写入分析任务失败状态时出错")

    def analysis_status(self, book_id: str) -> AnalysisStatusResponse:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            chapters = list(
                session.scalars(
                    select(Chapter)
                    .where(Chapter.book_id == book.id)
                    .order_by(Chapter.sequence.asc())
                )
            )
            statuses = [self._chapter_status(session, item) for item in chapters]
            job = session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.book_id == book.id,
                    GenerationJob.job_type == JOB_TYPE,
                )
                .order_by(GenerationJob.created_at.desc())
                .limit(1)
            )
            total_segments = sum(item.segment_count for item in statuses)
            review_segments = sum(item.review_count for item in statuses)
            characters = int(
                session.scalar(
                    select(func.count(Character.id)).where(
                        Character.book_id == book.id,
                        Character.merged_into_id.is_(None),
                    )
                )
                or 0
            )
            return AnalysisStatusResponse(
                book_id=book.id,
                job=to_job_out(job) if job is not None else None,
                total_chapters=len(chapters),
                completed_chapters=sum(
                    1 for item in chapters if item.analysis_status in FINISHED_CHAPTER_STATUSES
                ),
                review_chapters=sum(
                    1 for item in statuses if item.analysis_status == "needs_review"
                ),
                failed_chapters=sum(
                    1 for item in statuses if item.analysis_status == "failed"
                ),
                total_segments=total_segments,
                review_segments=review_segments,
                total_characters=characters,
                chapters=statuses,
            )

    def cancel_book_analysis(self, book_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            job = self._active_job(session, book.id)
            if job is None:
                raise ConflictError("本书没有正在运行的分析任务", code="no_active_job")
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            return to_job_out(job)


def recover_interrupted_analysis(
    session_factory: sessionmaker[Session],
) -> dict[str, int]:
    """启动时把上次进程遗留的 running/pending 任务标记为失败。"""
    with session_scope(session_factory) as session:
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
            job.error_message = (
                "应用在分析过程中退出，任务已中断；请重新发起分析。"
            )
            job.finished_at = now
            job.updated_at = now
        chapters = list(
            session.scalars(
                select(Chapter).where(Chapter.analysis_status.in_(("queued", "running")))
            )
        )
        for chapter in chapters:
            meta = dict(chapter.analysis_meta_json or {})
            meta.update(
                {
                    "status": "failed",
                    "finished_at": _iso_now(),
                    "error": {
                        "code": "interrupted",
                        "message": "应用在分析过程中退出，已恢复为失败状态；请重新分析。",
                    },
                }
            )
            chapter.analysis_meta_json = meta
            chapter.analysis_status = "failed"
            chapter.updated_at = now
        from novelvoice_studio.services.bible import recover_interrupted_bible
        from novelvoice_studio.services.scenes import recover_interrupted_scenes

        bible = recover_interrupted_bible(session)
        scenes = recover_interrupted_scenes(session)
        return {
            "jobs": len(jobs),
            "chapters": len(chapters),
            "bible_jobs": bible["jobs"],
            "bibles": bible["bibles"],
            "scene_jobs": scenes["jobs"],
            "scene_chapters": scenes["chapters"],
        }