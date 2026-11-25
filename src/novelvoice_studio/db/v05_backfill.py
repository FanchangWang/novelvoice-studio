"""v0.5.0 旧数据回填。

该模块由 Alembic 迁移调用，也可以被迁移测试直接调用。为保持历史迁移可重复，
这里的字段假设固定为 0.5.0 结构，不随业务服务演进而改变。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

_EMPTY_BIBLE = {
    "legacy": True,
    "characters": [],
    "relationships": [],
}


def stable_id(prefix: str, key: str) -> str:
    """生成符合 validate_id 规则的确定性 ID。"""
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"nvs05:{key}").hex[:16]
    return f"{prefix}_{digest}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(data_dir: Path, relative: str | None) -> Path | None:
    if not relative:
        return None
    root = data_dir.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _resolve_character(character_id: str | None, characters: dict[str, dict[str, Any]]) -> str | None:
    seen: set[str] = set()
    current = character_id
    while current and current not in seen:
        seen.add(current)
        row = characters.get(current)
        if row is None:
            return None
        merged_into = row.get("merged_into_id")
        if not merged_into:
            return current
        current = str(merged_into)
    return None


def _table_ids(connection: Connection, table: str) -> set[str]:
    return {str(row[0]) for row in connection.execute(text(f"SELECT id FROM {table}"))}


def _insert_if_missing(
    connection: Connection,
    existing: set[str],
    *,
    table: str,
    item_id: str,
    values: dict[str, Any],
) -> bool:
    if item_id in existing:
        return False
    columns = ", ".join(values)
    params = ", ".join(f":{key}" for key in values)
    connection.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values
    )
    existing.add(item_id)
    return True


def backfill_v05(connection: Connection, data_dir: Path) -> dict[str, Any]:
    """把 0.4.0 数据映射到 0.5.0 表；重复执行不增加数据。"""
    started = _now()
    stats: dict[str, Any] = {
        "started_at": started.isoformat(),
        "books_scanned": 0,
        "books_migrated": 0,
        "book_bibles_created": 0,
        "character_arcs_created": 0,
        "scenes_created": 0,
        "scene_characters_created": 0,
        "voice_actors_created": 0,
        "voice_casts_created": 0,
        "script_versions_created": 0,
        "script_units_created": 0,
        "segments_mapped": 0,
        "audio_assets_mapped": 0,
        "review_items_created": 0,
        "skipped": 0,
        "warnings": [],
    }

    def warn(code: str, message: str, *, skipped: bool = False, **extra: Any) -> None:
        stats["warnings"].append({"code": code, "message": message, **extra})
        if skipped:
            stats["skipped"] += 1

    books = {
        str(row["id"]): dict(row)
        for row in connection.execute(text("SELECT * FROM books")).mappings()
    }
    stats["books_scanned"] = len(books)
    chapters = {
        str(row["id"]): dict(row)
        for row in connection.execute(text("SELECT * FROM chapters")).mappings()
    }
    characters = {
        str(row["id"]): dict(row)
        for row in connection.execute(text("SELECT * FROM characters")).mappings()
    }
    segments_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        text("SELECT * FROM segments ORDER BY chapter_id, segment_index, id")
    ).mappings():
        segments_by_chapter[str(row["chapter_id"])].append(dict(row))

    active_characters_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for character in characters.values():
        if character.get("merged_into_id"):
            continue
        active_characters_by_book[str(character["book_id"])].append(character)
    for items in active_characters_by_book.values():
        items.sort(key=lambda item: str(item.get("canonical_name") or ""))

    relationships_by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        text("SELECT * FROM character_relationships ORDER BY book_id, id")
    ).mappings():
        relationships_by_book[str(row["book_id"])].append(dict(row))

    bible_ids = _table_ids(connection, "book_bibles")
    arc_ids = _table_ids(connection, "character_arcs")
    scene_ids = _table_ids(connection, "scenes")
    scene_character_ids = _table_ids(connection, "scene_characters")
    actor_ids = _table_ids(connection, "voice_actors")
    cast_ids = _table_ids(connection, "voice_casts")
    script_version_ids = _table_ids(connection, "script_versions")
    script_unit_ids = _table_ids(connection, "script_units")
    review_ids = _table_ids(connection, "review_items")

    # 1. 全书圣经与角色弧。
    for book_id, book in books.items():
        active = active_characters_by_book.get(book_id, [])
        if not active:
            continue
        bible_id = stable_id("bible", f"book:{book_id}:1")
        created = _insert_if_missing(
            connection,
            bible_ids,
            table="book_bibles",
            item_id=bible_id,
            values={
                "id": bible_id,
                "book_id": book_id,
                "version": 1,
                "status": "approved",
                "source": "legacy",
                "prompt_version": "legacy",
                "model": None,
                "summary": f"旧版数据迁移：{len(active)} 个角色",
                "content_json": _json_dump(
                    {
                        **_EMPTY_BIBLE,
                        "characters": [
                            {
                                "id": item["id"],
                                "name": item.get("canonical_name"),
                                "aliases": _json_load(item.get("aliases_json"), []),
                            }
                            for item in active
                        ],
                        "relationships": [
                            {
                                "id": item.get("id"),
                                "source_character_id": item.get("source_character_id"),
                                "target_character_id": item.get("target_character_id"),
                                "relationship_type": item.get("relationship_type"),
                                "description": item.get("description"),
                                "confidence": item.get("confidence"),
                                "evidence": _json_load(item.get("evidence_json"), []),
                            }
                            for item in relationships_by_book.get(book_id, [])
                        ],
                    }
                ),
                "timeline_json": _json_dump(
                    {"mode": "unknown", "events": [], "legacy": True}
                ),
                "confidence": 1.0,
                "is_active": 1,
                "approved_at": _now(),
                "created_at": _now(),
                "updated_at": _now(),
            },
        )
        if created:
            stats["book_bibles_created"] += 1

        for character in active:
            character_id = str(character["id"])
            arc_id = stable_id("arc", f"{book_id}:{character_id}:0")
            identity = (
                character.get("identity_description")
                or character.get("personality_desc")
                or ""
            )
            appearance = (
                {"description": character["appearance_desc"]}
                if character.get("appearance_desc")
                else {}
            )
            voice_traits = (
                {"description": character["voice_description"]}
                if character.get("voice_description")
                else {}
            )
            first_chapter = character.get("first_chapter_id")
            if first_chapter not in chapters:
                first_chapter = None
            created = _insert_if_missing(
                connection,
                arc_ids,
                table="character_arcs",
                item_id=arc_id,
                values={
                    "id": arc_id,
                    "book_id": book_id,
                    "bible_id": bible_id,
                    "character_id": character_id,
                    "arc_index": 0,
                    "label": "未分阶段",
                    "age_stage": character.get("age_range") or "unknown",
                    "chapter_start_id": first_chapter,
                    "chapter_end_id": None,
                    "story_time_start": None,
                    "story_time_end": None,
                    "story_order_start": None,
                    "story_order_end": None,
                    "summary": identity or None,
                    "traits_json": _json_dump(
                        _json_load(character.get("personality_json"), [])
                    ),
                    "appearance_json": _json_dump(appearance),
                    "voice_traits_json": _json_dump(voice_traits),
                    "confidence": _clamp(_as_float(character.get("confidence"))),
                    "evidence_json": _json_dump(
                        _json_load(character.get("evidence_json"), [])
                    ),
                    "source": "legacy",
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            if created:
                stats["character_arcs_created"] += 1

        meta = _json_load(book.get("meta_json"), {})
        meta["legacy_migrated"] = True
        connection.execute(
            text("UPDATE books SET bible_status = 'approved', meta_json = :meta WHERE id = :id"),
            {"meta": _json_dump(meta), "id": book_id},
        )
        stats["books_migrated"] += 1

    # 2. 分场与场景角色状态。
    for chapter_id, segments in segments_by_chapter.items():
        chapter = chapters.get(chapter_id)
        if chapter is None:
            warn(
                "chapter_missing",
                "片段引用了不存在的章节",
                skipped=True,
                chapter_id=chapter_id,
            )
            continue
        book_id = str(chapter["book_id"])
        chapter_start = _as_int(chapter.get("source_start_offset"), -1)
        chapter_end = _as_int(chapter.get("source_end_offset"), -1)
        if chapter_start < 0 or chapter_end <= chapter_start:
            warn(
                "chapter_offsets_invalid",
                "章节偏移无效，跳过分场",
                skipped=True,
                chapter_id=chapter_id,
            )
            continue

        scene_id = stable_id("scene", f"{chapter_id}:1:0")
        created = _insert_if_missing(
            connection,
            scene_ids,
            table="scenes",
            item_id=scene_id,
            values={
                "id": scene_id,
                "book_id": book_id,
                "chapter_id": chapter_id,
                "analysis_version": 1,
                "scene_index": 0,
                "title": chapter.get("title"),
                "summary": chapter.get("summary") or "旧版章节整体迁移",
                "location": None,
                "story_time_label": None,
                "story_order": None,
                "time_mode": "unknown",
                "pov_character_id": None,
                "mood": None,
                "source_start_offset": chapter_start,
                "source_end_offset": chapter_end,
                "analysis_status": "completed",
                "confidence": 1.0,
                "evidence_json": _json_dump(
                    [{"type": "legacy_migration", "reason": "one_scene_per_chapter"}]
                ),
                "meta_json": _json_dump({"legacy": True}),
                "source": "legacy",
                "created_at": _now(),
                "updated_at": _now(),
            },
        )
        if created:
            stats["scenes_created"] += 1
        connection.execute(
            text(
                "UPDATE chapters SET scene_version = 1, scene_status = 'completed' "
                "WHERE id = :id"
            ),
            {"id": chapter_id},
        )

        speaker_segments: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for segment in segments:
            if segment.get("speaker_type") != "character":
                continue
            canonical = _resolve_character(segment.get("speaker_id"), characters)
            if canonical is None:
                continue
            speaker_segments[canonical].append(segment)

        for character_id, items in speaker_segments.items():
            character = characters.get(character_id)
            if character is None:
                continue
            scene_character_id = stable_id(
                "schar", f"{scene_id}:{character_id}:0"
            )
            emotions = [
                str(item.get("emotion"))
                for item in items
                if item.get("emotion")
            ]
            emotional_state = Counter(emotions).most_common(1)[0][0] if emotions else None
            confidence_values = [
                _as_float(item.get("speaker_confidence"), 1.0)
                for item in items
                if item.get("speaker_confidence") is not None
            ]
            confidence = (
                sum(confidence_values) / len(confidence_values)
                if confidence_values
                else 1.0
            )
            created = _insert_if_missing(
                connection,
                scene_character_ids,
                table="scene_characters",
                item_id=scene_character_id,
                values={
                    "id": scene_character_id,
                    "book_id": book_id,
                    "scene_id": scene_id,
                    "character_id": character_id,
                    "state_phase": 0,
                    "presence": "speaker",
                    "age_stage": character.get("age_range") or "unknown",
                    "physical_state": None,
                    "emotional_state": emotional_state,
                    "relationship_state": None,
                    "knowledge_state": None,
                    "disguise_state": None,
                    "voice_note": None,
                    "state_tags_json": _json_dump(["legacy_approximation"]),
                    "confidence": _clamp(confidence),
                    "evidence_json": _json_dump(
                        [
                            {
                                "type": "legacy_migration",
                                "segment_count": len(items),
                            }
                        ]
                    ),
                    "source": "legacy",
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            if created:
                stats["scene_characters_created"] += 1

    # 3. 全局配音员与选角。
    profiles = [
        dict(row)
        for row in connection.execute(
            text(
                "SELECT * FROM voice_profiles "
                "WHERE is_selected = 1 AND status = 'ready' "
                "ORDER BY owner_type, owner_id, updated_at DESC, quality_score DESC, id"
            )
        ).mappings()
    ]
    active_cast_keys: set[tuple[Any, ...]] = set()
    for row in connection.execute(
        text("SELECT * FROM voice_casts WHERE is_active = 1")
    ).mappings():
        cast = dict(row)
        if cast.get("scope") == "narrator":
            active_cast_keys.add((str(cast["book_id"]), "narrator"))
        elif cast.get("scope") == "character":
            active_cast_keys.add(
                (str(cast["book_id"]), "character", str(cast["character_id"]))
            )

    for profile in profiles:
        profile_id = str(profile["id"])
        owner_type = profile.get("owner_type")
        owner_id = str(profile.get("owner_id") or "")
        book_id: str | None = None
        character_id: str | None = None
        scope: str | None = None
        if owner_type == "narrator":
            if owner_id not in books:
                warn(
                    "voice_owner_missing",
                    "旁白音色指向的书籍不存在，跳过",
                    skipped=True,
                    profile_id=profile_id,
                    owner_id=owner_id,
                )
                continue
            book_id = owner_id
            scope = "narrator"
            cast_key: tuple[Any, ...] = (book_id, "narrator")
        elif owner_type == "character":
            character_id = _resolve_character(owner_id, characters)
            character = characters.get(character_id) if character_id else None
            if character is None:
                warn(
                    "voice_owner_missing",
                    "角色音色指向的角色不存在，跳过",
                    skipped=True,
                    profile_id=profile_id,
                    owner_id=owner_id,
                )
                continue
            book_id = str(character["book_id"])
            scope = "character"
            cast_key = (book_id, "character", character_id)
        else:
            warn(
                "voice_owner_invalid",
                "未知音色 owner_type，跳过",
                skipped=True,
                profile_id=profile_id,
            )
            continue
        book = books.get(book_id)
        if book is None:
            warn(
                "voice_book_missing",
                "音色所属书籍不存在，跳过",
                skipped=True,
                profile_id=profile_id,
            )
            continue

        reference = _safe_path(data_dir, profile.get("reference_audio_path"))
        if reference is None or not reference.is_file():
            warn(
                "voice_reference_missing",
                "音色参考音缺失，跳过迁移",
                skipped=True,
                profile_id=profile_id,
                book_id=book_id,
            )
            continue

        actor_id = stable_id("va", f"profile:{profile_id}")
        if actor_id not in actor_ids:
            destination = data_dir / "voice_actors" / actor_id / "reference.wav"
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                source_sha = _sha256(reference)
                if destination.is_file():
                    if _sha256(destination) != source_sha:
                        warn(
                            "voice_reference_conflict",
                            "全局参考音已存在且校验和不一致，保留现有文件",
                            profile_id=profile_id,
                            actor_id=actor_id,
                        )
                else:
                    shutil.copyfile(reference, destination)
            except OSError as exc:
                warn(
                    "voice_reference_copy_failed",
                    f"参考音复制失败：{exc}",
                    skipped=True,
                    profile_id=profile_id,
                    book_id=book_id,
                )
                continue
            relative_path = destination.resolve().relative_to(data_dir.resolve()).as_posix()
            params = _json_load(profile.get("generation_params_json"), {})
            params["legacy_reference_sha256"] = source_sha
            params["legacy_reference_source"] = profile.get("reference_audio_path")
            character = characters.get(character_id) if character_id else None
            gender = (character or {}).get("gender") or "unknown"
            if gender not in {"male", "female"}:
                gender = "unknown"
            age = (character or {}).get("age_range") or "unknown"
            if age not in {
                "child",
                "teenager",
                "young_adult",
                "adult",
                "middle_aged",
                "elderly",
            }:
                age = "unknown"
            timbre_tags = ["legacy"]
            if character and character.get("voice_description"):
                timbre_tags.append(str(character["voice_description"])[:80])
            personality_tags = (
                _json_load(character.get("personality_json"), [])
                if character
                else []
            )
            created = _insert_if_missing(
                connection,
                actor_ids,
                table="voice_actors",
                item_id=actor_id,
                values={
                    "id": actor_id,
                    "display_name": profile.get("name") or "旧版配音员",
                    "description": profile.get("description")
                    or (character or {}).get("voice_description"),
                    "gender_presentation": gender,
                    "age_impression": age,
                    "language": book.get("language") or "zh-CN",
                    "timbre_tags_json": _json_dump(timbre_tags),
                    "personality_tags_json": _json_dump(personality_tags),
                    "reference_text": profile.get("reference_text"),
                    "reference_audio_path": relative_path,
                    "instruct_text": profile.get("instruct_text"),
                    "seed": profile.get("seed"),
                    "provider": params.get("provider")
                    if isinstance(params.get("provider"), str)
                    else None,
                    "model": params.get("model")
                    if isinstance(params.get("model"), str)
                    else None,
                    "generation_params_json": _json_dump(params),
                    "sample_rate": profile.get("sample_rate"),
                    "duration_ms": profile.get("duration_ms"),
                    "status": "ready",
                    "source": "legacy",
                    "source_book_id": book_id,
                    "is_favorite": 0,
                    "legacy_voice_profile_id": profile_id,
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            if created:
                stats["voice_actors_created"] += 1
        else:
            character = characters.get(character_id) if character_id else None

        cast_id = stable_id("cast", f"profile:{profile_id}")
        if cast_id not in cast_ids:
            is_active = cast_key not in active_cast_keys
            if not is_active:
                warn(
                    "voice_cast_conflict",
                    "同一 owner 有多个已选音色，保留最新一个为 active",
                    profile_id=profile_id,
                    book_id=book_id,
                    character_id=character_id,
                )
            else:
                active_cast_keys.add(cast_key)
            created = _insert_if_missing(
                connection,
                cast_ids,
                table="voice_casts",
                item_id=cast_id,
                values={
                    "id": cast_id,
                    "book_id": book_id,
                    "character_id": character_id,
                    "voice_actor_id": actor_id,
                    "scope": scope,
                    "age_stage_condition": None,
                    "state_condition": None,
                    "priority": 0,
                    "source": "legacy",
                    "confidence": 1.0,
                    "is_active": 1 if is_active else 0,
                    "reason": "旧版音色迁移",
                    "legacy_voice_profile_id": profile_id,
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            if created:
                stats["voice_casts_created"] += 1
        connection.execute(
            text(
                "UPDATE voice_profiles SET migrated_voice_actor_id = :actor_id "
                "WHERE id = :profile_id"
            ),
            {"actor_id": actor_id, "profile_id": profile_id},
        )

    active_narrator_casts: dict[str, str] = {}
    active_character_casts: dict[tuple[str, str], str] = {}
    for row in connection.execute(
        text("SELECT * FROM voice_casts WHERE is_active = 1")
    ).mappings():
        cast = dict(row)
        if cast.get("scope") == "narrator":
            active_narrator_casts[str(cast["book_id"])] = str(cast["id"])
        elif cast.get("scope") == "character" and cast.get("character_id"):
            active_character_casts[
                (str(cast["book_id"]), str(cast["character_id"]))
            ] = str(cast["id"])

    scene_character_lookup: dict[tuple[str, str], str] = {}
    for row in connection.execute(
        text("SELECT id, scene_id, character_id FROM scene_characters")
    ).mappings():
        scene_character_lookup[(str(row["scene_id"]), str(row["character_id"]))] = str(
            row["id"]
        )

    # 4. 有声剧本与单元。
    for chapter_id, segments in segments_by_chapter.items():
        chapter = chapters.get(chapter_id)
        if chapter is None or not segments:
            continue
        book_id = str(chapter["book_id"])
        chapter_start = _as_int(chapter.get("source_start_offset"), -1)
        chapter_end = _as_int(chapter.get("source_end_offset"), -1)
        scene_id = stable_id("scene", f"{chapter_id}:1:0")
        unit_scene_id = scene_id if scene_id in scene_ids else None
        version_id = stable_id("sv", f"{chapter_id}:1")
        created = _insert_if_missing(
            connection,
            script_version_ids,
            table="script_versions",
            item_id=version_id,
            values={
                "id": version_id,
                "book_id": book_id,
                "chapter_id": chapter_id,
                "version": 1,
                "status": "approved",
                "adaptation_level": "legacy",
                "source_scene_version": 1,
                "source_hash": None,
                "prompt_version": "legacy",
                "model": None,
                "summary_json": _json_dump({"legacy": True}),
                "is_active": 1,
                "approved_at": _now(),
                "source": "legacy",
                "created_at": _now(),
                "updated_at": _now(),
            },
        )
        if created:
            stats["script_versions_created"] += 1

        created_units = 0
        for unit_index, segment in enumerate(segments):
            segment_id = str(segment["id"])
            unit_id = stable_id("su", f"segment:{segment_id}")
            if unit_id in script_unit_ids:
                continue
            source_text = str(segment.get("source_text") or "")
            speech_text = str(segment.get("speech_text") or "")
            text_value = speech_text or source_text
            if not source_text and not text_value:
                warn(
                    "segment_empty",
                    "片段没有文本，跳过剧本单元",
                    skipped=True,
                    segment_id=segment_id,
                )
                continue
            start = segment.get("source_start_offset")
            end = segment.get("source_end_offset")
            start_int = _as_int(start, -1) if start is not None else -1
            end_int = _as_int(end, -1) if end is not None else -1
            needs_review = False
            if start_int < 0 or end_int <= start_int:
                if chapter_start >= 0 and chapter_end > chapter_start:
                    start_int, end_int = chapter_start, chapter_end
                    needs_review = True
                else:
                    warn(
                        "segment_offsets_invalid",
                        "片段偏移与章节偏移都不可用，跳过剧本单元",
                        skipped=True,
                        segment_id=segment_id,
                    )
                    continue
            if not speech_text:
                needs_review = True

            unit_type = str(segment.get("segment_type") or "unknown")
            if unit_type not in {
                "narration",
                "dialogue",
                "inner_monologue",
                "sfx",
                "ambience",
                "pause",
                "unknown",
            }:
                unit_type = "unknown"
                needs_review = True

            speaker_type = str(segment.get("speaker_type") or "unknown")
            if speaker_type not in {"narrator", "character", "unknown", "none"}:
                speaker_type = "unknown"
                needs_review = True
            speaker_id = _resolve_character(segment.get("speaker_id"), characters)
            if speaker_type == "character" and speaker_id is None:
                speaker_type = "unknown"
                needs_review = True
            if speaker_type != "character":
                speaker_id = None

            review_status = str(segment.get("review_status") or "auto")
            if review_status not in {"auto", "needs_review", "approved"}:
                review_status = "needs_review"
            if needs_review and review_status != "approved":
                review_status = "needs_review"
            if unit_type == "unknown" and review_status != "approved":
                review_status = "needs_review"

            audio_status = str(segment.get("audio_status") or "none")
            if audio_status not in {
                "not_applicable",
                "none",
                "stale",
                "queued",
                "generating",
                "ready",
                "failed",
            }:
                audio_status = "none"
            if unit_type in {"sfx", "ambience", "pause"}:
                audio_status = "not_applicable"
                text_value = ""

            character_state_id = (
                scene_character_lookup.get((scene_id, speaker_id))
                if unit_scene_id and speaker_type == "character" and speaker_id
                else None
            )
            voice_cast_id = None
            if speaker_type == "narrator":
                voice_cast_id = active_narrator_casts.get(book_id)
            elif speaker_type == "character" and speaker_id:
                voice_cast_id = active_character_casts.get((book_id, speaker_id))

            confidence = _clamp(
                _as_float(segment.get("speaker_confidence"), 1.0)
            )
            created = _insert_if_missing(
                connection,
                script_unit_ids,
                table="script_units",
                item_id=unit_id,
                values={
                    "id": unit_id,
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "script_version_id": version_id,
                    "scene_id": unit_scene_id,
                    "unit_index": unit_index,
                    "unit_type": unit_type,
                    "speaker_type": speaker_type,
                    "speaker_id": speaker_id,
                    "speaker_name_snapshot": segment.get("speaker_name_snapshot"),
                    "character_state_id": character_state_id,
                    "voice_cast_id": voice_cast_id,
                    "source_text": source_text,
                    "text": text_value,
                    "display_text": text_value,
                    "source_start_offset": start_int,
                    "source_end_offset": end_int,
                    "emotion": segment.get("emotion") or "neutral",
                    "intensity": _as_int(segment.get("intensity"), 1),
                    "delivery": segment.get("delivery") or "normal",
                    "tts_instruction": segment.get("tts_instruction"),
                    "speed": _as_float(segment.get("speed"), 1.0),
                    "pause_before_ms": _as_int(segment.get("pause_before_ms"), 0),
                    "pause_after_ms": _as_int(segment.get("pause_after_ms"), 250),
                    "sfx_json": _json_dump({}),
                    "review_status": review_status,
                    "confidence": confidence,
                    "audio_status": audio_status,
                    "current_audio_asset_id": segment.get("current_audio_asset_id"),
                    "render_hash": None,
                    "user_locked_fields_json": _json_dump(
                        _json_load(segment.get("user_locked_fields_json"), [])
                    ),
                    "evidence_json": _json_dump(
                        _json_load(segment.get("evidence_json"), [])
                    ),
                    "source": "legacy",
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            if created:
                created_units += 1
                stats["script_units_created"] += 1
            connection.execute(
                text(
                    "UPDATE segments SET migrated_script_unit_id = :unit_id "
                    "WHERE id = :segment_id"
                ),
                {"unit_id": unit_id, "segment_id": segment_id},
            )
            stats["segments_mapped"] += 1

        connection.execute(
            text(
                "UPDATE script_versions SET summary_json = :summary "
                "WHERE id = :id"
            ),
            {
                "summary": _json_dump(
                    {
                        "legacy": True,
                        "unit_count": len(segments),
                        "created_units": created_units,
                    }
                ),
                "id": version_id,
            },
        )
        connection.execute(
            text(
                "UPDATE chapters SET script_status = 'completed' WHERE id = :id"
            ),
            {"id": chapter_id},
        )

    # 5. 音频资产映射。
    mapped_before = int(
        connection.execute(
            text("SELECT COUNT(*) FROM audio_assets WHERE script_unit_id IS NOT NULL")
        ).scalar_one()
    )
    connection.execute(
        text(
            """
            UPDATE audio_assets
            SET script_unit_id = (
                SELECT su.id
                FROM script_units su
                JOIN segments s ON s.migrated_script_unit_id = su.id
                WHERE s.id = audio_assets.segment_id
                LIMIT 1
            )
            WHERE segment_id IS NOT NULL AND script_unit_id IS NULL
            """
        )
    )
    mapped_after = int(
        connection.execute(
            text("SELECT COUNT(*) FROM audio_assets WHERE script_unit_id IS NOT NULL")
        ).scalar_one()
    )
    stats["audio_assets_mapped"] = mapped_after - mapped_before
    connection.execute(
        text(
            """
            UPDATE audio_assets
            SET voice_actor_id = (
                SELECT vc.voice_actor_id
                FROM script_units su
                JOIN voice_casts vc ON vc.book_id = su.book_id AND vc.is_active = 1
                WHERE su.id = audio_assets.script_unit_id
                  AND (
                    (su.speaker_type = 'narrator' AND vc.scope = 'narrator')
                    OR (
                        su.speaker_type = 'character'
                        AND vc.scope = 'character'
                        AND vc.character_id = su.speaker_id
                    )
                  )
                LIMIT 1
            )
            WHERE script_unit_id IS NOT NULL AND voice_actor_id IS NULL
            """
        )
    )

    # 6. 迁移提示与生产状态。
    for book_id, book in books.items():
        has_script = connection.execute(
            text(
                "SELECT 1 FROM script_versions WHERE book_id = :book_id "
                "AND is_active = 1 LIMIT 1"
            ),
            {"book_id": book_id},
        ).first()
        connection.execute(
            text("UPDATE books SET production_status = :status WHERE id = :id"),
            {"status": "ready" if has_script else "not_started", "id": book_id},
        )
        active = active_characters_by_book.get(book_id, [])
        if not active:
            continue
        bible_id = stable_id("bible", f"book:{book_id}:1")
        review_id = stable_id("rv", f"legacy:{book_id}")
        created = _insert_if_missing(
            connection,
            review_ids,
            table="review_items",
            item_id=review_id,
            values={
                "id": review_id,
                "book_id": book_id,
                "entity_type": "book_bible",
                "entity_id": bible_id,
                "code": "legacy_migration",
                "severity": "info",
                "title": "旧版数据已迁移",
                "message": (
                    "角色、片段和音色已映射到 0.5.0 模型；"
                    "建议重新生成角色圣经与有声剧本。"
                ),
                "payload_json": _json_dump({"book_id": book_id}),
                "status": "open",
                "source": "system",
                "resolution_note": None,
                "resolved_at": None,
                "created_at": _now(),
                "updated_at": _now(),
            },
        )
        if created:
            stats["review_items_created"] += 1

    stats["finished_at"] = _now().isoformat()
    return stats