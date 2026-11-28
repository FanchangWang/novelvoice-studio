"""制作阶段的有效状态：只有当前版本的阻断异常才需要人工审核。"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from novelvoice_studio.models import (
    Chapter,
    ReviewItem,
    Scene,
    ScriptUnit,
    ScriptVersion,
)
from novelvoice_studio.services.review_policy import AUTO_ACCEPTED_CODES

Stage = Literal["scene", "script"]


def stage_review_scope(
    session: Session,
    chapter: Chapter,
    stage: Stage,
    version: ScriptVersion | None = None,
) -> tuple[list[str], list[Any]]:
    """返回当前阶段的实体范围，避免历史版本异常阻塞当前结果。"""
    conditions: list[Any] = [
        and_(
            ReviewItem.entity_type == "chapter",
            ReviewItem.entity_id == chapter.id,
        )
    ]
    if stage == "scene":
        entity_ids = list(
            session.scalars(
                select(Scene.id).where(
                    Scene.chapter_id == chapter.id,
                    Scene.analysis_version == chapter.scene_version,
                )
            )
        )
        if entity_ids:
            conditions.append(
                and_(
                    ReviewItem.entity_type == "scene",
                    ReviewItem.entity_id.in_(entity_ids),
                )
            )
        return entity_ids, conditions

    if version is None:
        version = session.scalar(
            select(ScriptVersion)
            .where(
                ScriptVersion.chapter_id == chapter.id,
                ScriptVersion.is_active.is_(True),
            )
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )
    entity_ids = []
    if version is not None:
        entity_ids = list(
            session.scalars(
                select(ScriptUnit.id).where(
                    ScriptUnit.script_version_id == version.id,
                )
            )
        )
        conditions.append(
            and_(
                ReviewItem.entity_type == "script_version",
                ReviewItem.entity_id == version.id,
            )
        )
        if entity_ids:
            conditions.append(
                and_(
                    ReviewItem.entity_type == "script_unit",
                    ReviewItem.entity_id.in_(entity_ids),
                )
            )
    return entity_ids, conditions


def has_open_blocking(
    session: Session,
    book_id: str,
    stage: Stage,
    conditions: list[Any],
) -> bool:
    prefix = "scene_" if stage == "scene" else "script_"
    return bool(
        session.scalar(
            select(func.count(ReviewItem.id)).where(
                ReviewItem.book_id == book_id,
                ReviewItem.category == "content",
                ReviewItem.status == "open",
                ReviewItem.severity == "blocking",
                ReviewItem.code.not_in(AUTO_ACCEPTED_CODES),
                ReviewItem.code.startswith(prefix),
                or_(*conditions),
            )
        )
    )


def entity_has_open_blocking(
    session: Session,
    book_id: str,
    stage: Stage,
    entity_type: str,
    entity_id: str,
) -> bool:
    prefix = "scene_" if stage == "scene" else "script_"
    return bool(
        session.scalar(
            select(func.count(ReviewItem.id)).where(
                ReviewItem.book_id == book_id,
                ReviewItem.category == "content",
                ReviewItem.status == "open",
                ReviewItem.severity == "blocking",
                ReviewItem.code.not_in(AUTO_ACCEPTED_CODES),
                ReviewItem.code.startswith(prefix),
                ReviewItem.entity_type == entity_type,
                ReviewItem.entity_id == entity_id,
            )
        )
    )


def effective_stage_status(
    stored_status: str,
    *,
    active: bool = False,
    blocking: bool = False,
) -> str:
    if active:
        return "running"
    if blocking:
        return "needs_review"
    if stored_status == "needs_review":
        return "completed"
    return stored_status


def effective_entity_status(
    stored_status: str,
    *,
    blocking: bool = False,
) -> str:
    if blocking and stored_status != "approved":
        return "needs_review"
    if stored_status == "needs_review":
        return "completed"
    return stored_status


def effective_unit_status(status: str, *, blocking: bool) -> str:
    if status == "approved":
        return status
    if blocking:
        return "needs_review"
    if status == "needs_review":
        return "auto"
    return status


def effective_version_status(status: str, *, blocking: bool) -> str:
    if status == "approved":
        return status
    if blocking:
        return "needs_review"
    if status == "needs_review":
        return "draft"
    return status
