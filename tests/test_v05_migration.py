from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from novelvoice_studio.config import AppConfig, ProvidersConfig, Settings
from novelvoice_studio.db.migrate import build_alembic_config, current_revision
from novelvoice_studio.db.session import create_db_engine
from novelvoice_studio.db.v05_backfill import backfill_v05, stable_id

OLD_REVISION = "5c1d9a7b3e2f"
NEW_REVISION = "9f3c2a7b5d10"
HEAD_REVISION = "a4c1e8d29b73"
TS = "2026-11-01 00:00:00"

BOOK_A = "book_1111111111111111"
BOOK_B = "book_2222222222222222"
BOOK_C = "book_3333333333333333"
CHAPTER_A = "chap_1111111111111111"
CHAPTER_C = "chap_3333333333333333"
CHAR_1 = "char_1111111111111111"
CHAR_2 = "char_2222222222222222"
CHAR_MERGED = "char_3333333333333333"
CHAR_C = "char_4444444444444444"
REL_1 = "rel_1111111111111111"
SEG_1 = "segm_1111111111111111"
SEG_2 = "segm_2222222222222222"
SEG_3 = "segm_3333333333333333"
SEG_4 = "segm_4444444444444444"
SEG_C = "segm_5555555555555555"
AUDIO_1 = "audi_1111111111111111"
AUDIO_2 = "audi_2222222222222222"
PROFILE_NARRATOR = "vpro_1111111111111111"
PROFILE_CHAR_1 = "vpro_2222222222222222"
PROFILE_MISSING = "vpro_3333333333333333"
PROFILE_UNSELECTED = "vpro_4444444444444444"


def _legacy_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        app=AppConfig(
            data_dir=str(tmp_path / "data"),
            auto_migrate=False,
            log_level="WARNING",
            max_upload_mb=2,
            frontend_dir=str(tmp_path / "missing-frontend"),
        ),
        providers=ProvidersConfig(mode="mock"),
    )
    settings.ensure_dirs()
    command.upgrade(build_alembic_config(settings), OLD_REVISION)
    assert current_revision(settings.db_path) == OLD_REVISION
    return settings


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    columns = ", ".join(values)
    params = ", ".join(f":{key}" for key in values)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({params})", values)


