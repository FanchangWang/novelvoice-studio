"""角色人工审核：编辑、合并、拆分与关系维护。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from novelvoice_studio.core.errors import ConflictError, NotFoundError, ValidationError
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Book, Character, CharacterRelationship, Segment
from novelvoice_studio.schemas.analysis import (
    CharacterListResponse,
    CharacterMergeRequest,
    CharacterMergeResponse,
    CharacterOut,
    CharacterSplitResponse,
    CharacterUpdate,
    RelationshipListResponse,
)
from novelvoice_studio.services.analysis_serializers import (
    to_character_out,
    to_relationship_out,
)
from novelvoice_studio.services.paths import new_id, validate_id

_ALIAS_BASIS = {"explicit", "inferred", "unknown"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] if cleaned else None


def _alias_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return ""


def normalize_aliases(raw: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return result
    for item in raw:
        name = _alias_name(item)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        if isinstance(item, dict):
            evidence = item.get("evidence")
            result.append(
                {
                    "name": name[:120],
                    "confidence": max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
                    "basis": (
                        item.get("basis") if item.get("basis") in _ALIAS_BASIS else "unknown"
                    ),
                    "chapter_id": item.get("chapter_id"),
                    "evidence": evidence if isinstance(evidence, list) else [],
                    "source": str(item.get("source") or "ai"),
                }
            )
        else:
            result.append(
                {
                    "name": name[:120],
                    "confidence": 0.0,
                    "basis": "unknown",
                    "chapter_id": None,
                    "evidence": [],
                    "source": "ai",
                }
            )
    return result


class CharacterService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -------------------------------------------------------------- helpers

    def _book(self, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = self.session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _character(self, character_id: str) -> Character:
        validate_id(character_id, "char")
        character = self.session.get(Character, character_id)
        if character is None:
            raise NotFoundError("角色不存在")
        return character

    def _active(self, book_id: str) -> list[Character]:
        return list(
            self.session.scalars(
                select(Character)
                .where(Character.book_id == book_id, Character.merged_into_id.is_(None))
                .order_by(Character.created_at.asc())
            )
        )

    def _all_by_id(self, book_id: str) -> dict[str, Character]:
        rows = self.session.scalars(
            select(Character).where(Character.book_id == book_id)
        )
        return {item.id: item for item in rows}

    def _merged_source_map(self, book_id: str) -> dict[str, list[str]]:
        rows = self.session.execute(
            select(Character.merged_into_id, Character.id).where(
                Character.book_id == book_id, Character.merged_into_id.is_not(None)
            )
        ).all()
        result: dict[str, list[str]] = {}
        for target_id, source_id in rows:
            if target_id:
                result.setdefault(str(target_id), []).append(str(source_id))
        return result

    # ----------------------------------------------------------------- read

    def list_characters(
        self, book_id: str, *, include_merged: bool = False
    ) -> CharacterListResponse:
        self._book(book_id)
        source_map = self._merged_source_map(book_id)
        rows = list(
            self.session.scalars(
                select(Character)
                .where(Character.book_id == book_id)
                .order_by(Character.created_at.asc())
            )
        )
        if not include_merged:
            rows = [item for item in rows if item.merged_into_id is None]
        items = [to_character_out(item, source_map.get(item.id, [])) for item in rows]
        return CharacterListResponse(items=items, total=len(items))

    def get_character(self, character_id: str) -> CharacterOut:
        character = self._character(character_id)
        self._book(character.book_id)
        source_map = self._merged_source_map(character.book_id)
        return to_character_out(character, source_map.get(character.id, []))

    def list_relationships(self, book_id: str) -> RelationshipListResponse:
        self._book(book_id)
        names = {
            item.id: item.canonical_name
            for item in self.session.scalars(
                select(Character).where(Character.book_id == book_id)
            )
        }
        rows = list(
            self.session.scalars(
                select(CharacterRelationship)
                .where(CharacterRelationship.book_id == book_id)
                .order_by(CharacterRelationship.created_at.asc())
            )
        )
        active_ids = {
            item.id
            for item in self.session.scalars(
                select(Character).where(
                    Character.book_id == book_id, Character.merged_into_id.is_(None)
                )
            )
        }
        rows = [
            item
            for item in rows
            if item.source_character_id in active_ids and item.target_character_id in active_ids
        ]
        items = [to_relationship_out(item, names) for item in rows]
        return RelationshipListResponse(items=items, total=len(items))

    # --------------------------------------------------------------- update

    def update_character(self, character_id: str, payload: CharacterUpdate) -> CharacterOut:
        character = self._character(character_id)
        self._book(character.book_id)
        if character.merged_into_id:
            raise ConflictError("该角色已合并到其他角色，请先拆分后再编辑")
        fields = payload.model_fields_set
        if not fields:
            return self.get_character(character.id)
        original_name = character.canonical_name

        if "canonical_name" in fields and payload.canonical_name is not None:
            new_name = payload.canonical_name.strip()
            if not new_name:
                raise ValidationError("角色名称不能为空")
            duplicate = self.session.scalar(
                select(Character.id).where(
                    Character.book_id == character.book_id,
                    Character.id != character.id,
                    Character.merged_into_id.is_(None),
                    func.lower(Character.canonical_name) == new_name.casefold(),
                )
            )
            if duplicate:
                raise ConflictError("已存在同名角色，请使用合并功能")
            if new_name.casefold() != character.canonical_name.casefold():
                existing = normalize_aliases(character.aliases_json)
                if not any(
                    item["name"].casefold() == character.canonical_name.casefold()
                    for item in existing
                ):
                    existing.append(
                        {
                            "name": character.canonical_name,
                            "confidence": 1.0,
                            "basis": "explicit",
                            "chapter_id": character.first_chapter_id,
                            "evidence": [],
                            "source": "manual",
                        }
                    )
                character.aliases_json = existing
                character.canonical_name = new_name[:120]

        if "aliases" in fields and payload.aliases is not None:
            previous = {
                item["name"].casefold(): item
                for item in normalize_aliases(character.aliases_json)
            }
            aliases: list[dict[str, Any]] = []
            seen: set[str] = set()
            for name in payload.aliases:
                cleaned = name.strip()
                if not cleaned or cleaned.casefold() in seen:
                    continue
                if cleaned.casefold() == character.canonical_name.casefold():
                    continue
                seen.add(cleaned.casefold())
                aliases.append(
                    previous.get(
                        cleaned.casefold(),
                        {
                            "name": cleaned[:120],
                            "confidence": 1.0,
                            "basis": "explicit",
                            "chapter_id": character.first_chapter_id,
                            "evidence": [],
                            "source": "manual",
                        },
                    )
                )
            # 改名后的旧名字必须保留为别名，避免原先按旧名绑定的片段失去可读标识。
            if (
                original_name.casefold() != character.canonical_name.casefold()
                and original_name.casefold() not in seen
            ):
                seen.add(original_name.casefold())
                aliases.append(
                    previous.get(
                        original_name.casefold(),
                        {
                            "name": original_name[:120],
                            "confidence": 1.0,
                            "basis": "explicit",
                            "chapter_id": character.first_chapter_id,
                            "evidence": [],
                            "source": "manual",
                        },
                    )
                )
            character.aliases_json = aliases
            candidates = normalize_aliases(character.alias_candidates_json)
            character.alias_candidates_json = [
                item for item in candidates if item["name"].casefold() not in seen
            ]

        scalar_fields = (
            "gender",
            "gender_basis",
            "age_range",
            "age_basis",
            "character_type",
            "identity_description",
            "personality_desc",
            "appearance_desc",
            "voice_description",
        )
        for field_name in scalar_fields:
            if field_name not in fields:
                continue
            value = getattr(payload, field_name)
            if isinstance(value, str):
                value = _clean_text(value, 2000)
            setattr(character, field_name, value)

        if "personality" in fields and payload.personality is not None:
            cleaned = []
            seen_tags: set[str] = set()
            for tag in payload.personality:
                text = tag.strip()
                if text and text.casefold() not in seen_tags:
                    seen_tags.add(text.casefold())
                    cleaned.append(text[:60])
            character.personality_json = cleaned[:20]

        if "is_provisional" in fields and payload.is_provisional is not None:
            character.is_provisional = payload.is_provisional

        if "user_locked" in fields and payload.user_locked is not None:
            character.user_locked = payload.user_locked
        elif fields - {"user_locked"}:
            # 用户一旦编辑角色资料，即视为人工确认，后续 AI 重跑不得覆盖。
            character.user_locked = True

        character.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(character)
        return self.get_character(character.id)

    # ---------------------------------------------------------------- merge

    def merge(self, payload: CharacterMergeRequest) -> CharacterMergeResponse:
        target = self._character(payload.target_character_id)
        book = self._book(target.book_id)
        if target.merged_into_id:
            raise ConflictError("目标角色已被合并，请选择未合并的角色")
        sources: list[Character] = []
        for source_id in payload.source_character_ids:
            source = self._character(source_id)
            if source.book_id != book.id:
                raise ValidationError("只能合并同一本书的角色")
            if source.merged_into_id:
                raise ConflictError(f"角色「{source.canonical_name}」已经合并")
            if source.id == target.id:
                raise ValidationError("不能合并到自身")
            sources.append(source)

        children = self.session.scalar(
            select(func.count(Character.id)).where(
                Character.merged_into_id.in_([item.id for item in sources])
            )
        )
        if children:
            raise ConflictError("部分被合并角色自身包含已合并角色，请先拆分")

        target_aliases = normalize_aliases(target.aliases_json)
        target_alias_names = {
            item["name"].casefold() for item in target_aliases
        } | {target.canonical_name.casefold()}
        merge_log = list(target.merge_log_json or [])
        total_segments = 0

        for source in sources:
            segment_ids = [
                str(item)
                for item in self.session.scalars(
                    select(Segment.id).where(
                        Segment.book_id == book.id, Segment.speaker_id == source.id
                    )
                )
            ]
            if segment_ids:
                self.session.execute(
                    Segment.__table__.update()
                    .where(Segment.id.in_(segment_ids))
                    .values(
                        speaker_id=target.id,
                        speaker_name_snapshot=target.canonical_name,
                    )
                )
                total_segments += len(segment_ids)

            relationships = list(
                self.session.scalars(
                    select(CharacterRelationship).where(
                        CharacterRelationship.book_id == book.id,
                        or_(
                            CharacterRelationship.source_character_id == source.id,
                            CharacterRelationship.target_character_id == source.id,
                        ),
                    )
                )
            )
            relationship_log = [
                {
                    "source_character_id": item.source_character_id,
                    "target_character_id": item.target_character_id,
                    "relationship_type": item.relationship_type,
                    "description": item.description,
                    "evidence": item.evidence_json or [],
                    "confidence": item.confidence,
                    "is_provisional": item.is_provisional,
                }
                for item in relationships
            ]
            for item in relationships:
                self.session.delete(item)

            added_aliases: list[str] = []
            candidates = [source.canonical_name, *[
                item["name"] for item in normalize_aliases(source.aliases_json)
            ]]
            for name in candidates:
                key = name.casefold()
                if not name or key in target_alias_names:
                    continue
                target_alias_names.add(key)
                added_aliases.append(name)
                target_aliases.append(
                    {
                        "name": name[:120],
                        "confidence": 1.0,
                        "basis": "explicit",
                        "chapter_id": source.first_chapter_id,
                        "evidence": [],
                        "source": "manual_merge",
                    }
                )

            merge_log.append(
                {
                    "source_character_id": source.id,
                    "canonical_name": source.canonical_name,
                    "aliases_added": added_aliases,
                    "segment_ids": segment_ids,
                    "relationships": relationship_log,
                    "merged_at": _iso_now(),
                }
            )
            source.updated_at = utc_now()
            source.merged_into_id = target.id
            source.merge_log_json = [
                *(source.merge_log_json or []),
                {
                    "merged_into_id": target.id,
                    "merged_into_name": target.canonical_name,
                    "at": _iso_now(),
                },
            ]

        target.aliases_json = target_aliases
        target.merge_log_json = merge_log
        target.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(target)
        return CharacterMergeResponse(
            target=self.get_character(target.id),
            merged_source_ids=[item.id for item in sources],
            updated_segments=total_segments,
        )

    def split(self, character_id: str, source_character_id: str) -> CharacterSplitResponse:
        target = self._character(character_id)
        source = self._character(source_character_id)
        if target.book_id != source.book_id:
            raise ValidationError("角色不属于同一本书")
        if target.merged_into_id:
            raise ConflictError("当前角色已被合并，请先拆分")
        if source.merged_into_id != target.id:
            raise ConflictError("该角色并非合并到当前角色")

        merge_log = list(target.merge_log_json or [])
        event = next(
            (
                item
                for item in reversed(merge_log)
                if isinstance(item, dict)
                and item.get("source_character_id") == source.id
            ),
            None,
        )
        if event is None:
            raise ConflictError("缺少合并记录，无法自动拆分；可手动编辑别名与片段说话人")

        other_added = {
            str(name).casefold()
            for item in merge_log
            if isinstance(item, dict)
            and item is not event
            and item.get("source_character_id") != source.id
            for name in (item.get("aliases_added") or [])
        }
        remove_names = {
            str(name).casefold()
            for name in (event.get("aliases_added") or [])
            if str(name).casefold() not in other_added
        }
        target.aliases_json = [
            item
            for item in normalize_aliases(target.aliases_json)
            if item["name"].casefold() not in remove_names
        ]

        segment_ids = [str(item) for item in (event.get("segment_ids") or [])]
        updated_segments = 0
        if segment_ids:
            result = self.session.execute(
                Segment.__table__.update()
                .where(
                    Segment.id.in_(segment_ids),
                    Segment.speaker_id == target.id,
                )
                .values(
                    speaker_id=source.id,
                    speaker_name_snapshot=source.canonical_name,
                )
            )
            updated_segments = int(result.rowcount or 0)

        for record in event.get("relationships") or []:
            if not isinstance(record, dict):
                continue
            first = str(record.get("source_character_id") or "")
            second = str(record.get("target_character_id") or "")
            if not first or not second or first == second:
                continue
            existing = self.session.scalar(
                select(CharacterRelationship.id).where(
                    CharacterRelationship.book_id == target.book_id,
                    CharacterRelationship.source_character_id == first,
                    CharacterRelationship.target_character_id == second,
                )
            )
            if existing:
                continue
            relationship_type = str(record.get("relationship_type") or "unknown")[:64]
            relationship = CharacterRelationship(
                id=new_id("rel"),
                book_id=target.book_id,
                source_character_id=first,
                target_character_id=second,
                relationship_type=relationship_type,
                description=record.get("description"),
                evidence_json=record.get("evidence") or [],
                confidence=float(record.get("confidence") or 0.0),
                is_provisional=bool(record.get("is_provisional", True)),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.session.add(relationship)

        target.merge_log_json = [item for item in merge_log if item is not event]
        target.updated_at = utc_now()
        source.merged_into_id = None
        source.merge_log_json = [
            *(source.merge_log_json or []),
            {
                "split_from_id": target.id,
                "split_from_name": target.canonical_name,
                "at": _iso_now(),
            },
        ]
        source.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(target)
        self.session.refresh(source)
        return CharacterSplitResponse(
            character=self.get_character(target.id),
            restored=self.get_character(source.id),
            updated_segments=updated_segments,
        )