"""整书、当前卷与选中章节的分场/剧本批量编排。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from novelvoice_studio.db.session import session_scope
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    Book,
    BookBible,
    Chapter,
    GenerationJob,
    Scene,
    ScriptVersion,
)
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.paths import new_id, validate_id
from novelvoice_studio.services.scenes import SceneService
from novelvoice_studio.services.scripts import ScriptService

logger = logging.getLogger(__name__)

BATCH_JOB_TYPE = "production_batch"
_ACTIVE_STATUSES = ("pending", "running")
_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def recover_interrupted_batches(session: Session) -> dict[str, int]:
    """启动时结束上次进程遗留的批量任务，避免总览永久显示运行中。"""
    jobs = list(
        session.scalars(
            select(GenerationJob).where(
                GenerationJob.job_type == BATCH_JOB_TYPE,
                GenerationJob.status.in_(_ACTIVE_STATUSES),
            )
        )
    )
    now = utc_now()
    for job in jobs:
        job.status = "failed"
        job.error_code = "interrupted"
        job.error_message = "应用在批量制作过程中退出，任务已中断；可重新发起批量生成。"
        job.finished_at = now
        job.updated_at = now
        result = dict(job.result_json or {})
        result["current_child"] = None
        job.result_json = result
    return {"production_batch_jobs": len(jobs)}


class ProductionBatchService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        scene_service: SceneService,
        script_service: ScriptService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.scene_service = scene_service
        self.script_service = script_service

    def start(
        self,
        book_id: str,
        *,
        stages: list[str],
        volume_number: int | None = None,
        chapter_ids: list[str] | None = None,
        only_missing: bool = True,
        adaptation_level: str = "light",
    ) -> AnalysisJobOut:
        normalized_stages = list(dict.fromkeys(stages))
        if not normalized_stages or any(
            stage not in {"scenes", "script"} for stage in normalized_stages
        ):
            raise ValidationError("批量阶段只能是 scenes / script")
        if volume_number is not None and volume_number < 0:
            raise ValidationError("卷号不能小于 0")
        if adaptation_level not in {"light", "standard", "strong"}:
            raise ValidationError("改编档位只能是 light / standard / strong")

        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            active = session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.book_id == book.id,
                    GenerationJob.job_type == BATCH_JOB_TYPE,
                    GenerationJob.status.in_(_ACTIVE_STATUSES),
                )
                .order_by(GenerationJob.created_at.desc())
                .limit(1)
            )
            if active is not None:
                raise ConflictError(
                    "本书已有批量制作任务正在运行，请等待完成或先取消",
                    code="production_batch_running",
                )
            bible = session.scalar(
                select(BookBible)
                .where(BookBible.book_id == book.id, BookBible.is_active.is_(True))
                .order_by(BookBible.version.desc())
                .limit(1)
            )
            if bible is None or bible.status not in {"approved", "needs_review"}:
                raise ValidationError(
                    "请先完成角色圣经并采用当前版本，再批量生成分场和剧本",
                    code="bible_required",
                )

            query = select(Chapter).where(
                Chapter.book_id == book.id,
                Chapter.analysis_included.is_(True),
            )
            if volume_number is not None and not chapter_ids:
                query = query.where(Chapter.volume_number == volume_number)
            requested_ids = list(dict.fromkeys(chapter_ids or []))
            if requested_ids:
                for chapter_id in requested_ids:
                    validate_id(chapter_id, "ch")
                query = query.where(Chapter.id.in_(requested_ids))
            chapters = list(
                session.scalars(query.order_by(Chapter.sequence.asc()))
            )
            if requested_ids and len(chapters) != len(requested_ids):
                raise ValidationError(
                    "部分章节不存在、未纳入分析或不属于当前书籍",
                    code="batch_chapter_unavailable",
                )
            if volume_number is not None and requested_ids and any(
                item.volume_number != volume_number for item in chapters
            ):
                raise ValidationError("选中章节不属于指定卷")
            if not chapters:
                raise ValidationError("当前范围没有可生成的章节")

            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=book.id,
                target_type="book",
                stage="production",
                job_type=BATCH_JOB_TYPE,
                status="pending",
                total_items=len(chapters),
                completed_items=0,
                failed_items=0,
                progress=0.0,
                attempt_count=0,
                max_attempts=1,
                params_json={
                    "book_id": book.id,
                    "stages": normalized_stages,
                    "volume_number": volume_number,
                    "chapter_ids": [item.id for item in chapters],
                    "only_missing": bool(only_missing),
                    "adaptation_level": adaptation_level,
                },
                result_json={"chapters": [], "child_job_ids": []},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            session.flush()
            output = to_job_out(job)

        thread = threading.Thread(
            target=self._run,
            args=(output.id,),
            name=f"novelvoice-production-batch-{output.id}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001
            logger.exception("无法启动批量制作后台线程")
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, output.id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = "thread_start_failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
            raise AppError(
                "无法启动批量制作后台线程",
                status_code=500,
                code="production_batch_thread_failed",
            ) from exc
        return output

    def get_job(self, job_id: str) -> AnalysisJobOut:
        validate_id(job_id, "job")
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.job_type != BATCH_JOB_TYPE:
                raise NotFoundError("批量制作任务不存在")
            return to_job_out(job)

    def cancel(self, job_id: str) -> AnalysisJobOut:
        validate_id(job_id, "job")
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.job_type != BATCH_JOB_TYPE:
                raise NotFoundError("批量制作任务不存在")
            if job.status not in _ACTIVE_STATUSES:
                raise ConflictError("批量制作任务已结束", code="batch_not_active")
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            result = dict(job.result_json or {})
            current = result.get("current_child") or {}
            child_id = current.get("job_id")
            stage = current.get("stage")
            output = to_job_out(job)
        if child_id and stage:
            self._cancel_child(child_id, stage)
        return output

    def _cancel_child(self, child_id: str, stage: str) -> None:
        try:
            with session_scope(self.session_factory) as session:
                child = session.get(GenerationJob, child_id)
                chapter_id = child.target_id if child is not None else None
            if not chapter_id:
                return
            if stage == "scenes":
                self.scene_service.cancel_scene_analysis(chapter_id)
            else:
                self.script_service.cancel_script(chapter_id)
        except (ConflictError, NotFoundError):
            pass

    def _run(self, job_id: str) -> None:
        try:
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = utc_now()
                job.updated_at = utc_now()
                params = dict(job.params_json or {})
                chapter_ids = list(params.get("chapter_ids") or [])
                stages = list(params.get("stages") or [])
                only_missing = bool(params.get("only_missing", True))
                adaptation = str(params.get("adaptation_level") or "light")
                result = dict(job.result_json or {})
                chapter_results = list(result.get("chapters") or [])
                child_job_ids = list(result.get("child_job_ids") or [])

            failed_chapters = 0
            for index, chapter_id in enumerate(chapter_ids, start=1):
                if self._is_cancelled(job_id):
                    return
                chapter_result: dict[str, Any] = {
                    "chapter_id": chapter_id,
                    "stages": {},
                }
                stage_failed = False
                for stage in stages:
                    if self._is_cancelled(job_id):
                        return
                    if only_missing and self._stage_exists(chapter_id, stage):
                        chapter_result["stages"][stage] = {"status": "skipped"}
                        continue
                    try:
                        if stage == "scenes":
                            child = self.scene_service.start_scene_analysis(chapter_id)
                        else:
                            child = self.script_service.start_script(
                                chapter_id, adaptation
                            )
                        child_job_ids.append(child.id)
                        registered = self._set_current_child(
                            job_id,
                            child.id,
                            stage,
                            chapter_id,
                            chapter_results,
                            child_job_ids,
                            index,
                        )
                        if not registered:
                            self._cancel_child(child.id, stage)
                            return
                        status = self._wait_child(job_id, child.id)
                        if status == "cancelled":
                            return
                        if status != "completed":
                            stage_failed = True
                            chapter_result["stages"][stage] = {
                                "status": "failed",
                                "job_id": child.id,
                                "error_code": status,
                            }
                            if stage == "scenes" and "script" in stages:
                                chapter_result["stages"]["script"] = {
                                    "status": "skipped",
                                    "reason": "scenes_failed",
                                }
                            break
                        chapter_result["stages"][stage] = {
                            "status": "completed",
                            "job_id": child.id,
                        }
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "批量制作章节 %s 阶段 %s 失败：%s",
                            chapter_id,
                            stage,
                            exc,
                        )
                        stage_failed = True
                        chapter_result["stages"][stage] = {
                            "status": "failed",
                            "error_code": str(getattr(exc, "code", "batch_stage_failed")),
                            "message": str(getattr(exc, "message", exc))[:500],
                        }
                        if stage == "scenes" and "script" in stages:
                            chapter_result["stages"]["script"] = {
                                "status": "skipped",
                                "reason": "scenes_failed",
                            }
                        break
                if stage_failed:
                    failed_chapters += 1
                chapter_result["status"] = (
                    "failed" if stage_failed else "completed"
                )
                chapter_results.append(chapter_result)
                self._update_progress(
                    job_id,
                    index,
                    len(chapter_ids),
                    failed_chapters,
                    chapter_results,
                    child_job_ids,
                )

            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "failed" if failed_chapters else "completed"
                if failed_chapters:
                    job.error_code = "production_batch_chapters_failed"
                    job.error_message = (
                        f"{failed_chapters} 章制作失败，已完成的其他章节结果已保留；"
                        "重试会跳过已完成阶段。"
                    )
                else:
                    job.error_code = None
                    job.error_message = None
                job.failed_items = failed_chapters
                job.progress = 1.0
                job.finished_at = utc_now()
                job.updated_at = utc_now()
                result = dict(job.result_json or {})
                result.update(
                    {
                        "chapters": chapter_results,
                        "child_job_ids": child_job_ids,
                        "current_child": None,
                        "failed_chapters": failed_chapters,
                    }
                )
                job.result_json = result
        except Exception as exc:  # noqa: BLE001
            logger.exception("批量制作任务 %s 异常终止", job_id)
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is not None and job.status != "cancelled":
                    job.status = "failed"
                    job.error_code = "production_batch_failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()

    def _wait_child(self, parent_id: str, child_id: str) -> str:
        while True:
            with session_scope(self.session_factory) as session:
                parent = session.get(GenerationJob, parent_id)
                child = session.get(GenerationJob, child_id)
                if parent is None or parent.status == "cancelled":
                    return "cancelled"
                if child is None:
                    return "missing"
                if child.status in _TERMINAL_STATUSES:
                    if child.status == "failed":
                        return child.error_code or "failed"
                    return child.status
            time.sleep(0.1)

    def _stage_exists(self, chapter_id: str, stage: str) -> bool:
        with session_scope(self.session_factory) as session:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                return False
            if stage == "scenes":
                count = session.scalar(
                    select(func.count(Scene.id)).where(
                        Scene.chapter_id == chapter_id,
                        Scene.analysis_version == chapter.scene_version,
                    )
                )
                return bool(count) and chapter.scene_status != "failed"
            count = session.scalar(
                select(func.count(ScriptVersion.id)).where(
                    ScriptVersion.chapter_id == chapter_id,
                    ScriptVersion.is_active.is_(True),
                )
            )
            return bool(count) and chapter.script_status != "failed"

    def _is_cancelled(self, job_id: str) -> bool:
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            return job is None or job.status == "cancelled"

    def _set_current_child(
        self,
        job_id: str,
        child_id: str,
        stage: str,
        chapter_id: str,
        chapter_results: list[dict[str, Any]],
        child_job_ids: list[str],
        index: int,
    ) -> bool:
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return False
            result = dict(job.result_json or {})
            result.update(
                {
                    "chapters": chapter_results,
                    "child_job_ids": child_job_ids,
                    "current_child": {
                        "job_id": child_id,
                        "stage": stage,
                        "chapter_id": chapter_id,
                    },
                    "current_index": index,
                }
            )
            job.result_json = result
            job.updated_at = utc_now()
            return True

    def _update_progress(
        self,
        job_id: str,
        completed: int,
        total: int,
        failed: int,
        chapter_results: list[dict[str, Any]],
        child_job_ids: list[str],
    ) -> None:
        with session_scope(self.session_factory) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "cancelled":
                return
            job.completed_items = completed
            job.failed_items = failed
            job.progress = min(1.0, completed / max(1, total))
            result = dict(job.result_json or {})
            result.update(
                {
                    "chapters": chapter_results,
                    "child_job_ids": child_job_ids,
                    "current_child": None,
                    "failed_chapters": failed,
                }
            )
            job.result_json = result
            job.updated_at = utc_now()

    @staticmethod
    def _book(session: Session, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

