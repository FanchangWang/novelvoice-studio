"""stage 2 analysis review

Revision ID: 0790ed4240ab
Revises: 1b53be94da51
Create Date: 2026-11-25 10:08:27.180898

阶段 1 没有写入角色、关系、片段的功能，因此这三张表在升级时重建，避免
SQLite 无法按名称删除匿名外键/检查约束的问题。书籍、章节、任务与其他表
均保留原数据。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from novelvoice_studio.db.types import UTCDateTime

revision: str = "0790ed4240ab"
down_revision: str | None = "1b53be94da51"
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
_EMOTION_SQL = "(" + ",".join(f"'{item}'" for item in EMOTIONS) + ")"
_DELIVERY_SQL = "(" + ",".join(f"'{item}'" for item in DELIVERIES) + ")"


def upgrade() -> None:
    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "summary_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "analysis_meta_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("progress", sa.Float(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column(
                "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
            )
        )
        batch_op.add_column(
            sa.Column(
                "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("2")
            )
        )
        batch_op.add_column(sa.Column("error_code", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "updated_at",
                UTCDateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )

    # 阶段 1 不存在这些表的业务数据；先删除子表，再重建。
    op.drop_table("character_relationships")
    op.drop_table("segments")
    op.drop_table("characters")

    op.create_table(
        "characters",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("canonical_name", sa.String(length=120), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("alias_candidates_json", sa.JSON(), nullable=False),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("gender_basis", sa.String(length=16), nullable=False),
        sa.Column("age_range", sa.String(length=24), nullable=False),
        sa.Column("age_basis", sa.String(length=16), nullable=False),
        sa.Column("character_type", sa.String(length=16), nullable=False),
        sa.Column("identity_description", sa.Text(), nullable=True),
        sa.Column("personality_json", sa.JSON(), nullable=False),
        sa.Column("personality_desc", sa.Text(), nullable=True),
        sa.Column("appearance_desc", sa.Text(), nullable=True),
        sa.Column("voice_description", sa.Text(), nullable=True),
        sa.Column("first_chapter_id", sa.String(length=32), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("user_locked", sa.Boolean(), nullable=False),
        sa.Column("merged_into_id", sa.String(length=32), nullable=True),
        sa.Column("merge_log_json", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "gender IN ('male','female','other','unknown')",
            name="ck_characters_gender",
        ),
        sa.CheckConstraint(
            "gender_basis IN ('explicit','inferred','unknown')",
            name="ck_characters_gender_basis",
        ),
        sa.CheckConstraint(
            "age_range IN ('child','teenager','young_adult','adult','middle_aged','elderly','unknown')",
            name="ck_characters_age_range",
        ),
        sa.CheckConstraint(
            "age_basis IN ('explicit','inferred','unknown')",
            name="ck_characters_age_basis",
        ),
        sa.CheckConstraint(
            "character_type IN ('human','animal','non_human','unknown')",
            name="ck_characters_type",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_characters_confidence"
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.create_index("ix_characters_book_id", ["book_id"], unique=False)
        batch_op.create_index(
            "ix_characters_book_name", ["book_id", "canonical_name"], unique=False
        )
        batch_op.create_index("ix_characters_merged_into_id", ["merged_into_id"], unique=False)

    op.create_table(
        "character_relationships",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("source_character_id", sa.String(length=32), nullable=False),
        sa.Column("target_character_id", sa.String(length=32), nullable=False),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "source_character_id != target_character_id",
            name="ck_relationship_distinct",
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_character_id"], ["characters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_character_id"], ["characters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "book_id",
            "source_character_id",
            "target_character_id",
            name="uq_relationship_pair",
        ),
    )
    with op.batch_alter_table("character_relationships", schema=None) as batch_op:
        batch_op.create_index(
            "ix_character_relationships_book_id", ["book_id"], unique=False
        )

    op.create_table(
        "segments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("speech_text", sa.Text(), nullable=False),
        sa.Column("segment_type", sa.String(length=24), nullable=False),
        sa.Column("speaker_type", sa.String(length=16), nullable=False),
        sa.Column("speaker_id", sa.String(length=32), nullable=True),
        sa.Column("speaker_name_snapshot", sa.String(length=120), nullable=True),
        sa.Column("speaker_confidence", sa.Float(), nullable=True),
        sa.Column("voice_profile_id", sa.String(length=32), nullable=True),
        sa.Column("emotion", sa.String(length=32), nullable=False),
        sa.Column("emotion_confidence", sa.Float(), nullable=True),
        sa.Column("intensity", sa.Integer(), nullable=False),
        sa.Column("delivery", sa.String(length=24), nullable=False),
        sa.Column("tts_instruction", sa.Text(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("pause_before_ms", sa.Integer(), nullable=False),
        sa.Column("pause_after_ms", sa.Integer(), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("audio_status", sa.String(length=16), nullable=False),
        sa.Column("current_audio_asset_id", sa.String(length=32), nullable=True),
        sa.Column("user_locked_fields_json", sa.JSON(), nullable=False),
        sa.Column("text_version", sa.Integer(), nullable=False),
        sa.Column("source_start_offset", sa.Integer(), nullable=True),
        sa.Column("source_end_offset", sa.Integer(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "segment_type IN ('narration','dialogue','inner_monologue','unknown')",
            name="ck_segments_type",
        ),
        sa.CheckConstraint(
            "speaker_type IN ('character','narrator','unknown')",
            name="ck_segments_speaker_type",
        ),
        sa.CheckConstraint(
            "audio_status IN ('none','stale','queued','generating','ready','failed')",
            name="ck_segments_audio_status",
        ),
        sa.CheckConstraint(
            "review_status IN ('auto','needs_review','approved')",
            name="ck_segments_review_status",
        ),
        sa.CheckConstraint(f"emotion IN {_EMOTION_SQL}", name="ck_segments_emotion"),
        sa.CheckConstraint(f"delivery IN {_DELIVERY_SQL}", name="ck_segments_delivery"),
        sa.CheckConstraint(
            "intensity >= 1 AND intensity <= 3", name="ck_segments_intensity"
        ),
        sa.CheckConstraint("speed > 0 AND speed <= 3", name="ck_segments_speed"),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chapter_id", "segment_index", name="uq_segments_chapter_index"),
    )
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.create_index("ix_segments_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_segments_chapter_id", ["chapter_id"], unique=False)
        batch_op.create_index("ix_segments_speaker", ["speaker_type", "speaker_id"], unique=False)
        batch_op.create_index("ix_segments_book_review", ["book_id", "review_status"], unique=False)


def downgrade() -> None:
    op.drop_table("segments")
    op.drop_table("character_relationships")
    op.drop_table("characters")

    op.create_table(
        "characters",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("age_group", sa.String(length=16), nullable=False),
        sa.Column("personality_json", sa.JSON(), nullable=False),
        sa.Column("personality_desc", sa.Text(), nullable=True),
        sa.Column("role_desc", sa.Text(), nullable=True),
        sa.Column("appearance_desc", sa.Text(), nullable=True),
        sa.Column("voice_style", sa.Text(), nullable=True),
        sa.Column("first_chapter_id", sa.String(length=32), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("user_locked", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "gender IN ('male','female','other','unknown')",
            name="ck_characters_gender",
        ),
        sa.CheckConstraint(
            "age_group IN ('child','teen','young','middle','senior','unknown')",
            name="ck_characters_age_group",
        ),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("characters", schema=None) as batch_op:
        batch_op.create_index("ix_characters_book_id", ["book_id"], unique=False)
        batch_op.create_index("ix_characters_book_name", ["book_id", "name"], unique=False)

    op.create_table(
        "character_relationships",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("character_a_id", sa.String(length=32), nullable=False),
        sa.Column("character_b_id", sa.String(length=32), nullable=False),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["book_id"], ["books.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["character_a_id"], ["characters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["character_b_id"], ["characters.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "book_id",
            "character_a_id",
            "character_b_id",
            name="uq_relationship_pair",
        ),
    )
    with op.batch_alter_table("character_relationships", schema=None) as batch_op:
        batch_op.create_index(
            "ix_character_relationships_book_id", ["book_id"], unique=False
        )

    op.create_table(
        "segments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chapter_id", sa.String(length=32), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("speaker_type", sa.String(length=16), nullable=False),
        sa.Column("speaker_id", sa.String(length=32), nullable=True),
        sa.Column("speaker_name_snapshot", sa.String(length=120), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_version", sa.Integer(), nullable=False),
        sa.Column("voice_profile_id", sa.String(length=32), nullable=True),
        sa.Column("emotion", sa.String(length=32), nullable=True),
        sa.Column("emotion_intensity", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("pause_before_ms", sa.Integer(), nullable=False),
        sa.Column("pause_after_ms", sa.Integer(), nullable=False),
        sa.Column("audio_status", sa.String(length=16), nullable=False),
        sa.Column("current_audio_asset_id", sa.String(length=32), nullable=True),
        sa.Column("user_locked_fields_json", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "audio_status IN ('none','stale','queued','generating','ready','failed')",
            name="ck_segments_audio_status",
        ),
        sa.CheckConstraint(
            "speaker_type IN ('character','narrator','unknown')",
            name="ck_segments_speaker_type",
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chapter_id", "segment_index", name="uq_segments_chapter_index"),
    )
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.create_index("ix_segments_chapter_id", ["chapter_id"], unique=False)
        batch_op.create_index("ix_segments_speaker", ["speaker_type", "speaker_id"], unique=False)

    with op.batch_alter_table("generation_jobs", schema=None) as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("error_code")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("progress")
        batch_op.drop_column("target_id")

    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_column("analysis_meta_json")
        batch_op.drop_column("summary_json")