def _seed_legacy(settings: Settings) -> None:
    book_root = settings.data_dir / "books" / BOOK_A
    (book_root / "voices").mkdir(parents=True)
    (book_root / "audio").mkdir(parents=True)
    narrator_wav = book_root / "voices" / "narrator.wav"
    character_wav = book_root / "voices" / "character.wav"
    narrator_wav.write_bytes(b"RIFF-narrator-reference")
    character_wav.write_bytes(b"RIFF-character-reference")
    audio_1 = book_root / "audio" / "s1.wav"
    audio_2 = book_root / "audio" / "s2.wav"
    audio_1.write_bytes(b"RIFF-audio-1")
    audio_2.write_bytes(b"RIFF-audio-2")

    with sqlite3.connect(settings.db_path) as connection:
        _insert(
            connection,
            "books",
            {
                "id": BOOK_A,
                "title": "迁移测试",
                "author": "作者",
                "description": None,
                "language": "zh-CN",
                "status": "ready",
                "project_dir": f"books/{BOOK_A}",
                "source_file_path": None,
                "source_encoding": "utf-8",
                "source_char_count": 1000,
                "source_sha256": None,
                "import_warnings_json": "[]",
                "deleted_at": None,
                "created_at": TS,
                "updated_at": TS,
            },
        )
        _insert(
            connection,
            "books",
            {
                "id": BOOK_B,
                "title": "空书",
                "language": "zh-CN",
                "status": "empty",
                "project_dir": f"books/{BOOK_B}",
                "source_char_count": 0,
                "import_warnings_json": "[]",
                "created_at": TS,
                "updated_at": TS,
            },
        )
        _insert(
            connection,
            "books",
            {
                "id": BOOK_C,
                "title": "已删除的书",
                "language": "zh-CN",
                "status": "deleted",
                "project_dir": f"books/{BOOK_C}",
                "source_char_count": 500,
                "import_warnings_json": "[]",
                "deleted_at": TS,
                "created_at": TS,
                "updated_at": TS,
            },
        )
        _insert(
            connection,
            "chapters",
            {
                "id": CHAPTER_A,
                "book_id": BOOK_A,
                "title": "第一章",
                "sequence": 1,
                "source_start_offset": 0,
                "source_end_offset": 1000,
                "summary": "旧章节摘要",
                "summary_json": json.dumps({"summary": "旧章节摘要"}, ensure_ascii=False),
                "analysis_meta_json": "{}",
                "analysis_status": "completed",
                "created_at": TS,
                "updated_at": TS,
            },
        )
        _insert(
            connection,
            "chapters",
            {
                "id": CHAPTER_C,
                "book_id": BOOK_C,
                "title": "已删章节",
                "sequence": 1,
                "source_start_offset": 0,
                "source_end_offset": 500,
                "summary": "软删除书章节",
                "summary_json": "{}",
                "analysis_meta_json": "{}",
                "analysis_status": "completed",
                "created_at": TS,
                "updated_at": TS,
            },
        )

        for character_id, name, gender, age_range, merged in (
            (CHAR_1, "林川", "male", "young_adult", None),
            (CHAR_2, "苏婉", "female", "adult", None),
            (CHAR_MERGED, "林川旧名", "unknown", "unknown", CHAR_1),
        ):
            _insert(
                connection,
                "characters",
                {
                    "id": character_id,
                    "book_id": BOOK_A,
                    "canonical_name": name,
                    "aliases_json": json.dumps([name], ensure_ascii=False),
                    "alias_candidates_json": "[]",
                    "gender": gender,
                    "gender_basis": "explicit",
                    "age_range": age_range,
                    "age_basis": "inferred",
                    "character_type": "human",
                    "identity_description": f"{name}的身份",
                    "personality_json": json.dumps(["冷静"], ensure_ascii=False),
                    "personality_desc": "沉着",
                    "appearance_desc": "清瘦",
                    "voice_description": "中音",
                    "first_chapter_id": CHAPTER_A,
                    "evidence_json": "[]",
                    "confidence": 0.9,
                    "is_provisional": 0,
                    "user_locked": 1,
                    "merged_into_id": merged,
                    "merge_log_json": "[]",
                    "created_at": TS,
                    "updated_at": TS,
                },
            )
        _insert(
            connection,
            "characters",
            {
                "id": CHAR_C,
                "book_id": BOOK_C,
                "canonical_name": "陈舟",
                "aliases_json": "[]",
                "alias_candidates_json": "[]",
                "gender": "male",
                "gender_basis": "explicit",
                "age_range": "adult",
                "age_basis": "inferred",
                "character_type": "human",
                "identity_description": "旧书角色",
                "personality_json": "[]",
                "personality_desc": None,
                "appearance_desc": None,
                "voice_description": None,
                "first_chapter_id": CHAPTER_C,
                "evidence_json": "[]",
                "confidence": 0.6,
                "is_provisional": 0,
                "user_locked": 0,
                "merged_into_id": None,
                "merge_log_json": "[]",
                "created_at": TS,
                "updated_at": TS,
            },
        )
        _insert(
            connection,
            "character_relationships",
            {
                "id": REL_1,
                "book_id": BOOK_A,
                "source_character_id": CHAR_1,
                "target_character_id": CHAR_2,
                "relationship_type": "friend",
                "description": "旧友",
                "evidence_json": "[]",
                "confidence": 0.8,
                "is_provisional": 0,
                "created_at": TS,
                "updated_at": TS,
            },
        )

        segments = (
            (
                SEG_1,
                0,
                "夜色渐深。",
                "夜色渐深。",
                "narration",
                "narrator",
                None,
                None,
                "sad",
                1,
                "normal",
                "approved",
                "ready",
                AUDIO_1,
                0,
                100,
            ),
            (
                SEG_2,
                1,
                "「走吧。」",
                "走吧。",
                "dialogue",
                "character",
                CHAR_1,
                0.9,
                "determined",
                2,
                "slow",
                "approved",
                "ready",
                AUDIO_2,
                100,
                200,
            ),
            (
                SEG_3,
                2,
                "「等等。」",
                "等等。",
                "dialogue",
                "character",
                CHAR_2,
                0.8,
                "tender",
                1,
                "normal",
                "auto",
                "none",
                None,
                200,
                300,
            ),
            (
                SEG_4,
                3,
                "他想起从前。",
                "",
                "inner_monologue",
                "character",
                CHAR_MERGED,
                0.7,
                "sad",
                1,
                "normal",
                "needs_review",
                "none",
                None,
                None,
                None,
            ),
        )
        for (
            segment_id,
            index,
            source_text,
            speech_text,
            segment_type,
            speaker_type,
            speaker_id,
            speaker_confidence,
            emotion,
            intensity,
            delivery,
            review_status,
            audio_status,
            current_audio,
            start,
            end,
        ) in segments:
            _insert(
                connection,
                "segments",
                {
                    "id": segment_id,
                    "book_id": BOOK_A,
                    "chapter_id": CHAPTER_A,
                    "segment_index": index,
                    "source_text": source_text,
                    "speech_text": speech_text,
                    "segment_type": segment_type,
                    "speaker_type": speaker_type,
                    "speaker_id": speaker_id,
                    "speaker_name_snapshot": None,
                    "speaker_confidence": speaker_confidence,
                    "voice_profile_id": None,
                    "emotion": emotion,
                    "emotion_confidence": None,
                    "intensity": intensity,
                    "delivery": delivery,
                    "tts_instruction": None,
                    "speed": 1.0,
                    "pause_before_ms": 0,
                    "pause_after_ms": 250,
                    "review_status": review_status,
                    "audio_status": audio_status,
                    "current_audio_asset_id": current_audio,
                    "user_locked_fields_json": "[]",
                    "text_version": 1,
                    "source_start_offset": start,
                    "source_end_offset": end,
                    "evidence_json": "[]",
                    "created_at": TS,
                    "updated_at": TS,
                },
            )
        _insert(
            connection,
            "segments",
            {
                "id": SEG_C,
                "book_id": BOOK_C,
                "chapter_id": CHAPTER_C,
                "segment_index": 0,
                "source_text": "旧书残页。",
                "speech_text": "旧书残页。",
                "segment_type": "narration",
                "speaker_type": "narrator",
                "speaker_id": None,
                "speaker_name_snapshot": None,
                "speaker_confidence": None,
                "voice_profile_id": None,
                "emotion": "neutral",
                "emotion_confidence": None,
                "intensity": 1,
                "delivery": "normal",
                "tts_instruction": None,
                "speed": 1.0,
                "pause_before_ms": 0,
                "pause_after_ms": 250,
                "review_status": "auto",
                "audio_status": "none",
                "current_audio_asset_id": None,
                "user_locked_fields_json": "[]",
                "text_version": 1,
                "source_start_offset": 0,
                "source_end_offset": 500,
                "evidence_json": "[]",
                "created_at": TS,
                "updated_at": TS,
            },
        )

        profiles = (
            (
                PROFILE_NARRATOR,
                "narrator",
                BOOK_A,
                "旁白",
                "books/book_a_missing.wav",
                1,
                "ready",
                0.9,
            ),
            (
                PROFILE_CHAR_1,
                "character",
                CHAR_1,
                "林川音色",
                narrator_wav.relative_to(settings.data_dir).as_posix(),
                1,
                "ready",
                0.8,
            ),
            (
                PROFILE_MISSING,
                "character",
                CHAR_2,
                "苏婉音色",
                f"books/{BOOK_A}/voices/missing.wav",
                1,
                "ready",
                0.7,
            ),
            (
                PROFILE_UNSELECTED,
                "character",
                CHAR_2,
                "未选音色",
                character_wav.relative_to(settings.data_dir).as_posix(),
                0,
                "ready",
                0.5,
            ),
        )
        # 第一个旁白档案的参考音随后补到正确路径，避免同一个文件被错误复用。
        narrator_reference = f"books/{BOOK_A}/voices/narrator.wav"
        for (
            profile_id,
            owner_type,
            owner_id,
            name,
            reference_path,
            selected,
            status,
            quality,
        ) in profiles:
            if profile_id == PROFILE_NARRATOR:
                reference_path = narrator_reference
            _insert(
                connection,
                "voice_profiles",
                {
                    "id": profile_id,
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "name": name,
                    "description": None,
                    "reference_text": "参考文本",
                    "reference_audio_path": reference_path,
                    "instruct_text": "自然",
                    "seed": 42,
                    "generation_params_json": json.dumps(
                        {"provider": "qwen3_tts", "model": "legacy-model"}
                    ),
                    "sample_rate": 24000,
                    "duration_ms": 3000,
                    "is_selected": selected,
                    "is_mock": 0,
                    "status": status,
                    "quality_score": quality,
                    "user_locked": 0,
                    "created_at": TS,
                    "updated_at": TS,
                },
            )

        for asset_id, segment_id, path, tts_model in (
            (AUDIO_1, SEG_1, audio_1, "model-1"),
            (AUDIO_2, SEG_2, audio_2, "model-1"),
        ):
            _insert(
                connection,
                "audio_assets",
                {
                    "id": asset_id,
                    "segment_id": segment_id,
                    "version": 1,
                    "file_path": path.relative_to(settings.data_dir).as_posix(),
                    "format": "wav",
                    "sample_rate": 24000,
                    "channels": 1,
                    "sample_width_bits": 16,
                    "duration_ms": 1000,
                    "file_size_bytes": path.stat().st_size,
                    "checksum_sha256": None,
                    "tts_provider": "cosyvoice3",
                    "tts_model": tts_model,
                    "generation_params_json": "{}",
                    "is_valid": 1,
                    "created_at": TS,
                },
            )


