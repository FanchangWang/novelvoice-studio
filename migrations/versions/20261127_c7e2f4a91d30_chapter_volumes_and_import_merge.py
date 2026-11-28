"""章节卷结构、特殊章节分类与增量导入字段

Revision ID: c7e2f4a91d30
Revises: a4c1e8d29b73
Create Date: 2026-11-27 12:00:00.000000

旧章节统一回填到“默认卷”。番外、请假单、作者说明等仍保留在章节表中，
但默认不进入设定集、分场与剧本分析；用户可在章节详情中显式纳入。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "c7e2f4a91d30"
down_revision: str | None = "a4c1e8d29b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("volume_number", sa.Integer(), nullable=False,
                      server_default=sa.text("0"))
        )
        batch_op.add_column(
            sa.Column("volume_title", sa.String(length=255), nullable=False,
                      server_default=sa.text("'默认卷'"))
        )
        batch_op.add_column(
            sa.Column("chapter_kind", sa.String(length=16), nullable=False,
                      server_default=sa.text("'story'"))
        )
        batch_op.add_column(
            sa.Column("analysis_included", sa.Boolean(), nullable=False,
                      server_default=sa.text("1"))
        )
        batch_op.add_column(
            sa.Column("source_hash", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_chapters_kind",
            "chapter_kind IN ('story','extra','author_note')",
        )
        batch_op.create_check_constraint(
            "ck_chapters_analysis_included",
            "analysis_included IN (0,1)",
        )
    op.create_index(
        "ix_chapters_source_hash", "chapters", ["source_hash"], unique=False
    )

    op.execute(sa.text(
        "UPDATE chapters SET chapter_kind = 'author_note', analysis_included = 0 "
        "WHERE title LIKE '%请假%' OR title LIKE '%停更%' "
        "OR title LIKE '%作者有话%' OR title LIKE '%更新说明%' "
        "OR title LIKE '%单章说明%' OR title LIKE '%上架感言%'"
    ))
    op.execute(sa.text(
        "UPDATE chapters SET chapter_kind = 'extra', analysis_included = 0 "
        "WHERE chapter_kind = 'story' AND ("
        "title LIKE '%番外%' OR title LIKE '%后记%' OR title LIKE '%前言%' "
        "OR title LIKE '%序言%')"
    ))


def downgrade() -> None:
    op.drop_index("ix_chapters_source_hash", table_name="chapters")
    with op.batch_alter_table("chapters", schema=None) as batch_op:
        batch_op.drop_constraint("ck_chapters_analysis_included", type_="check")
        batch_op.drop_constraint("ck_chapters_kind", type_="check")
        batch_op.drop_column("source_hash")
        batch_op.drop_column("analysis_included")
        batch_op.drop_column("chapter_kind")
        batch_op.drop_column("volume_title")
        batch_op.drop_column("volume_number")
