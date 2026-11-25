"""v0.5.0 AI production pipeline schema

Revision ID: 9f3c2a7b5d10
Revises: 5c1d9a7b3e2f
Create Date: 2026-11-26 10:00:00.000000

新增角色圣经、人物弧、分场状态、有声剧本、全局配音员、选角与异常收件箱，
并把旧版角色/片段/音色/音频资产回填到新结构。旧表与旧音频文件保留，
只增加迁移列，不删除历史 WAV。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from alembic import op
import sqlalchemy as sa

from novelvoice_studio.db.types import UTCDateTime
from novelvoice_studio.db.v05_backfill import backfill_v05

revision: str = "9f3c2a7b5d10"
down_revision: str | None = "5c1d9a7b3e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMOTIONS = (
    "neutral",
    "happy",
    "excited",
    "angry",
    "sad",
    "fearful",
    "surprised",
    "doubtful",
    "sarcastic",
    "tender",
    "embarrassed",
    "determined",
    "tired",
)
DELIVERIES = (
    "normal",
    "whispering",
    "shouting",
    "crying",
    "laughing",
    "breathless",
    "slow",
    "hesitant",
)
AGE_STAGES = (
    "child",
    "teenager",
    "young_adult",
    "adult",
    "middle_aged",
    "elderly",
    "unknown",
)
_EMOTION_SQL = "(" + ",".join(f"'{item}'" for item in EMOTIONS) + ")"
_DELIVERY_SQL = "(" + ",".join(f"'{item}'" for item in DELIVERIES) + ")"
_AGE_SQL = "(" + ",".join(f"'{item}'" for item in AGE_STAGES) + ")"
_SOURCE_SQL = "('ai','legacy','user','system')"
_TEXT_UNITS_SQL = "('narration','dialogue','inner_monologue')"
_CUE_UNITS_SQL = "('sfx','ambience','pause')"


def _data_dir() -> Path:
    database = op.get_bind().engine.url.database
    if database and database != ":memory:":
        return Path(database).resolve().parent
    return Path.cwd()


def _write_report(stats: dict[str, object], data_dir: Path) -> None:
    logs_dir = data_dir / "logs"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = logs_dir / f"migration-v05-{stamp}.json"
        target.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # 报告只是审计辅助，不应阻止迁移完成。
        pass


def upgrade() -> None:
    # 1. 旧表增列。SQLite 通过 batch 重建，保留原有数据。
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "bible_status",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'not_started'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "production_status",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'not_started'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "meta_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_books_bible_status",
            "bible_status IN ('not_started','running','needs_review','approved','failed')",
        )
        batch_op.create_check_constraint(
            "ck_books_production_status",
            "production_status IN "
            "('not_started','analyzing','scripting','casting','needs_review','ready','generating','failed')",
        )

    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scene_status",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'not_started'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "scene_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "script_status",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'not_started'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_chapters_scene_status",
            "scene_status IN ('not_started','running','completed','needs_review','failed')",
        )
        batch_op.create_check_constraint(
            "ck_chapters_script_status",
            "script_status IN ('not_started','running','completed','needs_review','failed')",
        )

    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "role_tier",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'unknown'"),
            )
        )
        batch_op.add_column(sa.Column("arc_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("story_time_start", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("story_time_end", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("story_order_start", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("story_order_end", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'legacy'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_characters_role_tier",
            "role_tier IN ('protagonist','major','minor','extra','unknown')",
        )
        batch_op.create_check_constraint(
            "ck_characters_source", "source IN ('ai','legacy','user')"
        )

    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("migrated_script_unit_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_index(
            "ix_segments_migrated_script_unit_id",
            ["migrated_script_unit_id"],
            unique=False,
        )

    with op.batch_alter_table("voice_profiles", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("migrated_voice_actor_id", sa.String(length=32), nullable=True)
        )
        batch_op.create_index(
            "uq_voice_profiles_migrated_actor",
            ["migrated_voice_actor_id"],
            unique=True,
            sqlite_where=sa.text("migrated_voice_actor_id IS NOT NULL"),
        )

    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_type", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("stage", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("parent_job_id", sa.String(length=32), nullable=True))
        batch_op.create_index(
            "ix_generation_jobs_parent_job_id", ["parent_job_id"], unique=False
        )

    # 2. 新表。按外键依赖顺序创建。
    op.create_table(
        "book_bibles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ai'"),
        ),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "content_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "timeline_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("approved_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','running','needs_review','approved','superseded','failed')",
            name="ck_book_bibles_status",
        ),
        sa.CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_book_bibles_source"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_book_bibles_confidence"
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("book_id", "version", name="uq_book_bibles_book_version"),
    )
    with op.batch_alter_table("book_bibles", schema=None) as batch_op:
        batch_op.create_index("ix_book_bibles_book_id", ["book_id"], unique=False)
        batch_op.create_index(
            "uq_book_bibles_active",
            ["book_id"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
        )

    op.create_table(
        "character_arcs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("bible_id", sa.String(length=32), nullable=False),
        sa.Column("character_id", sa.String(length=32), nullable=False),
        sa.Column("arc_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("age_stage", sa.String(length=24), nullable=False),
        sa.Column("chapter_start_id", sa.String(length=32), nullable=True),
        sa.Column("chapter_end_id", sa.String(length=32), nullable=True),
        sa.Column("story_time_start", sa.String(length=120), nullable=True),
        sa.Column("story_time_end", sa.String(length=120), nullable=True),
        sa.Column("story_order_start", sa.Integer(), nullable=True),
        sa.Column("story_order_end", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("traits_json", sa.JSON(), nullable=False),
        sa.Column("appearance_json", sa.JSON(), nullable=False),
        sa.Column("voice_traits_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(f"age_stage IN {_AGE_SQL}", name="ck_character_arcs_age_stage"),
        sa.CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_character_arcs_source"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_character_arcs_confidence"
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bible_id"], ["book_bibles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_start_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chapter_end_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bible_id",
            "character_id",
            "arc_index",
            name="uq_character_arcs_bible_character_index",
        ),
    )
    with op.batch_alter_table("character_arcs", schema=None) as batch_op:
        batch_op.create_index("ix_character_arcs_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_character_arcs_bible_id", ["bible_id"], unique=False)
        batch_op.create_index(
            "ix_character_arcs_character_index",
            ["character_id", "arc_index"],
            unique=False,
        )

    op.create_table(
        "voice_actors",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("gender_presentation", sa.String(length=16), nullable=False),
        sa.Column("age_impression", sa.String(length=24), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("timbre_tags_json", sa.JSON(), nullable=False),
        sa.Column("personality_tags_json", sa.JSON(), nullable=False),
        sa.Column("reference_text", sa.Text(), nullable=True),
        sa.Column("reference_audio_path", sa.Text(), nullable=True),
        sa.Column("instruct_text", sa.Text(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("generation_params_json", sa.JSON(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_book_id", sa.String(length=32), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False),
        sa.Column("legacy_voice_profile_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "gender_presentation IN ('male','female','neutral','unknown')",
            name="ck_voice_actors_gender",
        ),
        sa.CheckConstraint(f"age_impression IN {_AGE_SQL}", name="ck_voice_actors_age"),
        sa.CheckConstraint(
            "status IN ('draft','ready','failed','archived')",
            name="ck_voice_actors_status",
        ),
        sa.CheckConstraint(
            "source IN ('ai','legacy','user')", name="ck_voice_actors_source"
        ),
        sa.ForeignKeyConstraint(["source_book_id"], ["books.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("voice_actors", schema=None) as batch_op:
        batch_op.create_index(
            "uq_voice_actors_legacy_profile",
            ["legacy_voice_profile_id"],
            unique=True,
            sqlite_where=sa.text("legacy_voice_profile_id IS NOT NULL"),
        )
        batch_op.create_index(
            "ix_voice_actors_status_language",
            ["status", "language"],
            unique=False,
        )
        batch_op.create_index(
            "ix_voice_actors_age_gender",
            ["age_impression", "gender_presentation"],
            unique=False,
        )

    op.create_table(
        "voice_casts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("character_id", sa.String(length=32), nullable=True),
        sa.Column("voice_actor_id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("age_stage_condition", sa.String(length=24), nullable=True),
        sa.Column("state_condition", sa.String(length=255), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("legacy_voice_profile_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "scope IN ('narrator','character','character_stage')",
            name="ck_voice_casts_scope",
        ),
        sa.CheckConstraint(
            "source IN ('auto','manual','legacy')", name="ck_voice_casts_source"
        ),
        sa.CheckConstraint(
            "(scope = 'narrator' AND character_id IS NULL AND age_stage_condition IS NULL) "
            "OR (scope = 'character' AND character_id IS NOT NULL AND age_stage_condition IS NULL) "
            "OR (scope = 'character_stage' AND character_id IS NOT NULL AND age_stage_condition IS NOT NULL)",
            name="ck_voice_casts_owner",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_voice_casts_confidence"
        ),
        sa.CheckConstraint(
            f"age_stage_condition IS NULL OR age_stage_condition IN {_AGE_SQL}",
            name="ck_voice_casts_age_condition",
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["voice_actor_id"], ["voice_actors.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("voice_casts", schema=None) as batch_op:
        batch_op.create_index("ix_voice_casts_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_voice_casts_character_id", ["character_id"], unique=False)
        batch_op.create_index(
            "ix_voice_casts_voice_actor_id", ["voice_actor_id"], unique=False
        )
        batch_op.create_index(
            "uq_voice_casts_narrator_active",
            ["book_id"],
            unique=True,
            sqlite_where=sa.text("scope = 'narrator' AND is_active = 1"),
        )
        batch_op.create_index(
            "uq_voice_casts_character_active",
            ["book_id", "character_id"],
            unique=True,
            sqlite_where=sa.text("scope = 'character' AND is_active = 1"),
        )
        batch_op.create_index(
            "uq_voice_casts_stage_active",
            ["book_id", "character_id", "age_stage_condition"],
            unique=True,
            sqlite_where=sa.text("scope = 'character_stage' AND is_active = 1"),
        )

    op.create_table(
        "scenes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("scene_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("story_time_label", sa.String(length=120), nullable=True),
        sa.Column("story_order", sa.Integer(), nullable=True),
        sa.Column("time_mode", sa.String(length=24), nullable=False),
        sa.Column("pov_character_id", sa.String(length=32), nullable=True),
        sa.Column("mood", sa.String(length=64), nullable=True),
        sa.Column("source_start_offset", sa.Integer(), nullable=False),
        sa.Column("source_end_offset", sa.Integer(), nullable=False),
        sa.Column("analysis_status", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "time_mode IN ('present','flashback','flashforward','dream','unknown')",
            name="ck_scenes_time_mode",
        ),
        sa.CheckConstraint(
            "analysis_status IN ('draft','running','completed','needs_review','failed')",
            name="ck_scenes_status",
        ),
        sa.CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_scenes_source"),
        sa.CheckConstraint(
            "source_start_offset >= 0 AND source_end_offset > source_start_offset",
            name="ck_scenes_offsets",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_scenes_confidence"
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["pov_character_id"], ["characters.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chapter_id",
            "analysis_version",
            "scene_index",
            name="uq_scenes_chapter_version_index",
        ),
    )
    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.create_index("ix_scenes_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_scenes_chapter_id", ["chapter_id"], unique=False)

    op.create_table(
        "scene_characters",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("scene_id", sa.String(length=32), nullable=False),
        sa.Column("character_id", sa.String(length=32), nullable=False),
        sa.Column("state_phase", sa.Integer(), nullable=False),
        sa.Column("presence", sa.String(length=16), nullable=False),
        sa.Column("age_stage", sa.String(length=24), nullable=False),
        sa.Column("physical_state", sa.String(length=255), nullable=True),
        sa.Column("emotional_state", sa.String(length=255), nullable=True),
        sa.Column("relationship_state", sa.String(length=255), nullable=True),
        sa.Column("knowledge_state", sa.Text(), nullable=True),
        sa.Column("disguise_state", sa.String(length=255), nullable=True),
        sa.Column("voice_note", sa.String(length=255), nullable=True),
        sa.Column("state_tags_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "presence IN ('speaker','present','mentioned')",
            name="ck_scene_characters_presence",
        ),
        sa.CheckConstraint(
            f"age_stage IN {_AGE_SQL}", name="ck_scene_characters_age_stage"
        ),
        sa.CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_scene_characters_source"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_scene_characters_confidence",
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scene_id",
            "character_id",
            "state_phase",
            name="uq_scene_characters_scene_character_phase",
        ),
    )
    with op.batch_alter_table("scene_characters", schema=None) as batch_op:
        batch_op.create_index("ix_scene_characters_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_scene_characters_scene_id", ["scene_id"], unique=False)
        batch_op.create_index(
            "ix_scene_characters_character_id", ["character_id"], unique=False
        )

    op.create_table(
        "script_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("adaptation_level", sa.String(length=16), nullable=False),
        sa.Column("source_scene_version", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("approved_at", UTCDateTime(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','running','needs_review','approved','superseded','failed')",
            name="ck_script_versions_status",
        ),
        sa.CheckConstraint(
            "adaptation_level IN ('light','standard','strong','legacy')",
            name="ck_script_versions_adaptation",
        ),
        sa.CheckConstraint(
            "source IN ('ai','legacy','user')", name="ck_script_versions_source"
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chapter_id", "version", name="uq_script_versions_chapter_version"
        ),
    )
    with op.batch_alter_table("script_versions", schema=None) as batch_op:
        batch_op.create_index("ix_script_versions_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_script_versions_chapter_id", ["chapter_id"], unique=False)
        batch_op.create_index(
            "uq_script_versions_active",
            ["chapter_id"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
        )

    op.create_table(
        "script_units",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("script_version_id", sa.String(length=32), nullable=False),
        sa.Column("scene_id", sa.String(length=32), nullable=True),
        sa.Column("unit_index", sa.Integer(), nullable=False),
        sa.Column("unit_type", sa.String(length=24), nullable=False),
        sa.Column("speaker_type", sa.String(length=16), nullable=False),
        sa.Column("speaker_id", sa.String(length=32), nullable=True),
        sa.Column("speaker_name_snapshot", sa.String(length=120), nullable=True),
        sa.Column("character_state_id", sa.String(length=32), nullable=True),
        sa.Column("voice_cast_id", sa.String(length=32), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=True),
        sa.Column("source_start_offset", sa.Integer(), nullable=False),
        sa.Column("source_end_offset", sa.Integer(), nullable=False),
        sa.Column("emotion", sa.String(length=32), nullable=False),
        sa.Column("intensity", sa.Integer(), nullable=False),
        sa.Column("delivery", sa.String(length=24), nullable=False),
        sa.Column("tts_instruction", sa.Text(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("pause_before_ms", sa.Integer(), nullable=False),
        sa.Column("pause_after_ms", sa.Integer(), nullable=False),
        sa.Column("sfx_json", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("audio_status", sa.String(length=24), nullable=False),
        sa.Column("current_audio_asset_id", sa.String(length=32), nullable=True),
        sa.Column("render_hash", sa.String(length=64), nullable=True),
        sa.Column("user_locked_fields_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "unit_type IN ('narration','dialogue','inner_monologue','sfx','ambience','pause','unknown')",
            name="ck_script_units_type",
        ),
        sa.CheckConstraint(
            "speaker_type IN ('narrator','character','unknown','none')",
            name="ck_script_units_speaker_type",
        ),
        sa.CheckConstraint(
            "review_status IN ('auto','needs_review','approved')",
            name="ck_script_units_review_status",
        ),
        sa.CheckConstraint(
            "audio_status IN ('not_applicable','none','stale','queued','generating','ready','failed')",
            name="ck_script_units_audio_status",
        ),
        sa.CheckConstraint(f"emotion IN {_EMOTION_SQL}", name="ck_script_units_emotion"),
        sa.CheckConstraint(
            f"delivery IN {_DELIVERY_SQL}", name="ck_script_units_delivery"
        ),
        sa.CheckConstraint(
            "source IN ('ai','legacy','user')", name="ck_script_units_source"
        ),
        sa.CheckConstraint(
            "intensity >= 1 AND intensity <= 3", name="ck_script_units_intensity"
        ),
        sa.CheckConstraint(
            "speed > 0 AND speed <= 3", name="ck_script_units_speed"
        ),
        sa.CheckConstraint(
            "source_start_offset >= 0 AND source_end_offset > source_start_offset",
            name="ck_script_units_offsets",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_script_units_confidence"
        ),
        sa.CheckConstraint(
            f"unit_type NOT IN {_TEXT_UNITS_SQL} OR length(trim(text)) > 0",
            name="ck_script_units_tts_text",
        ),
        sa.CheckConstraint(
            f"unit_type NOT IN {_CUE_UNITS_SQL} OR audio_status = 'not_applicable'",
            name="ck_script_units_cue_audio",
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["script_version_id"], ["script_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["character_state_id"], ["scene_characters.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["voice_cast_id"], ["voice_casts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "script_version_id", "unit_index", name="uq_script_units_version_index"
        ),
    )
    with op.batch_alter_table("script_units", schema=None) as batch_op:
        batch_op.create_index("ix_script_units_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_script_units_chapter_id", ["chapter_id"], unique=False)
        batch_op.create_index("ix_script_units_scene_id", ["scene_id"], unique=False)
        batch_op.create_index("ix_script_units_speaker_id", ["speaker_id"], unique=False)
        batch_op.create_index(
            "ix_script_units_voice_cast_id", ["voice_cast_id"], unique=False
        )
        batch_op.create_index(
            "ix_script_units_version_type",
            ["script_version_id", "unit_type"],
            unique=False,
        )
        batch_op.create_index(
            "ix_script_units_book_audio", ["book_id", "audio_status"], unique=False
        )

    op.create_table(
        "review_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=32), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('blocking','warning','info')", name="ck_review_items_severity"
        ),
        sa.CheckConstraint(
            "status IN ('open','resolved','ignored')", name="ck_review_items_status"
        ),
        sa.CheckConstraint(f"source IN {_SOURCE_SQL}", name="ck_review_items_source"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("review_items", schema=None) as batch_op:
        batch_op.create_index("ix_review_items_book_id", ["book_id"], unique=False)
        batch_op.create_index(
            "ix_review_items_book_status", ["book_id", "status", "severity"], unique=False
        )
        batch_op.create_index(
            "ix_review_items_entity", ["entity_type", "entity_id"], unique=False
        )
        batch_op.create_index(
            "uq_review_items_open",
            ["book_id", "entity_type", "entity_id", "code"],
            unique=True,
            sqlite_where=sa.text("status = 'open'"),
        )

    # 3. 音频资产允许挂到剧本单元，并记录全局配音员。
    with op.batch_alter_table("audio_assets", schema=None, recreate="always") as batch_op:
        batch_op.alter_column(
            "segment_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("script_unit_id", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("voice_actor_id", sa.String(length=32), nullable=True)
        )
        batch_op.drop_constraint(
            "uq_audio_assets_segment_version", type_="unique"
        )
        batch_op.create_foreign_key(
            "fk_audio_assets_script_unit_id",
            "script_units",
            ["script_unit_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_audio_assets_voice_actor_id",
            "voice_actors",
            ["voice_actor_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_audio_assets_owner",
            "segment_id IS NOT NULL OR script_unit_id IS NOT NULL",
        )
    op.create_index(
        "uq_audio_assets_segment_version",
        "audio_assets",
        ["segment_id", "version"],
        unique=True,
        sqlite_where=sa.text("segment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_audio_assets_script_unit_version",
        "audio_assets",
        ["script_unit_id", "version"],
        unique=True,
        sqlite_where=sa.text("script_unit_id IS NOT NULL"),
    )
    op.create_index(
        "ix_audio_assets_script_unit_id", "audio_assets", ["script_unit_id"], unique=False
    )
    op.create_index(
        "ix_audio_assets_voice_actor_id", "audio_assets", ["voice_actor_id"], unique=False
    )

    # 4. 回填旧数据并写迁移报告。
    data_dir = _data_dir()
    stats = backfill_v05(op.get_bind(), data_dir)
    stats["revision"] = revision
    stats["data_dir"] = str(data_dir)
    _write_report(stats, data_dir)


def downgrade() -> None:
    # 新音频资产可能只挂在剧本单元上；旧结构要求 segment_id 非空。
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM audio_assets "
            "WHERE segment_id IS NULL AND script_unit_id IS NOT NULL"
        )
    )
    op.drop_index("ix_audio_assets_voice_actor_id", table_name="audio_assets")
    op.drop_index("ix_audio_assets_script_unit_id", table_name="audio_assets")
    op.drop_index("uq_audio_assets_script_unit_version", table_name="audio_assets")
    op.drop_index("uq_audio_assets_segment_version", table_name="audio_assets")
    with op.batch_alter_table("audio_assets", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("ck_audio_assets_owner", type_="check")
        batch_op.drop_constraint(
            "fk_audio_assets_voice_actor_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_audio_assets_script_unit_id", type_="foreignkey"
        )
        batch_op.drop_column("voice_actor_id")
        batch_op.drop_column("script_unit_id")
        batch_op.alter_column(
            "segment_id",
            existing_type=sa.String(length=32),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_audio_assets_segment_version", ["segment_id", "version"]
        )

    op.drop_table("review_items")
    op.drop_table("script_units")
    op.drop_table("scene_characters")
    op.drop_table("script_versions")
    op.drop_table("scenes")
    op.drop_table("voice_casts")
    op.drop_table("character_arcs")
    op.drop_table("book_bibles")
    op.drop_table("voice_actors")

    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_generation_jobs_parent_job_id")
        batch_op.drop_column("parent_job_id")
        batch_op.drop_column("stage")
        batch_op.drop_column("target_type")

    with op.batch_alter_table("voice_profiles", schema=None) as batch_op:
        batch_op.drop_index("uq_voice_profiles_migrated_actor")
        batch_op.drop_column("migrated_voice_actor_id")

    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.drop_index("ix_segments_migrated_script_unit_id")
        batch_op.drop_column("migrated_script_unit_id")

    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.drop_constraint("ck_characters_source", type_="check")
        batch_op.drop_constraint("ck_characters_role_tier", type_="check")
        batch_op.drop_column("source")
        batch_op.drop_column("story_order_end")
        batch_op.drop_column("story_order_start")
        batch_op.drop_column("story_time_end")
        batch_op.drop_column("story_time_start")
        batch_op.drop_column("arc_summary")
        batch_op.drop_column("role_tier")

    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_constraint("ck_chapters_script_status", type_="check")
        batch_op.drop_constraint("ck_chapters_scene_status", type_="check")
        batch_op.drop_column("script_status")
        batch_op.drop_column("scene_version")
        batch_op.drop_column("scene_status")

    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.drop_constraint("ck_books_production_status", type_="check")
        batch_op.drop_constraint("ck_books_bible_status", type_="check")
        batch_op.drop_column("meta_json")
        batch_op.drop_column("production_status")
        batch_op.drop_column("bible_status")