def _scalar(settings: Settings, sql: str, params: tuple[object, ...] = ()) -> object:
    with sqlite3.connect(settings.db_path) as connection:
        return connection.execute(sql, params).fetchone()[0]


def _run_backfill(settings: Settings) -> dict[str, object]:
    engine = create_db_engine(settings)
    try:
        with engine.begin() as connection:
            return backfill_v05(connection, settings.data_dir)
    finally:
        engine.dispose()


def test_v05_migration_backfills_legacy_data(tmp_path: Path) -> None:
    legacy = _legacy_settings(tmp_path)
    _seed_legacy(legacy)

    command.upgrade(build_alembic_config(legacy), "head")
    assert current_revision(legacy.db_path) == HEAD_REVISION

    bible_id = stable_id("bible", f"book:{BOOK_A}:1")
    bible_c_id = stable_id("bible", f"book:{BOOK_C}:1")
    scene_id = stable_id("scene", f"{CHAPTER_A}:1:0")
    version_id = stable_id("sv", f"{CHAPTER_A}:1")
    narrator_actor = stable_id("va", f"profile:{PROFILE_NARRATOR}")
    character_actor = stable_id("va", f"profile:{PROFILE_CHAR_1}")
    narrator_cast = stable_id("cast", f"profile:{PROFILE_NARRATOR}")
    character_cast = stable_id("cast", f"profile:{PROFILE_CHAR_1}")
    unit_1 = stable_id("su", f"segment:{SEG_1}")
    unit_2 = stable_id("su", f"segment:{SEG_2}")
    unit_4 = stable_id("su", f"segment:{SEG_4}")

    assert _scalar(legacy, "SELECT COUNT(*) FROM book_bibles") == 2
    assert _scalar(legacy, "SELECT COUNT(*) FROM book_bibles WHERE id = ?", (bible_id,)) == 1
    assert _scalar(legacy, "SELECT COUNT(*) FROM book_bibles WHERE id = ?", (bible_c_id,)) == 1
    assert _scalar(legacy, "SELECT COUNT(*) FROM character_arcs") == 3
    assert _scalar(legacy, "SELECT COUNT(*) FROM scenes") == 2
    assert _scalar(legacy, "SELECT COUNT(*) FROM scene_characters") == 2
    assert _scalar(legacy, "SELECT COUNT(*) FROM voice_actors") == 2
    assert _scalar(legacy, "SELECT COUNT(*) FROM voice_casts") == 2
    assert _scalar(legacy, "SELECT COUNT(*) FROM script_versions") == 2
    assert _scalar(legacy, "SELECT COUNT(*) FROM script_units") == 5
    assert _scalar(legacy, "SELECT COUNT(*) FROM review_items") == 2

    assert _scalar(legacy, "SELECT bible_status FROM books WHERE id = ?", (BOOK_A,)) == "approved"
    assert _scalar(legacy, "SELECT production_status FROM books WHERE id = ?", (BOOK_A,)) == "ready"
    assert _scalar(legacy, "SELECT bible_status FROM books WHERE id = ?", (BOOK_B,)) == "not_started"
    assert _scalar(legacy, "SELECT bible_status FROM books WHERE id = ?", (BOOK_C,)) == "approved"
    assert _scalar(legacy, "SELECT production_status FROM books WHERE id = ?", (BOOK_C,)) == "ready"
    assert _scalar(legacy, "SELECT scene_version FROM chapters WHERE id = ?", (CHAPTER_A,)) == 1
    assert _scalar(legacy, "SELECT scene_status FROM chapters WHERE id = ?", (CHAPTER_A,)) == "completed"
    assert _scalar(legacy, "SELECT script_status FROM chapters WHERE id = ?", (CHAPTER_A,)) == "completed"
    assert _scalar(legacy, "SELECT script_status FROM chapters WHERE id = ?", (CHAPTER_C,)) == "completed"

    with sqlite3.connect(legacy.db_path) as connection:
        connection.row_factory = sqlite3.Row
        unit = connection.execute(
            "SELECT * FROM script_units WHERE id = ?", (unit_2,)
        ).fetchone()
        assert unit is not None
        assert unit["book_id"] == BOOK_A
        assert unit["chapter_id"] == CHAPTER_A
        assert unit["script_version_id"] == version_id
        assert unit["scene_id"] == scene_id
        assert unit["unit_type"] == "dialogue"
        assert unit["speaker_type"] == "character"
        assert unit["speaker_id"] == CHAR_1
        assert unit["voice_cast_id"] == character_cast
        assert unit["source_text"] == "「走吧。」"
        assert unit["text"] == "走吧。"
        assert unit["emotion"] == "determined"
        assert unit["intensity"] == 2
        assert unit["delivery"] == "slow"
        assert unit["review_status"] == "approved"
        assert unit["audio_status"] == "ready"
        assert unit["source_start_offset"] == 100
        assert unit["source_end_offset"] == 200

        narrator_unit = connection.execute(
            "SELECT speaker_type, voice_cast_id FROM script_units WHERE id = ?",
            (unit_1,),
        ).fetchone()
        assert narrator_unit["speaker_type"] == "narrator"
        assert narrator_unit["voice_cast_id"] == narrator_cast

        fallback_unit = connection.execute(
            "SELECT source_start_offset, source_end_offset, review_status, speaker_id "
            "FROM script_units WHERE id = ?",
            (unit_4,),
        ).fetchone()
        assert fallback_unit["source_start_offset"] == 0
        assert fallback_unit["source_end_offset"] == 1000
        assert fallback_unit["review_status"] == "needs_review"
        assert fallback_unit["speaker_id"] == CHAR_1

        assert connection.execute(
            "SELECT COUNT(*) FROM segments WHERE migrated_script_unit_id IS NOT NULL"
        ).fetchone()[0] == 5
        assert connection.execute(
            "SELECT migrated_script_unit_id FROM segments WHERE id = ?", (SEG_1,)
        ).fetchone()[0] == unit_1

        assert connection.execute(
            "SELECT voice_actor_id FROM audio_assets WHERE id = ?", (AUDIO_1,)
        ).fetchone()[0] == narrator_actor
        assert connection.execute(
            "SELECT voice_actor_id FROM audio_assets WHERE id = ?", (AUDIO_2,)
        ).fetchone()[0] == character_actor
        assert connection.execute(
            "SELECT COUNT(*) FROM audio_assets WHERE script_unit_id IS NOT NULL"
        ).fetchone()[0] == 2

        assert connection.execute(
            "SELECT migrated_voice_actor_id FROM voice_profiles WHERE id = ?",
            (PROFILE_NARRATOR,),
        ).fetchone()[0] == narrator_actor
        assert connection.execute(
            "SELECT migrated_voice_actor_id FROM voice_profiles WHERE id = ?",
            (PROFILE_MISSING,),
        ).fetchone()[0] is None
        assert connection.execute(
            "SELECT migrated_voice_actor_id FROM voice_profiles WHERE id = ?",
            (PROFILE_UNSELECTED,),
        ).fetchone()[0] is None

        bible = connection.execute(
            "SELECT content_json, source, status FROM book_bibles WHERE id = ?",
            (bible_id,),
        ).fetchone()
        content = json.loads(bible["content_json"])
        assert bible["source"] == "legacy"
        assert bible["status"] == "approved"
        assert {item["id"] for item in content["characters"]} == {CHAR_1, CHAR_2}
        assert len(content["relationships"]) == 1
        assert content["relationships"][0]["relationship_type"] == "friend"

        assert connection.execute(
            "SELECT COUNT(*) FROM character_arcs WHERE character_id = ?",
            (CHAR_MERGED,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT age_stage FROM character_arcs WHERE character_id = ?",
            (CHAR_1,),
        ).fetchone()[0] == "young_adult"
        assert connection.execute(
            "SELECT COUNT(*) FROM scene_characters WHERE character_id = ?",
            (CHAR_2,),
        ).fetchone()[0] == 1

        review = connection.execute(
            "SELECT entity_id, severity, status FROM review_items WHERE book_id = ?",
            (BOOK_A,),
        ).fetchone()
        assert review["entity_id"] == bible_id
        assert review["severity"] == "info"
        assert review["status"] == "open"

    copied = legacy.data_dir / "voice_actors" / narrator_actor / "reference.wav"
    assert copied.is_file()
    source = legacy.data_dir / "books" / BOOK_A / "voices" / "narrator.wav"
    assert copied.read_bytes() == source.read_bytes()
    assert source.is_file()
    assert (legacy.data_dir / "books" / BOOK_A / "audio" / "s1.wav").is_file()

    reports = list((legacy.data_dir / "logs").glob("migration-v05-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["revision"] == NEW_REVISION
    assert report["book_bibles_created"] == 2
    assert report["character_arcs_created"] == 3
    assert report["scenes_created"] == 2
    assert report["script_versions_created"] == 2
    assert report["script_units_created"] == 5
    assert report["voice_actors_created"] == 2
    assert report["audio_assets_mapped"] == 2
    assert report["review_items_created"] == 2
    warning_codes = {item["code"] for item in report["warnings"]}
    assert "voice_reference_missing" in warning_codes
    assert report["skipped"] == 1


def test_v05_backfill_is_idempotent(tmp_path: Path) -> None:
    legacy = _legacy_settings(tmp_path)
    _seed_legacy(legacy)
    command.upgrade(build_alembic_config(legacy), "head")

    before = {
        table: _scalar(legacy, f"SELECT COUNT(*) FROM {table}")
        for table in (
            "book_bibles",
            "character_arcs",
            "scenes",
            "scene_characters",
            "voice_actors",
            "voice_casts",
            "script_versions",
            "script_units",
            "review_items",
        )
    }
    stats = _run_backfill(legacy)
    for table, count in before.items():
        assert _scalar(legacy, f"SELECT COUNT(*) FROM {table}") == count
    for key in (
        "book_bibles_created",
        "character_arcs_created",
        "scenes_created",
        "scene_characters_created",
        "voice_actors_created",
        "voice_casts_created",
        "script_versions_created",
        "script_units_created",
        "audio_assets_mapped",
        "review_items_created",
        "segments_mapped",
    ):
        assert stats[key] == 0, key


def test_audio_asset_owner_and_partial_unique_constraints(tmp_path: Path) -> None:
    settings = _legacy_settings(tmp_path)
    command.upgrade(build_alembic_config(settings), "head")

    with sqlite3.connect(settings.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO audio_assets (
                    id, segment_id, script_unit_id, version, file_path, format,
                    sample_rate, channels, sample_width_bits, duration_ms,
                    file_size_bytes, generation_params_json, is_valid, created_at
                ) VALUES (
                    'audi_9999999999999999', NULL, NULL, 1, 'x.wav', 'wav',
                    24000, 1, 16, 10, 1, '{}', 1, CURRENT_TIMESTAMP
                )
                """
            )
        connection.rollback()

    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            """
            INSERT INTO audio_assets (
                id, segment_id, script_unit_id, version, file_path, format,
                sample_rate, channels, sample_width_bits, duration_ms,
                file_size_bytes, generation_params_json, is_valid, created_at
            ) VALUES (
                'audi_9999999999999998', NULL, 'su_1111111111111111', 1,
                'x.wav', 'wav', 24000, 1, 16, 10, 1, '{}', 1, CURRENT_TIMESTAMP
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO audio_assets (
                    id, segment_id, script_unit_id, version, file_path, format,
                    sample_rate, channels, sample_width_bits, duration_ms,
                    file_size_bytes, generation_params_json, is_valid, created_at
                ) VALUES (
                    'audi_9999999999999997', NULL, 'su_1111111111111111', 1,
                    'y.wav', 'wav', 24000, 1, 16, 10, 1, '{}', 1, CURRENT_TIMESTAMP
                )
                """
            )


def test_v05_downgrade_restores_old_columns_and_keeps_files(tmp_path: Path) -> None:
    legacy = _legacy_settings(tmp_path)
    _seed_legacy(legacy)
    command.upgrade(build_alembic_config(legacy), "head")
    command.downgrade(build_alembic_config(legacy), OLD_REVISION)
    assert current_revision(legacy.db_path) == OLD_REVISION

    with sqlite3.connect(legacy.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        audio_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('audio_assets')")
        }
        book_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('books')")
        }
        assert "book_bibles" not in tables
        assert "script_units" not in tables
        assert "review_items" not in tables
        assert "script_unit_id" not in audio_columns
        assert "voice_actor_id" not in audio_columns
        assert "bible_status" not in book_columns
        assert connection.execute("SELECT COUNT(*) FROM audio_assets").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM voice_profiles"
        ).fetchone()[0] == 4

    narrator_actor = stable_id("va", f"profile:{PROFILE_NARRATOR}")
    assert (
        legacy.data_dir / "voice_actors" / narrator_actor / "reference.wav"
    ).is_file()
    assert (legacy.data_dir / "books" / BOOK_A / "voices" / "narrator.wav").is_file()