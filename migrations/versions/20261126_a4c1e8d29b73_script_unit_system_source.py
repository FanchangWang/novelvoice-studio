"""允许系统补齐的剧本单元标记为 system 来源

Revision ID: a4c1e8d29b73
Revises: 9f3c2a7b5d10
Create Date: 2026-11-26 16:30:00.000000

P4 有声剧本在模型漏覆盖原文时，会按原文确定性补入系统单元。
这类单元既非模型产出，也非用户编辑，需要独立的 source 值。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a4c1e8d29b73"
down_revision: str | None = "9f3c2a7b5d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_SOURCE = "source IN ('ai','legacy','user','system')"
_OLD_SOURCE = "source IN ('ai','legacy','user')"


def upgrade() -> None:
    with op.batch_alter_table("script_units", schema=None) as batch_op:
        batch_op.drop_constraint("ck_script_units_source", type_="check")
        batch_op.create_check_constraint("ck_script_units_source", _NEW_SOURCE)


def downgrade() -> None:
    with op.batch_alter_table("script_units", schema=None) as batch_op:
        batch_op.drop_constraint("ck_script_units_source", type_="check")
        batch_op.create_check_constraint("ck_script_units_source", _OLD_SOURCE)
