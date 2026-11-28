"""新版产品流程数据模型

Revision ID: d4f6a2b9c810
Revises: c7e2f4a91d30
Create Date: 2026-11-27 16:00:00.000000

开发阶段直接采用新版结构：书籍数字 ID、章节未分类状态、配音员范围、
异常分类，以及剧本与分场版本解耦所需的可空字段。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d4f6a2b9c810"
down_revision: str | None = "c7e2f4a91d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "book_id_sequences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )
    op.execute(
        sa.text(
            "INSERT INTO book_id_sequences (id, next_value) "
            "SELECT 1, COALESCE(MAX(CAST(substr(id, 6) AS INTEGER)), 0) + 1 "
            "FROM books WHERE substr(id, 6) NOT GLOB '*[^0-9]*'"
        )
    )

    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_constraint("ck_chapters_kind", type_="check")
        batch_op.add_column(
            sa.Column(
                "classification_status",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'ai_suggested'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_chapters_kind",
            "chapter_kind IN ('story','extra','author_note','unclassified')",
        )
        batch_op.create_check_constraint(
            "ck_chapters_classification_status",
            "classification_status IN ('ai_suggested','confirmed','unclassified')",
        )

    with op.batch_alter_table("voice_actors", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'general'"),
            )
        )
    op.execute(
        sa.text(
            "UPDATE voice_actors SET scope = 'book' WHERE source_book_id IS NOT NULL"
        )
    )
    with op.batch_alter_table("voice_actors", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_voice_actors_scope", "scope IN ('general','book')"
        )
        batch_op.create_check_constraint(
            "ck_voice_actors_scope_owner",
            "(scope = 'general' AND source_book_id IS NULL) "
            "OR (scope = 'book' AND source_book_id IS NOT NULL)",
        )
    op.create_index(
        "ix_voice_actors_scope_status", "voice_actors", ["scope", "status"]
    )

    with op.batch_alter_table("review_items", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "category",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'content'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_review_items_category", "category IN ('content','system')"
        )

    with op.batch_alter_table("script_versions", schema=None) as batch_op:
        batch_op.alter_column(
            "source_scene_version",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("script_versions", schema=None) as batch_op:
        batch_op.alter_column(
            "source_scene_version",
            existing_type=sa.Integer(),
            nullable=False,
        )

    with op.batch_alter_table("review_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_review_items_category", type_="check")
        batch_op.drop_column("category")

    op.drop_index("ix_voice_actors_scope_status", table_name="voice_actors")
    with op.batch_alter_table("voice_actors", schema=None) as batch_op:
        batch_op.drop_constraint("ck_voice_actors_scope_owner", type_="check")
        batch_op.drop_constraint("ck_voice_actors_scope", type_="check")
        batch_op.drop_column("scope")

    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_constraint("ck_chapters_classification_status", type_="check")
        batch_op.drop_constraint("ck_chapters_kind", type_="check")
        batch_op.drop_column("classification_status")
        batch_op.create_check_constraint(
            "ck_chapters_kind",
            "chapter_kind IN ('story','extra','author_note')",
        )

    op.drop_table("book_id_sequences")
