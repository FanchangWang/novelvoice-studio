"""segment evidence

Revision ID: 5c1d9a7b3e2f
Revises: 0790ed4240ab
Create Date: 2026-11-25 10:24:00.000000

阶段 2 分段需要记录对齐依据、回退原因和低置信度说明，方便审核界面展示。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "5c1d9a7b3e2f"
down_revision: str | None = "0790ed4240ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "evidence_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.drop_column("evidence_json")