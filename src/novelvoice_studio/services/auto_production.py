"""一键全自动制作：全书分析 → 自动选角 → 逐章剧本 → 自动配音。"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from novelvoice_studio.db.session import session_scope
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Book, BookBible, Chapter, GenerationJob, ScriptUnit, ScriptVersion
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.bible import BibleService
from novelvoice_studio.services.casting import CastingService
from novelvoice_studio.services.paths import new_id, validate_id
from novelvoice_studio.services.production_batch import ProductionBatchService
from novelvoice_studio.services.scenes import SceneService
from novelvoice_studio.services.scripts import ScriptService
from novelvoice_studio.services.unit_audio import CUE_UNIT_TYPES, UnitAudioService

logger = logging.getLogger(__name__)
AUTO_JOB_TYPE = "auto_production"
ACTIVE = ("pending", "running")
TERMINAL = ("completed", "failed", "cancelled")
STAGES = ("bible", "casting", "script", "audio")


def _append_system_failure(failures: list[Any], failure: dict[str, Any]) -> list[dict[str, Any]]:
    """保留阶段故障，只限制逐单元故障数量，避免后续音频错误淹没根因。"""
    stage_failures = [
        item for item in failures
        if isinstance(item, dict) and not item.get("unit_id")
    ]
    unit_failures = [
        item for item in failures
        if isinstance(item, dict) and item.get("unit_id")
    ]
    target = unit_failures if failure.get("unit_id") else stage_failures
    target.append(failure)
    return stage_failures + unit_failures[-50:]


class AutoProductionService:
    def __init__(
        self,
        settings: Settings,
        factory: sessionmaker[Session],
        registry: ProviderRegistry,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.registry = registry
        self.bibles = BibleService(settings, factory, registry)
        self.casting = CastingService(settings, factory, registry)
        self.batch = ProductionBatchService(
            settings,
            factory,
            SceneService(settings, factory, registry),
            ScriptService(settings, factory, registry),
        )

    def start(self, book_id: str, *, refresh_bible: bool = False) -> AnalysisJobOut:
        validate_id(book_id, "book")
        with session_scope(self.factory) as session:
            book = session.get(Book, book_id)
            if book is None or book.deleted_at is not None:
                raise NotFoundError("书籍不存在或已在回收站")
            running = session.scalar(
                select(func.count(GenerationJob.id)).where(
                    GenerationJob.book_id == book.id,
                    GenerationJob.status.in_(ACTIVE),
                ))
            if running:
                raise ConflictError("本书已有任务运行，请等待完成或取消", code="book_job_running")
            chapter_count = int(session.scalar(
                select(func.count(Chapter.id)).where(
                    Chapter.book_id == book.id,
                    Chapter.analysis_included.is_(True),
                )) or 0)
            if not chapter_count:
                raise ValidationError("没有纳入制作的正文章节，请先完成章节分类", code="no_included_chapters")
            bible = session.scalar(
                select(BookBible).where(
                    BookBible.book_id == book.id,
                    BookBible.is_active.is_(True),
                ).order_by(BookBible.version.desc()).limit(1))
            bible_ready = bible is not None and bible.status in {"approved", "needs_review"}
            included_ids = set(session.scalars(
                select(Chapter.id).where(
                    Chapter.book_id == book.id,
                    Chapter.analysis_included.is_(True),
                )
            ))
            covered_ids = {
                str(row.get("chapter_id"))
                for row in ((bible.content_json or {}).get("chapter_timelines") or [])
                if isinstance(row, dict)
            } if bible is not None else set()
            missing_bible_chapter_ids = [
                chapter_id for chapter_id in session.scalars(
                    select(Chapter.id).where(
                        Chapter.book_id == book.id,
                        Chapter.analysis_included.is_(True),
                    ).order_by(Chapter.sequence.asc())
                )
                if chapter_id in included_ids and chapter_id not in covered_ids
            ]
            # 导入更新或章节拆分后，旧圣经可能仍然 active，但不再覆盖全部正文章节。
            # 自动制作必须先补齐这些章节，不能把资料缺失伪装成逐章模型失败。
            needs_bible = refresh_bible or not bible_ready or bool(missing_bible_chapter_ids)
            cast_plan = self.casting.book_casts(book_id)
            latest_casting = session.scalar(
                select(GenerationJob)
                .where(
                    GenerationJob.book_id == book.id,
                    GenerationJob.job_type == "book_casting",
                    GenerationJob.status.in_(TERMINAL),
                )
                .order_by(GenerationJob.created_at.desc())
                .limit(1)
            )
            # 内容异常不应阻断续作；无声线、上次系统失败或仍有缺失角色时重新选角。
            cast_retry_needed = bool(
                latest_casting is not None
                and int((latest_casting.result_json or {}).get("failed") or 0) > 0
            )
            needs_casting = bool(
                refresh_bible or needs_bible or not cast_plan.cast_count
                or cast_retry_needed or cast_plan.missing_roles
                or self.casting.has_incompatible_auto_casts(book.id)
            )
            planned_stages = int(needs_bible) + int(needs_casting) + 2
            skipped_stages = {
                stage: {"status": "skipped" if not needed else "pending"}
                for stage, needed in (("bible", needs_bible), ("casting", needs_casting))
            }
            skipped_stages.update({"script": {"status": "pending"}, "audio": {"status": "pending"}})
            job = GenerationJob(
                id=new_id("job"), book_id=book.id, target_id=book.id,
                target_type="book", stage="production", job_type=AUTO_JOB_TYPE,
                status="pending", total_items=planned_stages,
                completed_items=0, failed_items=0, progress=0.0, attempt_count=0, max_attempts=1,
                params_json={
                    "refresh_bible": bool(refresh_bible),
                    "needs_bible": needs_bible,
                    "needs_casting": needs_casting,
                    "bible_chapter_ids": missing_bible_chapter_ids,
                },
                result_json={
                    "stage": None,
                    "stages": skipped_stages,
                    "current_child": None,
                    "audio": {"ready": 0, "failed": 0, "skipped": 0},
                    "system_failures": [],
                },
                created_at=utc_now(), updated_at=utc_now(),
            )
            session.add(job)
            book.production_status = "analyzing"
            book.updated_at = utc_now()
            session.flush()
            output = to_job_out(job)
        thread = threading.Thread(target=self._run, args=(output.id,), daemon=True,
                                  name=f"novelvoice-auto-{output.id}")
        try:
            thread.start()
        except Exception as exc:
            with session_scope(self.factory) as session:
                row = session.get(GenerationJob, output.id)
                if row:
                    row.status = "failed"
                    row.error_code = "thread_start_failed"
                    row.error_message = str(exc)[:1000]
                    row.finished_at = row.updated_at = utc_now()
            raise AppError("无法启动自动制作任务", status_code=500, code="auto_thread_failed") from exc
        return output

    def get_job(self, job_id: str) -> AnalysisJobOut:
        validate_id(job_id, "job")
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            return to_job_out(job)

    def cancel(self, job_id: str) -> AnalysisJobOut:
        validate_id(job_id, "job")
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            if job.status not in ACTIVE:
                raise ConflictError("自动制作任务已结束", code="auto_not_active")
            result = dict(job.result_json or {})
            child = result.get("current_child") or {}
            job.status = "cancelled"
            job.finished_at = job.updated_at = utc_now()
            book = session.get(Book, job.book_id)
            if book is not None:
                book.production_status = "not_started"
                book.updated_at = utc_now()
            output = to_job_out(job)
        if child.get("id") and child.get("type"):
            self._cancel_child(str(child["id"]), str(child["type"]))
        return output

    def _job(self, session: Session, job_id: str) -> GenerationJob:
        job = session.get(GenerationJob, job_id)
        if job is None or job.job_type != AUTO_JOB_TYPE:
            raise NotFoundError("自动制作任务不存在")
        return job

    def _cancel_child(self, child_id: str, kind: str) -> None:
        try:
            if kind == "bible":
                with session_scope(self.factory) as session:
                    child = session.get(GenerationJob, child_id)
                    book_id = child.book_id if child else None
                if book_id:
                    self.bibles.cancel_book_bible(book_id)
            elif kind == "casting":
                # 子任务 ID 对应其所属书籍；取消接口需要 book_id。
                with session_scope(self.factory) as session:
                    child = session.get(GenerationJob, child_id)
                    book_id = child.book_id if child else None
                if book_id:
                    self.casting.cancel_casting(book_id)
            elif kind == "batch":
                self.batch.cancel(child_id)
        except (ConflictError, NotFoundError):
            pass

    def _set_child(self, job_id: str, child: AnalysisJobOut, kind: str) -> None:
        stage = "script" if kind == "batch" else kind
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            child_row = session.get(GenerationJob, child.id)
            if child_row is not None:
                child_row.parent_job_id = job_id
            result = dict(job.result_json or {})
            stages = dict(result.get("stages") or {})
            stages[stage] = {
                "status": "running",
                "job_id": child.id,
                "completed_items": child.completed_items,
                "total_items": child.total_items,
                "progress": child.progress,
            }
            result["stages"] = stages
            result["current_child"] = {"id": child.id, "type": kind}
            job.result_json = result
            job.updated_at = utc_now()

    def _clear_child(self, job_id: str, *, stage: str, status: str) -> None:
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            result = dict(job.result_json or {})
            stages = dict(result.get("stages") or {})
            current = dict(stages.get(stage) or {})
            previous_status = str(current.get("status") or "")
            current["status"] = status
            stages[stage] = current
            result["stages"] = stages
            result["current_child"] = None
            result["stage"] = None
            job.result_json = result
            if previous_status not in {"completed", "skipped"}:
                job.completed_items = int(job.completed_items) + (status in {"completed", "skipped"})
                job.failed_items = int(job.failed_items) + (status == "failed")
                job.progress = min(1.0, float(job.completed_items) / float(job.total_items or 4))
            job.updated_at = utc_now()

    def _stage(self, job_id: str, stage: str) -> None:
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            result = dict(job.result_json or {})
            result["stage"] = stage
            job.result_json = result
            job.updated_at = utc_now()

    def _is_cancelled(self, job_id: str) -> bool:
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            return job.status == "cancelled"

    def _wait_child(self, parent_id: str, child_id: str) -> str:
        while True:
            if self._is_cancelled(parent_id):
                return "cancelled"
            with session_scope(self.factory) as session:
                child = session.get(GenerationJob, child_id)
                if child is None:
                    return "failed"
                status = str(child.status)
                parent = self._job(session, parent_id)
                current_child = dict((parent.result_json or {}).get("current_child") or {})
                kind = str(current_child.get("type") or "")
                stage = "script" if kind == "batch" else kind
                if stage in STAGES:
                    result = dict(parent.result_json or {})
                    stages = dict(result.get("stages") or {})
                    stages[stage] = {
                        "status": "running",
                        "job_id": child.id,
                        "completed_items": int(child.completed_items),
                        "total_items": int(child.total_items),
                        "progress": float(child.progress),
                        "error_message": child.error_message,
                    }
                    result["stages"] = stages
                    parent.result_json = result
                    parent.updated_at = utc_now()
            if status in TERMINAL:
                return status
            time.sleep(0.5)

    def _record_stage_failure(self, job_id: str, stage: str, message: str) -> None:
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            result = dict(job.result_json or {})
            stage_result = dict((result.get("stages") or {}).get(stage) or {})
            child_id = stage_result.get("job_id")
            child = session.get(GenerationJob, str(child_id)) if child_id else None
            detail = (child.error_message if child is not None else None) or message
            if stage == "script" and child is not None:
                chapter_failures: list[str] = []
                for item in (child.result_json or {}).get("chapters") or []:
                    if not isinstance(item, dict) or item.get("status") != "failed":
                        continue
                    chapter_id = str(item.get("chapter_id") or "")
                    chapter = session.get(Chapter, chapter_id) if chapter_id else None
                    title = chapter.title if chapter is not None else chapter_id
                    failed_stage = next(
                        (
                            (name, value)
                            for name, value in (item.get("stages") or {}).items()
                            if isinstance(value, dict) and value.get("status") == "failed"
                        ),
                        None,
                    )
                    if failed_stage is None:
                        continue
                    stage_name, stage_result = failed_stage
                    stage_job = session.get(
                        GenerationJob, str(stage_result.get("job_id") or "")
                    ) if stage_result.get("job_id") else None
                    reason = (
                        stage_job.error_message if stage_job is not None else None
                    ) or stage_result.get("message") or stage_result.get("error_code") or "制作失败"
                    chapter_failures.append(f"{title}（{stage_name}）：{reason}")
                if chapter_failures:
                    shown = chapter_failures[:5]
                    suffix = f"；另有 {len(chapter_failures) - len(shown)} 章" if len(chapter_failures) > len(shown) else ""
                    detail = "；".join(shown) + suffix
            failures = list(result.get("system_failures") or [])
            result["system_failures"] = _append_system_failure(
                failures, {"stage": stage, "message": str(detail)[:1200]},
            )
            job.result_json = result
            job.updated_at = utc_now()

    def _has_stage_failures(self, job_id: str) -> bool:
        with session_scope(self.factory) as session:
            stages = (self._job(session, job_id).result_json or {}).get("stages") or {}
            return any(
                isinstance(item, dict) and item.get("status") == "failed"
                for item in stages.values()
            )

    def _run(self, job_id: str) -> None:
        try:
            with session_scope(self.factory) as session:
                job = self._job(session, job_id)
                if job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = utc_now()
                book_id = job.book_id
                needs_bible = bool((job.params_json or {}).get("needs_bible"))
                needs_casting = bool((job.params_json or {}).get("needs_casting"))
                bible_chapter_ids = [
                    str(item) for item in ((job.params_json or {}).get("bible_chapter_ids") or [])
                    if item
                ]
                job.updated_at = utc_now()

            if needs_bible:
                self._stage(job_id, "bible")
                child = self.bibles.start_book_bible(
                    book_id, chapter_ids=bible_chapter_ids or None
                )
                self._set_child(job_id, child, "bible")
                status = self._wait_child(job_id, child.id)
                self._clear_child(job_id, stage="bible", status=status)
                if status == "cancelled":
                    self._cancel_child(child.id, "bible")
                if status != "completed":
                    raise RuntimeError(f"全书分析未完成：{status}")

            if self._is_cancelled(job_id):
                return

            if needs_casting:
                self._stage(job_id, "casting")
                child = self.casting.start_casting(book_id)
                self._set_child(job_id, child, "casting")
                status = self._wait_child(job_id, child.id)
                self._clear_child(job_id, stage="casting", status=status)
                if status == "cancelled":
                    self._cancel_child(child.id, "casting")
                if status != "completed":
                    self._record_stage_failure(job_id, "casting", f"自动选角未完成：{status}")
                    # 选角是系统依赖，不是内容审核门禁；剧本仍继续生成，音频阶段会处理已有声线。

            if self._is_cancelled(job_id):
                return

            self._stage(job_id, "script")
            child = self.batch.start(book_id, stages=["scenes", "script"], only_missing=True,
                                    adaptation_level="light")
            self._set_child(job_id, child, "batch")
            status = self._wait_child(job_id, child.id)
            self._clear_child(job_id, stage="script", status=status)
            if status == "cancelled":
                self._cancel_child(child.id, "batch")
            if status != "completed":
                self._record_stage_failure(job_id, "script", f"逐章剧本未完成：{status}")
                # 单章失败不应丢弃其他章节已完成剧本；继续为已有剧本生成音频。

            if self._is_cancelled(job_id):
                return

            # 分场/剧本会新增实际说话人；音频前做一次增量补选，避免遗留“缺少声音”。
            cast_plan = self.casting.book_casts(book_id)
            if cast_plan.missing_roles:
                self._stage(job_id, "casting")
                child = self.casting.start_casting(book_id)
                self._set_child(job_id, child, "casting")
                status = self._wait_child(job_id, child.id)
                self._clear_child(job_id, stage="casting", status=status)
                if status == "cancelled":
                    self._cancel_child(child.id, "casting")
                if status != "completed":
                    self._record_stage_failure(job_id, "casting", f"增量补选未完成：{status}")

            if self._is_cancelled(job_id):
                return

            self._stage(job_id, "audio")
            self._generate_audio(job_id, book_id)
            with session_scope(self.factory) as session:
                job = self._job(session, job_id)
                if job.status != "cancelled":
                    result = dict(job.result_json or {})
                    stages = dict(result.get("stages") or {})
                    result["stages"] = stages
                    result["current_child"] = None
                    job.result_json = result
        except Exception as exc:  # noqa: BLE001
            logger.exception("自动制作失败 book=%s job=%s", locals().get("book_id"), job_id)
            with session_scope(self.factory) as session:
                job = self._job(session, job_id)
                if job.status == "cancelled":
                    return
                job.status = "failed"
                job.error_code = "auto_production_failed"
                job.error_message = str(exc)[:1200]
                job.finished_at = job.updated_at = utc_now()
                result = dict(job.result_json or {})
                result["current_child"] = None
                stages = dict(result.get("stages") or {})
                current_stage = result.get("stage")
                if current_stage in STAGES:
                    current = dict(stages.get(str(current_stage)) or {})
                    current["status"] = "failed"
                    current["error_message"] = str(exc)[:1200]
                    stages[str(current_stage)] = current
                for stage in STAGES:
                    if stages.get(stage, {}).get("status") == "pending":
                        stages[stage] = {
                            "status": "blocked",
                            "reason": "前置阶段失败，未开始",
                        }
                result["stages"] = stages
                failures = list(result.get("system_failures") or [])
                result["system_failures"] = _append_system_failure(
                    failures, {"stage": result.get("stage"), "message": str(exc)[:1200]},
                )
                job.result_json = result
                book = session.get(Book, job.book_id)
                if book:
                    book.production_status = "failed"
                    book.updated_at = utc_now()

    def _audio_units(self, session: Session, book_id: str) -> list[ScriptUnit]:
        return list(session.scalars(
            select(ScriptUnit).join(ScriptVersion, ScriptUnit.script_version_id == ScriptVersion.id)
            .join(Chapter, Chapter.id == ScriptUnit.chapter_id)
            .where(
                ScriptUnit.book_id == book_id,
                ScriptVersion.is_active.is_(True),
                ScriptUnit.unit_type.not_in(CUE_UNIT_TYPES),
                ScriptUnit.audio_status != "ready",
            ).order_by(Chapter.sequence.asc(), ScriptUnit.unit_index.asc())))

    def _generate_audio(self, job_id: str, book_id: str) -> None:
        units = []
        with session_scope(self.factory) as session:
            units = self._audio_units(session, book_id)
            job = self._job(session, job_id)
            job.total_items = int(job.total_items) + len(units)
            result = dict(job.result_json or {})
            stages = dict(result.get("stages") or {})
            stages["audio"] = {
                "status": "running",
                "completed_items": 0,
                "total_items": len(units),
                "progress": 0.0,
            }
            result["stages"] = stages
            job.result_json = result
            job.updated_at = utc_now()
        for index, unit in enumerate(units, start=1):
            if self._is_cancelled(job_id):
                return
            outcome = "ready"
            try:
                with session_scope(self.factory) as session:
                    UnitAudioService(self.settings, session, self.registry).generate(unit.id)
            except Exception as exc:  # noqa: BLE001
                outcome = "failed"
                logger.warning("自动配音失败 unit=%s: %s", unit.id, exc)
                with session_scope(self.factory) as session:
                    job = self._job(session, job_id)
                    result = dict(job.result_json or {})
                    failures = list(result.get("system_failures") or [])
                    result["system_failures"] = _append_system_failure(
                        failures,
                        {"stage": "audio", "unit_id": unit.id, "message": str(exc)[:500]},
                    )
                    job.result_json = result
            with session_scope(self.factory) as session:
                job = self._job(session, job_id)
                result = dict(job.result_json or {})
                audio = dict(result.get("audio") or {})
                audio[outcome] = int(audio.get(outcome, 0)) + 1
                result["audio"] = audio
                stages = dict(result.get("stages") or {})
                audio_stage = dict(stages.get("audio") or {})
                audio_stage.update({
                    "status": "running",
                    "completed_items": index,
                    "total_items": len(units),
                    "progress": index / len(units),
                })
                stages["audio"] = audio_stage
                result["stages"] = stages
                job.result_json = result
                job.completed_items = int(job.completed_items) + 1
                job.failed_items = int(job.failed_items) + (outcome == "failed")
                job.progress = min(0.99, float(job.completed_items) / float(job.total_items))
                job.updated_at = utc_now()
        self._clear_child(job_id, stage="audio", status="completed" if not self._audio_failed(job_id) else "failed")
        with session_scope(self.factory) as session:
            job = self._job(session, job_id)
            if job.status == "cancelled":
                return
            failed = bool((job.result_json or {}).get("audio", {}).get("failed")) or self._has_stage_failures(job_id)
            job.status = "failed" if failed else "completed"
            job.progress = 1.0
            job.finished_at = job.updated_at = utc_now()
            if failed:
                job.error_code = "audio_units_failed" if (job.result_json or {}).get("audio", {}).get("failed") else "auto_stages_failed"
                job.error_message = "部分制作阶段失败，请查看系统任务故障；重试会跳过已完成结果。"
            book = session.get(Book, book_id)
            if book:
                book.production_status = "failed" if failed else "ready"
                book.updated_at = utc_now()

    def _audio_failed(self, job_id: str) -> bool:
        with session_scope(self.factory) as session:
            return bool((self._job(session, job_id).result_json or {}).get("audio", {}).get("failed"))


def recover_interrupted_auto(session: Session) -> dict[str, int]:
    jobs = list(session.scalars(select(GenerationJob).where(
        GenerationJob.job_type == AUTO_JOB_TYPE,
        GenerationJob.status.in_(ACTIVE),
    )))
    now = utc_now()
    for job in jobs:
        job.status = "failed"
        job.error_code = "interrupted"
        job.error_message = "应用在自动制作过程中退出，可从当前阶段重新发起。"
        job.finished_at = job.updated_at = now
    return {"auto_production_jobs": len(jobs)}
