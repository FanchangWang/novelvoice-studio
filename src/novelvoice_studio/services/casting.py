"""v0.5.0 P5：全局配音员库与自动选角。

配音员是跨书资产；书内 VoiceCast 只负责把角色/旁白映射到配音员。
自动选角优先复用已有的合适配音员，没有合适候选时才调用本机 VoiceDesign
生成一个，并把失败与低置信匹配写入异常收件箱。
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import soundfile as sf
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.base import AudioResult, VoiceCandidate
from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.ai.voice_prompts import describe_character_voice
from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationError,
)
from novelvoice_studio.db.session import session_scope
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    Book,
    BookBible,
    Chapter,
    Character,
    CharacterArc,
    GenerationJob,
    ReviewItem,
    Scene,
    SceneCharacter,
    ScriptUnit,
    ScriptVersion,
    VoiceActor,
    VoiceCast,
)
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.casting import (
    BookCastsResponse,
    CastActorOut,
    CastAssignRequest,
    CastCharacterPlanOut,
    CastStagePlanOut,
    CastingStatusResponse,
    VoiceActorCreateRequest,
    VoiceActorListResponse,
    VoiceActorOut,
    VoiceActorUpdateRequest,
    VoiceActorUsage,
    VoiceCastOut,
)
from novelvoice_studio.schemas.voice import VoicePreviewRequest
from novelvoice_studio.services.analysis_serializers import to_job_out
from novelvoice_studio.services.paths import (
    atomic_write_bytes,
    new_id,
    relative_to_data,
    resolve_relative,
    validate_id,
)

logger = logging.getLogger(__name__)

JOB_TYPE = "book_casting"
JOB_STAGE = "casting"
ACTIVE_JOB_STATUSES = ("pending", "running")
REUSE_THRESHOLD = 0.5
LOW_CONFIDENCE_THRESHOLD = 0.65
MAX_CAST_ITEMS = 500

VOICE_DESIGN_LOCK = threading.Lock()

AGE_ORDER = {
    "child": 0,
    "teenager": 1,
    "young_adult": 2,
    "adult": 3,
    "middle_aged": 4,
    "elderly": 5,
    "unknown": 6,
}
AGE_LABELS = {
    "child": "童年",
    "teenager": "少年",
    "young_adult": "青年",
    "adult": "成年",
    "middle_aged": "中年",
    "elderly": "老年",
    "unknown": "未定",
}
ROLE_ORDER = {
    "protagonist": 0,
    "major": 1,
    "minor": 2,
    "extra": 3,
    "unknown": 4,
}
REQUIRED_ROLE_TIERS = {"protagonist", "major", "minor"}
TTS_UNIT_TYPES = {"narration", "dialogue", "inner_monologue"}
CUE_UNIT_TYPES = {"sfx", "ambience", "pause"}
_CAST_REVIEW_PREFIX = "cast_"


def _age_label(value: str | None) -> str:
    return AGE_LABELS.get(str(value or "unknown"), "未定")


def _clean(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:limit]


def _clean_tags(values: Any, limit: int = 20) -> list[str]:
    if not isinstance(values, list):
        return []
    result = [str(item).strip() for item in values if str(item).strip()]
    return list(dict.fromkeys(result))[:limit]


def _gender_for_character(value: str | None) -> str:
    if value in {"male", "female"}:
        return value
    if value == "other":
        return "neutral"
    return "unknown"


def _actor_is_mock(actor: VoiceActor) -> bool:
    params = actor.generation_params_json or {}
    if bool(params.get("is_mock")):
        return True
    return str(actor.provider or "").startswith("mock")


@dataclass
class _Target:
    scope: str
    description: str
    gender: str = "unknown"
    age: str = "unknown"
    character_id: str | None = None
    character_name: str | None = None
    age_stage: str | None = None
    label: str | None = None
    role_tier: str = "unknown"
    timbre_tags: list[str] = field(default_factory=list)
    personality_tags: list[str] = field(default_factory=list)
    voice_note: str | None = None
    language: str = "zh-CN"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "_Target":
        return cls(
            scope=str(raw.get("scope") or "character"),
            description=str(raw.get("description") or ""),
            gender=str(raw.get("gender") or "unknown"),
            age=str(raw.get("age") or "unknown"),
            character_id=raw.get("character_id"),
            character_name=raw.get("character_name"),
            age_stage=raw.get("age_stage"),
            label=raw.get("label"),
            role_tier=str(raw.get("role_tier") or "unknown"),
            timbre_tags=_clean_tags(raw.get("timbre_tags")),
            personality_tags=_clean_tags(raw.get("personality_tags")),
            voice_note=_clean(raw.get("voice_note"), 300),
            language=str(raw.get("language") or "zh-CN"),
        )


class CastingService:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        registry: ProviderRegistry,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry

    # ------------------------------------------------------------ global actors

    def list_actors(
        self,
        *,
        query: str | None = None,
        favorite: bool | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> VoiceActorListResponse:
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        with session_scope(self.session_factory) as session:
            conditions = []
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                conditions.append(
                    or_(
                        VoiceActor.display_name.ilike(pattern),
                        VoiceActor.description.ilike(pattern),
                    )
                )
            if favorite is not None:
                conditions.append(VoiceActor.is_favorite.is_(bool(favorite)))
            if status is None:
                conditions.append(VoiceActor.status != "archived")
            else:
                conditions.append(VoiceActor.status == status)
            total = int(
                session.scalar(select(func.count(VoiceActor.id)).where(*conditions))
                or 0
            )
            rows = list(
                session.scalars(
                    select(VoiceActor)
                    .where(*conditions)
                    .order_by(
                        VoiceActor.is_favorite.desc(),
                        VoiceActor.updated_at.desc(),
                        VoiceActor.id.asc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            usage = self._usage_map(session, [row.id for row in rows])
            return VoiceActorListResponse(
                items=[self._actor_out(row, usage.get(row.id)) for row in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

    def get_actor(self, actor_id: str) -> VoiceActorOut:
        with session_scope(self.session_factory) as session:
            actor = self._actor(session, actor_id)
            usage = self._usage_map(session, [actor.id])
            return self._actor_out(actor, usage.get(actor.id))

    def create_actor(self, payload: VoiceActorCreateRequest) -> VoiceActorOut:
        reference_text = (
            payload.reference_text or self.settings.voice_design.reference_text
        ).strip()
        if not reference_text:
            raise ValidationError("参考文本不能为空")
        actor_id = new_id("va")
        candidate = self._design_voice(
            payload.description,
            reference_text,
            payload.seed,
            language=payload.language,
        )
        audio = candidate.audio
        if audio is None or not audio.audio_bytes:
            raise AppError(
                "声音设计没有返回音频",
                status_code=502,
                code="voice_design_empty",
            )
        info = self._audio_info(audio.audio_bytes)
        target = self._actor_reference_path(actor_id, processed=False)
        atomic_write_bytes(target, audio.audio_bytes)
        relative = relative_to_data(target, self.settings.data_dir)
        params = {
            "language": payload.language,
            "provider": audio.provider,
            "model": audio.model,
            "is_mock": audio.is_mock,
            "checksum_sha256": hashlib.sha256(audio.audio_bytes).hexdigest(),
            "reference_processed": False,
        }
        with session_scope(self.session_factory) as session:
            if payload.source_book_id:
                self._book(session, payload.source_book_id)
            actor = VoiceActor(
                id=actor_id,
                display_name=payload.display_name,
                description=payload.description,
                gender_presentation=payload.gender_presentation,
                age_impression=payload.age_impression,
                language=payload.language,
                timbre_tags_json=_clean_tags(payload.timbre_tags),
                personality_tags_json=_clean_tags(payload.personality_tags),
                reference_text=reference_text,
                reference_audio_path=relative,
                instruct_text=candidate.instruct_text,
                seed=candidate.seed,
                provider=audio.provider,
                model=audio.model,
                generation_params_json=params,
                sample_rate=info["sample_rate"],
                duration_ms=info["duration_ms"],
                status="ready",
                source="user",
                source_book_id=payload.source_book_id,
                is_favorite=payload.is_favorite,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(actor)
            session.flush()
            output = self._actor_out(actor)
        if not audio.is_mock:
            try:
                self._prepare_actor_reference(actor_id)
            except Exception:  # noqa: BLE001
                logger.warning("全局配音员参考音预处理失败，保留原始参考音", exc_info=True)
            return self.get_actor(actor_id)
        return output

    def update_actor(
        self, actor_id: str, payload: VoiceActorUpdateRequest
    ) -> VoiceActorOut:
        with session_scope(self.session_factory) as session:
            actor = self._actor(session, actor_id)
            fields = payload.model_fields_set
            if payload.status == "archived":
                active = int(
                    session.scalar(
                        select(func.count(VoiceCast.id)).where(
                            VoiceCast.voice_actor_id == actor.id,
                            VoiceCast.is_active.is_(True),
                        )
                    )
                    or 0
                )
                if active:
                    raise ConflictError(
                        f"该配音员仍被 {active} 个角色使用，不能归档",
                        code="actor_in_use",
                    )
            if "display_name" in fields and payload.display_name is not None:
                actor.display_name = payload.display_name
            if "description" in fields and payload.description is not None:
                actor.description = payload.description
            if (
                "gender_presentation" in fields
                and payload.gender_presentation is not None
            ):
                actor.gender_presentation = payload.gender_presentation
            if "age_impression" in fields and payload.age_impression is not None:
                actor.age_impression = payload.age_impression
            if "timbre_tags" in fields and payload.timbre_tags is not None:
                actor.timbre_tags_json = _clean_tags(payload.timbre_tags)
            if "personality_tags" in fields and payload.personality_tags is not None:
                actor.personality_tags_json = _clean_tags(payload.personality_tags)
            if "is_favorite" in fields and payload.is_favorite is not None:
                actor.is_favorite = payload.is_favorite
            if "status" in fields and payload.status is not None:
                actor.status = payload.status
            actor.updated_at = utc_now()
            session.flush()
            usage = self._usage_map(session, [actor.id])
            return self._actor_out(actor, usage.get(actor.id))

    def actor_audio_path(self, actor_id: str) -> Path:
        with session_scope(self.session_factory) as session:
            actor = self._actor(session, actor_id)
            return self._actor_file(actor)

    def preview_actor(
        self, actor_id: str, payload: VoicePreviewRequest
    ) -> AudioResult:
        reference = self.actor_audio_path(actor_id)
        with session_scope(self.session_factory) as session:
            actor = self._actor(session, actor_id)
            voice_profile = {
                "id": actor.id,
                "reference_path": str(reference),
                "description": actor.description or "",
                "instruct_text": actor.instruct_text or "",
                "seed": actor.seed,
                "language": actor.language,
            }
        return self.registry.tts.synthesize(
            payload.text,
            voice_profile,
            emotion=payload.emotion,
            intensity=float(payload.intensity),
            delivery=payload.delivery,
            instruction=payload.instruction,
            speed=payload.speed,
        )

    # ---------------------------------------------------------------- casting

    def book_casts(self, book_id: str) -> BookCastsResponse:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            return self._plan_response(session, book)

    def casting_status(self, book_id: str) -> CastingStatusResponse:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            plan = self._plan_response(session, book)
            job = self._latest_job(session, book.id)
            running = job is not None and job.status in ACTIVE_JOB_STATUSES
            return CastingStatusResponse(
                book_id=book.id,
                production_status="casting" if running else book.production_status,
                running=running,
                cast_count=plan.cast_count,
                missing_roles=plan.missing_roles,
                open_reviews=plan.open_reviews,
                job=to_job_out(job) if job else None,
            )

    def start_casting(self, book_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            if self._active_job(session, book.id) is not None:
                raise ConflictError(
                    "本书正在自动选角，请等待当前任务结束或先取消",
                    code="casting_running",
                )
            bible = self._active_bible(session, book.id)
            if bible is None or bible.status not in {"approved", "needs_review"}:
                raise ValidationError(
                    "请先生成并确认全书角色圣经，再自动选角",
                    code="bible_required",
                )
            targets = self._targets(session, book.id, bible)
            job = GenerationJob(
                id=new_id("job"),
                book_id=book.id,
                target_id=book.id,
                target_type="book",
                stage=JOB_STAGE,
                job_type=JOB_TYPE,
                status="pending",
                total_items=len(targets),
                completed_items=0,
                failed_items=0,
                progress=0.0,
                attempt_count=0,
                max_attempts=1,
                params_json={
                    "targets": [item.as_dict() for item in targets],
                    "previous_production_status": book.production_status,
                },
                result_json={
                    "generated": 0,
                    "reused": 0,
                    "skipped": 0,
                    "failed": 0,
                },
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(job)
            book.production_status = "casting"
            book.updated_at = utc_now()
            session.flush()
            output = to_job_out(job)

        thread = threading.Thread(
            target=self._run_job,
            args=(output.id,),
            name=f"novelvoice-casting-{output.id}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception as exc:  # noqa: BLE001
            logger.exception("无法启动自动选角后台线程")
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, output.id)
                if job is not None:
                    job.status = "failed"
                    job.error_code = "thread_start_failed"
                    job.error_message = str(exc)[:1000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                self._fail_job(session, output.id, exc)
            raise AppError(
                "无法启动自动选角后台线程",
                status_code=500,
                code="casting_thread_failed",
            ) from exc
        return output

    def cancel_casting(self, book_id: str) -> AnalysisJobOut:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            job = self._active_job(session, book.id)
            if job is None:
                raise ConflictError("本书没有正在运行的自动选角任务", code="no_active_job")
            job.status = "cancelled"
            job.finished_at = utc_now()
            job.updated_at = utc_now()
            if book.production_status == "casting":
                has_casts = bool(self._active_casts(session, book.id))
                book.production_status = "needs_review" if has_casts else "not_started"
                book.updated_at = utc_now()
            return to_job_out(job)

    def assign_cast(
        self, book_id: str, payload: CastAssignRequest
    ) -> BookCastsResponse:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            validate_id(payload.voice_actor_id, "va")
            actor = self._actor(session, payload.voice_actor_id)
            if actor.status != "ready":
                raise ConflictError("该配音员尚未就绪，不能绑定", code="actor_not_ready")
            self._actor_file(actor)
            try:
                self._prepare_actor_reference(actor.id)
            except Exception:  # noqa: BLE001
                logger.warning("绑定前参考音预处理失败，继续使用现有参考音", exc_info=True)
            character: Character | None = None
            age_stage: str | None = None
            if payload.scope == "narrator":
                if payload.character_id or payload.age_stage_condition:
                    raise ValidationError("旁白选角不能指定角色或年龄阶段")
            else:
                if not payload.character_id:
                    raise ValidationError("角色选角必须指定 character_id")
                validate_id(payload.character_id, "char")
                character = session.get(Character, payload.character_id)
                if (
                    character is None
                    or character.book_id != book.id
                    or character.merged_into_id is not None
                ):
                    raise NotFoundError("角色不存在、已合并或不属于本书")
                if payload.scope == "character_stage":
                    age_stage = payload.age_stage_condition
                    if not age_stage or age_stage == "unknown":
                        raise ValidationError("阶段选角必须指定具体年龄阶段")
                elif payload.age_stage_condition:
                    raise ValidationError("角色选角不能同时指定年龄阶段")

            conditions = [
                VoiceCast.book_id == book.id,
                VoiceCast.scope == payload.scope,
                VoiceCast.is_active.is_(True),
            ]
            if payload.scope == "narrator":
                conditions.extend(
                    [
                        VoiceCast.character_id.is_(None),
                        VoiceCast.age_stage_condition.is_(None),
                    ]
                )
            else:
                conditions.append(VoiceCast.character_id == character.id)  # type: ignore[union-attr]
                if payload.scope == "character_stage":
                    conditions.append(VoiceCast.age_stage_condition == age_stage)
                else:
                    conditions.append(VoiceCast.age_stage_condition.is_(None))
            old_rows = list(session.scalars(select(VoiceCast).where(*conditions)))
            for row in old_rows:
                row.is_active = False
                row.updated_at = utc_now()
            session.flush()

            cast = VoiceCast(
                id=new_id("cast"),
                book_id=book.id,
                character_id=character.id if character else None,
                voice_actor_id=actor.id,
                scope=payload.scope,
                age_stage_condition=age_stage,
                state_condition=None,
                priority=10 if payload.scope == "character_stage" else 0,
                source="manual",
                confidence=1.0,
                is_active=True,
                reason=_clean(payload.reason, 500) or "手动选角",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(cast)
            session.flush()
            self._resolve_owner_reviews(
                session,
                book_id=book.id,
                entity_type="book" if payload.scope == "narrator" else "character",
                entity_id=book.id if payload.scope == "narrator" else character.id,  # type: ignore[union-attr]
                note="已手动完成选角",
            )
            self._sync_script_units(session, book.id)
            self._resolve_script_unassigned_if_clear(session, book.id)
            self._refresh_production_status(session, book)
            return self._plan_response(session, book)

    # ---------------------------------------------------------------- workers

    def _run_job(self, job_id: str) -> None:
        started = time.perf_counter()
        try:
            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                job.status = "running"
                job.started_at = utc_now()
                job.attempt_count = 1
                job.updated_at = utc_now()
                raw_targets = list((job.params_json or {}).get("targets") or [])
                targets = [
                    _Target.from_dict(item)
                    for item in raw_targets
                    if isinstance(item, dict)
                ]
                book_id = job.book_id
                total = max(1, len(targets))

            generated = 0
            reused = 0
            skipped = 0
            failed = 0
            for index, target in enumerate(targets):
                with session_scope(self.session_factory) as session:
                    current = session.get(GenerationJob, job_id)
                    if current is None or current.status == "cancelled":
                        return
                outcome = self._process_target(book_id, target, job_id)
                status = outcome.get("status")
                if status == "generated":
                    generated += 1
                elif status == "reused":
                    reused += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                    job.completed_items = index + 1
                    job.failed_items = failed
                    job.progress = min(1.0, (index + 1) / total)
                    job.result_json = {
                        "generated": generated,
                        "reused": reused,
                        "skipped": skipped,
                        "failed": failed,
                    }
                    job.updated_at = utc_now()

            with session_scope(self.session_factory) as session:
                job = session.get(GenerationJob, job_id)
                if job is None or job.status == "cancelled":
                    return
                book = self._book(session, book_id)
                assigned = self._sync_script_units(session, book.id)
                missing = self._missing_roles(session, book.id)
                if failed and generated + reused + skipped == 0:
                    self._ignore_stale_reviews(session, book.id, job.id)
                    self._review(
                        session,
                        book_id=book.id,
                        entity_type="book",
                        entity_id=book.id,
                        code="cast_job_failed",
                        severity="blocking",
                        title="自动选角失败",
                        message="本次没有生成或复用任何配音员，请检查本机声音设计服务后重试。",
                        payload={"job_id": job.id},
                    )
                    job.status = "failed"
                    job.error_code = "casting_failed"
                    job.error_message = "没有生成或复用任何配音员"
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                    book.production_status = "failed"
                    book.updated_at = utc_now()
                    return
                self._review_unassigned_units(session, book.id, job.id)
                self._resolve_script_unassigned_if_clear(session, book.id)
                self._ignore_stale_reviews(session, book.id, job.id)
                job.status = "completed"
                job.progress = 1.0
                job.completed_items = len(targets)
                job.failed_items = failed
                job.result_json = {
                    "generated": generated,
                    "reused": reused,
                    "skipped": skipped,
                    "failed": failed,
                    "script_units_assigned": assigned,
                    "missing_roles": missing,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                }
                job.finished_at = utc_now()
                job.updated_at = utc_now()
                session.flush()
                self._refresh_production_status(session, book)
        except Exception as exc:  # noqa: BLE001
            logger.exception("自动选角任务 %s 异常终止", job_id)
            try:
                with session_scope(self.session_factory) as session:
                    job = session.get(GenerationJob, job_id)
                    if job is None or job.status == "cancelled":
                        return
                    job.status = "failed"
                    job.error_code = str(getattr(exc, "code", "casting_failed"))
                    job.error_message = str(getattr(exc, "message", exc))[:2000]
                    job.finished_at = utc_now()
                    job.updated_at = utc_now()
                    self._fail_job(session, job_id, exc)
            except Exception:  # noqa: BLE001
                logger.exception("写入自动选角失败状态时出错")

    def _process_target(
        self, book_id: str, target: _Target, job_id: str
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            if self._cast_for_target(session, book.id, target) is not None:
                return {"status": "skipped"}
            picked = self._pick_actor(session, book.id, target)
            book_title = book.title
        try:
            if picked is not None:
                actor_id, confidence, reason = picked
                self._prepare_actor_reference(actor_id)
                outcome = self._create_cast(
                    book_id,
                    target,
                    actor_id,
                    source="auto",
                    confidence=confidence,
                    reason=reason,
                    job_id=job_id,
                )
                if outcome.get("status") == "skipped":
                    return outcome
                if confidence < LOW_CONFIDENCE_THRESHOLD:
                    self._target_review(
                        book_id,
                        target,
                        job_id,
                        code="cast_low_confidence",
                        severity="info",
                        title="自动选角复用了置信度较低的配音员",
                        message=f"匹配分数 {confidence:.2f}，建议试听后再继续。",
                    )
                return {**outcome, "status": "reused"}
            actor_id = self._generate_actor(book_id, book_title, target)
            self._prepare_actor_reference(actor_id)
            outcome = self._create_cast(
                book_id,
                target,
                actor_id,
                source="auto",
                confidence=0.8,
                reason="没有合适的历史配音员，已自动生成并绑定",
                job_id=job_id,
            )
            if outcome.get("status") == "skipped":
                return outcome
            return {**outcome, "status": "generated"}
        except DependencyUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动选角目标失败 %s: %s", target.character_name or target.scope, exc)
            code = str(getattr(exc, "code", "cast_generation_failed"))
            message = _clean(getattr(exc, "message", exc), 1200) or "选角失败"
            self._target_review(
                book_id,
                target,
                job_id,
                code=code if code.startswith("cast_") else "cast_generation_failed",
                severity="warning",
                title=f"“{target.character_name or '旁白'}”选角失败",
                message=message,
            )
            return {"status": "failed", "error": message}

    def _generate_actor(
        self, book_id: str, book_title: str, target: _Target
    ) -> str:
        reference_text = self.settings.voice_design.reference_text
        seed = int(
            hashlib.sha256(
                f"{book_id}:{target.scope}:{target.character_id}:{target.age_stage}".encode(
                    "utf-8"
                )
            ).hexdigest()[:8],
            16,
        ) % (2**31 - 1)
        candidate = self._design_voice(
            target.description,
            reference_text,
            seed,
            language=target.language,
        )
        audio = candidate.audio
        if audio is None or not audio.audio_bytes:
            raise AppError(
                "声音设计没有返回音频",
                status_code=502,
                code="voice_design_empty",
            )
        info = self._audio_info(audio.audio_bytes)
        actor_id = new_id("va")
        path = self._actor_reference_path(actor_id, processed=False)
        atomic_write_bytes(path, audio.audio_bytes)
        relative = relative_to_data(path, self.settings.data_dir)
        params = {
            "language": "zh-CN",
            "provider": audio.provider,
            "model": audio.model,
            "is_mock": audio.is_mock,
            "checksum_sha256": hashlib.sha256(audio.audio_bytes).hexdigest(),
            "reference_processed": False,
            "auto_cast": True,
        }
        if target.character_name:
            name = f"{target.character_name}声线"
            if target.age_stage:
                name += f" · {_age_label(target.age_stage)}"
        else:
            name = f"旁白声线 · {book_title[:16]}"
        with session_scope(self.session_factory) as session:
            self._book(session, book_id)
            actor = VoiceActor(
                id=actor_id,
                display_name=name[:120],
                description=target.description,
                gender_presentation=target.gender
                if target.gender in {"male", "female", "neutral", "unknown"}
                else "unknown",
                age_impression=target.age
                if target.age in AGE_ORDER
                else "unknown",
                language="zh-CN",
                timbre_tags_json=_clean_tags(target.timbre_tags),
                personality_tags_json=_clean_tags(target.personality_tags),
                reference_text=reference_text,
                reference_audio_path=relative,
                instruct_text=candidate.instruct_text,
                seed=candidate.seed,
                provider=audio.provider,
                model=audio.model,
                generation_params_json=params,
                sample_rate=info["sample_rate"],
                duration_ms=info["duration_ms"],
                status="ready",
                source="ai",
                source_book_id=book_id,
                is_favorite=False,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(actor)
            session.flush()
        return actor_id

    def _create_cast(
        self,
        book_id: str,
        target: _Target,
        actor_id: str,
        *,
        source: str,
        confidence: float,
        reason: str,
        job_id: str,
    ) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            book = self._book(session, book_id)
            if self._cast_for_target(session, book.id, target) is not None:
                return {"status": "skipped"}
            actor = self._actor(session, actor_id)
            if actor.status != "ready":
                raise ConflictError("生成的配音员没有就绪", code="cast_actor_not_ready")
            self._actor_file(actor)
            cast = VoiceCast(
                id=new_id("cast"),
                book_id=book.id,
                character_id=target.character_id,
                voice_actor_id=actor.id,
                scope=target.scope,
                age_stage_condition=target.age_stage,
                state_condition=None,
                priority=10 if target.scope == "character_stage" else 0,
                source=source,
                confidence=max(0.0, min(1.0, confidence)),
                is_active=True,
                reason=_clean(reason, 500),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(cast)
            session.flush()
            self._resolve_owner_reviews(
                session,
                book_id=book.id,
                entity_type="book" if target.scope == "narrator" else "character",
                entity_id=book.id if target.scope == "narrator" else target.character_id,
                note="自动选角已完成",
            )
            return {"status": "created", "cast_id": cast.id}

    # -------------------------------------------------------------- matching

    def _pick_actor(
        self, session: Session, book_id: str, target: _Target
    ) -> tuple[str, float, str] | None:
        used = self._used_actor_ids(session, book_id)
        rows = list(
            session.scalars(
                select(VoiceActor).where(
                    VoiceActor.status == "ready",
                    VoiceActor.reference_audio_path.is_not(None),
                )
            )
        )
        best: tuple[float, VoiceActor] | None = None
        for actor in rows:
            if actor.id in used:
                continue
            try:
                self._actor_file(actor)
            except (NotFoundError, AppError):
                continue
            score = self._score_actor(target, actor)
            if score is None:
                continue
            if best is None or score > best[0]:
                best = (score, actor)
        if best is None:
            return None
        score, actor = best
        reasons = [
            f"gender={actor.gender_presentation}",
            f"age={actor.age_impression}",
        ]
        if actor.is_favorite:
            reasons.append("已收藏")
        return actor.id, round(score, 3), "复用历史配音员：" + "，".join(reasons)

    def _score_actor(self, target: _Target, actor: VoiceActor) -> float | None:
        score = 0.0
        actor_gender = actor.gender_presentation or "unknown"
        target_gender = target.gender or "unknown"
        if target_gender in {"male", "female"}:
            if actor_gender in {"male", "female"} and actor_gender != target_gender:
                return None
            if actor_gender == target_gender:
                score += 0.35
            elif actor_gender == "neutral":
                score += 0.12
            else:
                score += 0.05
        elif target_gender == "neutral":
            if actor_gender == "neutral":
                score += 0.28
            elif actor_gender in {"male", "female"}:
                score += 0.12
            else:
                score += 0.08
        else:
            score += 0.15 if actor_gender != "unknown" else 0.08

        target_age = target.age if target.age in AGE_ORDER else "unknown"
        actor_age = actor.age_impression if actor.age_impression in AGE_ORDER else "unknown"
        if target_age != "unknown":
            if actor_age == "unknown":
                score += 0.04
            else:
                distance = abs(AGE_ORDER[target_age] - AGE_ORDER[actor_age])
                if distance > 2:
                    return None
                score += 0.28 if distance == 0 else 0.12 if distance == 1 else 0.0
        else:
            score += 0.08
        if target.language == actor.language:
            score += 0.08
        target_tags = {
            str(item).strip().casefold()
            for item in list(target.timbre_tags) + list(target.personality_tags)
            if str(item).strip()
        }
        actor_tags = {
            str(item).strip().casefold()
            for item in list(actor.timbre_tags_json or [])
            + list(actor.personality_tags_json or [])
            if str(item).strip()
        }
        if target_tags and actor_tags:
            score += 0.14 * (len(target_tags & actor_tags) / len(target_tags))
        if actor.is_favorite:
            score += 0.04
        if score < REUSE_THRESHOLD:
            return None
        return min(1.0, score)

    # --------------------------------------------------------------- targets

    def _targets(
        self, session: Session, book_id: str, bible: BookBible
    ) -> list[_Target]:
        active_casts = self._active_casts(session, book_id)
        cast_map = self._cast_map(active_casts)
        targets: list[_Target] = []
        if not any(cast.scope == "narrator" for cast in active_casts):
            targets.append(
                _Target(
                    scope="narrator",
                    description=self.settings.voice_design.narrator_description,
                    gender="unknown",
                    age="adult",
                    timbre_tags=["旁白", "叙述"],
                )
            )
        characters = self._plan_characters(session, book_id, bible)
        for item in characters:
            if not item["cast_required"]:
                continue
            character: Character = item["character"]
            if ("character", character.id, None) not in cast_map:
                targets.append(item["base_target"])
            stages = item["stages"]
            if len(stages) > 1:
                for stage in stages:
                    key = ("character_stage", character.id, stage["age_stage"])
                    if key not in cast_map:
                        targets.append(stage["target"])
        return targets

    def _plan_characters(
        self, session: Session, book_id: str, bible: BookBible | None
    ) -> list[dict[str, Any]]:
        characters = list(
            session.scalars(
                select(Character)
                .where(
                    Character.book_id == book_id,
                    Character.merged_into_id.is_(None),
                )
                .order_by(Character.canonical_name.asc(), Character.id.asc())
            )
        )
        if not characters:
            return []
        ids = [item.id for item in characters]
        arcs = (
            list(
                session.scalars(
                    select(CharacterArc)
                    .where(
                        CharacterArc.bible_id == bible.id,
                        CharacterArc.character_id.in_(ids),
                    )
                    .order_by(CharacterArc.arc_index.asc(), CharacterArc.id.asc())
                )
            )
            if bible is not None
            else []
        )
        states = list(
            session.execute(
                select(SceneCharacter, Scene)
                .join(Scene, Scene.id == SceneCharacter.scene_id)
                .join(Chapter, Chapter.id == Scene.chapter_id)
                .where(
                    Chapter.book_id == book_id,
                    Chapter.scene_version > 0,
                    Scene.analysis_version == Chapter.scene_version,
                    SceneCharacter.character_id.in_(ids),
                )
            ).all()
        )
        speaker_ids = set(
            session.scalars(
                select(ScriptUnit.speaker_id)
                .join(ScriptVersion, ScriptVersion.id == ScriptUnit.script_version_id)
                .where(
                    ScriptVersion.book_id == book_id,
                    ScriptVersion.is_active.is_(True),
                    ScriptUnit.speaker_type == "character",
                    ScriptUnit.speaker_id.is_not(None),
                )
                .distinct()
            )
        )
        scene_ids = {state.character_id for state, _scene in states}
        arc_map: dict[str, list[CharacterArc]] = {}
        for arc in arcs:
            arc_map.setdefault(arc.character_id, []).append(arc)
        state_map: dict[str, list[SceneCharacter]] = {}
        for state, _scene in states:
            state_map.setdefault(state.character_id, []).append(state)
        result: list[dict[str, Any]] = []
        for character in characters:
            required = (
                character.role_tier in REQUIRED_ROLE_TIERS
                or character.id in scene_ids
                or character.id in speaker_ids
            )
            stages = self._character_stages(
                character,
                arc_map.get(character.id, []),
                state_map.get(character.id, []),
            )
            base_target = self._base_target(character, state_map.get(character.id, []))
            for stage in stages:
                stage["target"] = self._stage_target(
                    character, stage, state_map.get(character.id, [])
                )
            result.append(
                {
                    "character": character,
                    "cast_required": required,
                    "stages": stages,
                    "base_target": base_target,
                }
            )
        result.sort(
            key=lambda item: (
                ROLE_ORDER.get(item["character"].role_tier, 4),
                item["character"].canonical_name,
                item["character"].id,
            )
        )
        return result

    def _character_stages(
        self,
        character: Character,
        arcs: list[CharacterArc],
        states: list[SceneCharacter],
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for arc in arcs:
            if arc.age_stage in AGE_ORDER and arc.age_stage != "unknown":
                hint = _clean(
                    (arc.voice_traits_json or {}).get("voice_hint")
                    or (arc.voice_traits_json or {}).get("description"),
                    255,
                )
                found.setdefault(
                    arc.age_stage,
                    {
                        "age_stage": arc.age_stage,
                        "label": _clean(arc.label, 120) or _age_label(arc.age_stage),
                        "voice_hint": hint,
                    },
                )
        for state in states:
            if state.age_stage in AGE_ORDER and state.age_stage != "unknown":
                found.setdefault(
                    state.age_stage,
                    {
                        "age_stage": state.age_stage,
                        "label": _age_label(state.age_stage),
                    },
                )
        return sorted(
            found.values(),
            key=lambda item: (AGE_ORDER.get(item["age_stage"], 6), item["label"]),
        )

    def _base_target(
        self, character: Character, states: list[SceneCharacter]
    ) -> _Target:
        note = next(
            (state.voice_note for state in states if _clean(state.voice_note, 200)),
            None,
        )
        description = describe_character_voice(
            gender=character.gender,
            age_range=character.age_range,
            voice_description=character.voice_description,
            personality_desc=character.personality_desc,
        )
        if note:
            description = f"{description}；当前声音提示：{_clean(note, 120)}"
        return _Target(
            scope="character",
            character_id=character.id,
            character_name=character.canonical_name,
            description=description,
            gender=_gender_for_character(character.gender),
            age=character.age_range
            if character.age_range in AGE_ORDER
            else "unknown",
            role_tier=character.role_tier,
            timbre_tags=[
                _age_label(character.age_range),
                character.role_tier,
            ],
            personality_tags=_clean_tags(character.personality_json, 8),
            voice_note=_clean(note, 300),
        )

    def _stage_target(
        self,
        character: Character,
        stage: dict[str, Any],
        states: list[SceneCharacter],
    ) -> _Target:
        age_stage = stage["age_stage"]
        note = next(
            (
                state.voice_note
                for state in states
                if state.age_stage == age_stage and _clean(state.voice_note, 200)
            ),
            None,
        )
        hint = _clean(stage.get("voice_hint"), 200)
        description = describe_character_voice(
            gender=character.gender,
            age_range=age_stage,
            voice_description=character.voice_description,
            personality_desc=character.personality_desc,
        )
        if hint:
            description = f"{description}；人物弧声音提示：{hint}"
        if note:
            description = f"{description}；此阶段声音提示：{_clean(note, 120)}"
        return _Target(
            scope="character_stage",
            character_id=character.id,
            character_name=character.canonical_name,
            age_stage=age_stage,
            label=stage["label"],
            description=description,
            gender=_gender_for_character(character.gender),
            age=age_stage,
            role_tier=character.role_tier,
            timbre_tags=[
                _age_label(age_stage),
                stage["label"],
                character.role_tier,
            ],
            personality_tags=_clean_tags(character.personality_json, 8),
            voice_note=_clean(note, 300),
        )

    # --------------------------------------------------------------- plan view

    def _plan_response(self, session: Session, book: Book) -> BookCastsResponse:
        bible = self._active_bible(session, book.id)
        casts = self._active_casts(session, book.id)
        cast_map = self._cast_map(casts)
        actor_ids = [cast.voice_actor_id for cast in casts]
        actors = {
            row.id: row
            for row in session.scalars(
                select(VoiceActor).where(VoiceActor.id.in_(actor_ids))
            )
        } if actor_ids else {}
        narrator = cast_map.get(("narrator", None, None))
        characters = self._plan_characters(session, book.id, bible)
        missing: list[str] = []
        if narrator is None:
            missing.append("旁白")
        character_rows: list[CastCharacterPlanOut] = []
        for item in characters:
            character = item["character"]
            base_cast = cast_map.get(("character", character.id, None))
            if item["cast_required"] and base_cast is None:
                missing.append(character.canonical_name)
            stage_rows: list[CastStagePlanOut] = []
            for stage in item["stages"]:
                stage_cast = cast_map.get(
                    ("character_stage", character.id, stage["age_stage"])
                )
                stage_rows.append(
                    CastStagePlanOut(
                        age_stage=stage["age_stage"],
                        label=stage["label"],
                        cast=self._cast_out(
                            stage_cast, actors, character.canonical_name
                        )
                        if stage_cast
                        else None,
                    )
                )
            character_rows.append(
                CastCharacterPlanOut(
                    character_id=character.id,
                    name=character.canonical_name,
                    gender=character.gender,
                    age_range=character.age_range,
                    role_tier=character.role_tier,
                    cast_required=bool(item["cast_required"]),
                    base_cast=self._cast_out(
                        base_cast, actors, character.canonical_name
                    )
                    if base_cast
                    else None,
                    stages=stage_rows,
                    missing=bool(item["cast_required"] and base_cast is None),
                )
            )
        reviews = self._open_cast_reviews(session, book.id)
        job = self._latest_job(session, book.id)
        running = job is not None and job.status in ACTIVE_JOB_STATUSES
        return BookCastsResponse(
            book_id=book.id,
            bible_ready=bible is not None
            and bible.status in {"approved", "needs_review"},
            narrator=self._cast_out(narrator, actors) if narrator else None,
            characters=character_rows,
            cast_count=len(casts),
            missing_roles=missing,
            open_reviews=len(reviews),
            ready=bool(
                not running
                and bible is not None
                and narrator is not None
                and not missing
                and not any(
                    item.severity in {"blocking", "warning"} for item in reviews
                )
            ),
            job=to_job_out(job) if job else None,
        )

    def _cast_out(
        self,
        cast: VoiceCast | None,
        actors: dict[str, VoiceActor],
        character_name: str | None = None,
    ) -> VoiceCastOut | None:
        if cast is None:
            return None
        actor = actors.get(cast.voice_actor_id)
        return VoiceCastOut(
            id=cast.id,
            book_id=cast.book_id,
            character_id=cast.character_id,
            character_name=character_name,
            voice_actor_id=cast.voice_actor_id,
            scope=cast.scope,
            age_stage_condition=cast.age_stage_condition,
            state_condition=cast.state_condition,
            priority=cast.priority,
            source=cast.source,
            confidence=cast.confidence,
            is_active=cast.is_active,
            reason=cast.reason,
            actor=CastActorOut(
                id=actor.id,
                display_name=actor.display_name,
                gender_presentation=actor.gender_presentation,
                age_impression=actor.age_impression,
                language=actor.language,
                timbre_tags=list(actor.timbre_tags_json or []),
                personality_tags=list(actor.personality_tags_json or []),
                is_favorite=actor.is_favorite,
                is_mock=_actor_is_mock(actor),
                audio_url=f"/api/v1/voice-actors/{actor.id}/audio",
            )
            if actor
            else None,
            created_at=cast.created_at,
            updated_at=cast.updated_at,
        )

    # ---------------------------------------------------------------- sync

    def _sync_script_units(self, session: Session, book_id: str) -> int:
        version_ids = list(
            session.scalars(
                select(ScriptVersion.id).where(
                    ScriptVersion.book_id == book_id,
                    ScriptVersion.is_active.is_(True),
                )
            )
        )
        if not version_ids:
            return 0
        casts = self._active_casts(session, book_id)
        cast_map = self._cast_map(casts)
        units = list(
            session.scalars(
                select(ScriptUnit).where(
                    ScriptUnit.script_version_id.in_(version_ids)
                )
            )
        )
        state_ids = {
            unit.character_state_id
            for unit in units
            if unit.character_state_id
        }
        state_ages = {
            row.id: row.age_stage
            for row in session.scalars(
                select(SceneCharacter).where(SceneCharacter.id.in_(state_ids))
            )
        } if state_ids else {}
        narrator = cast_map.get(("narrator", None, None))
        assigned = 0
        for unit in units:
            desired: str | None = None
            if unit.unit_type in TTS_UNIT_TYPES:
                if unit.speaker_type == "narrator":
                    desired = narrator.id if narrator else None
                elif unit.speaker_type == "character" and unit.speaker_id:
                    stage = state_ages.get(unit.character_state_id or "")
                    if stage:
                        stage_cast = cast_map.get(
                            ("character_stage", unit.speaker_id, stage)
                        )
                        if stage_cast:
                            desired = stage_cast.id
                    if desired is None:
                        base = cast_map.get(("character", unit.speaker_id, None))
                        desired = base.id if base else None
            if unit.voice_cast_id == desired:
                continue
            if (
                unit.audio_status == "ready"
                and unit.current_audio_asset_id
                and unit.voice_cast_id != desired
            ):
                unit.audio_status = "stale"
            unit.voice_cast_id = desired
            unit.updated_at = utc_now()
            assigned += 1
        return assigned

    def _review_unassigned_units(
        self, session: Session, book_id: str, job_id: str
    ) -> None:
        version_ids = list(
            session.scalars(
                select(ScriptVersion.id).where(
                    ScriptVersion.book_id == book_id,
                    ScriptVersion.is_active.is_(True),
                )
            )
        )
        if not version_ids:
            return
        rows = list(
            session.scalars(
                select(ScriptUnit).where(
                    ScriptUnit.script_version_id.in_(version_ids),
                    ScriptUnit.unit_type.in_(TTS_UNIT_TYPES),
                    ScriptUnit.voice_cast_id.is_(None),
                )
            )
        )
        if not rows:
            return
        names = dict(
            session.execute(
                select(Character.id, Character.canonical_name).where(
                    Character.id.in_(
                        [row.speaker_id for row in rows if row.speaker_id]
                    )
                )
            ).all()
        ) if any(row.speaker_id for row in rows) else {}
        speakers: dict[str, int] = {}
        for row in rows:
            label = (
                names.get(row.speaker_id or "")
                or row.speaker_name_snapshot
                or ("旁白" if row.speaker_type == "narrator" else "未识别说话人")
            )
            speakers[label] = speakers.get(label, 0) + 1
        self._review(
            session,
            book_id=book_id,
            entity_type="book",
            entity_id=book_id,
            code="cast_script_unassigned",
            severity="warning",
            title="部分剧本单元没有可用配音员",
            message="这些单元在生成音频前需要补齐选角或说话人。",
            payload={
                "job_id": job_id,
                "units": len(rows),
                "speakers": speakers,
            },
        )

    def _resolve_script_unassigned_if_clear(
        self, session: Session, book_id: str
    ) -> None:
        version_ids = list(
            session.scalars(
                select(ScriptVersion.id).where(
                    ScriptVersion.book_id == book_id,
                    ScriptVersion.is_active.is_(True),
                )
            )
        )
        remaining = 0
        if version_ids:
            remaining = int(
                session.scalar(
                    select(func.count(ScriptUnit.id)).where(
                        ScriptUnit.script_version_id.in_(version_ids),
                        ScriptUnit.unit_type.in_(TTS_UNIT_TYPES),
                        ScriptUnit.voice_cast_id.is_(None),
                    )
                )
                or 0
            )
        if remaining:
            return
        rows = list(
            session.scalars(
                select(ReviewItem).where(
                    ReviewItem.book_id == book_id,
                    ReviewItem.entity_type == "book",
                    ReviewItem.entity_id == book_id,
                    ReviewItem.code == "cast_script_unassigned",
                    ReviewItem.status == "open",
                )
            )
        )
        now = utc_now()
        for row in rows:
            row.status = "resolved"
            row.resolution_note = "所有可配音剧本单元均已绑定配音员"
            row.resolved_at = now
            row.updated_at = now

    def _missing_roles(self, session: Session, book_id: str) -> list[str]:
        casts = self._active_casts(session, book_id)
        cast_map = self._cast_map(casts)
        missing: list[str] = []
        if ("narrator", None, None) not in cast_map:
            missing.append("旁白")
        bible = self._active_bible(session, book_id)
        for item in self._plan_characters(session, book_id, bible):
            if not item["cast_required"]:
                continue
            if ("character", item["character"].id, None) not in cast_map:
                missing.append(item["character"].canonical_name)
        return missing

    # -------------------------------------------------------------- reviews

    def _review(
        self,
        session: Session,
        *,
        book_id: str,
        entity_type: str,
        entity_id: str | None,
        code: str,
        severity: str,
        title: str,
        message: str | None,
        payload: dict[str, Any],
    ) -> ReviewItem:
        existing = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.entity_type == entity_type,
                ReviewItem.entity_id == entity_id,
                ReviewItem.code == code,
                ReviewItem.status == "open",
            )
        )
        if existing is None:
            item = ReviewItem(
                id=new_id("review"),
                book_id=book_id,
                entity_type=entity_type,
                entity_id=entity_id,
                code=code,
                severity=severity,
                title=title[:255],
                message=message,
                payload_json=payload,
                status="open",
                source="system",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(item)
            session.flush()
            return item
        existing.severity = severity
        existing.title = title[:255]
        existing.message = message
        existing.payload_json = payload
        existing.updated_at = utc_now()
        return existing

    def _target_review(
        self,
        book_id: str,
        target: _Target,
        job_id: str,
        *,
        code: str,
        severity: str,
        title: str,
        message: str,
    ) -> None:
        with session_scope(self.session_factory) as session:
            if target.scope == "narrator":
                entity_type = "book"
                entity_id = book_id
                if code in {"cast_generation_failed", "cast_role_unfilled"}:
                    code = "cast_narrator_unfilled"
            else:
                entity_type = "character"
                entity_id = target.character_id
                if code == "cast_generation_failed":
                    code = "cast_role_unfilled"
            self._review(
                session,
                book_id=book_id,
                entity_type=entity_type,
                entity_id=entity_id,
                code=code,
                severity=severity,
                title=title,
                message=message,
                payload={
                    "job_id": job_id,
                    "scope": target.scope,
                    "character_id": target.character_id,
                    "age_stage": target.age_stage,
                },
            )

    def _resolve_owner_reviews(
        self,
        session: Session,
        *,
        book_id: str,
        entity_type: str,
        entity_id: str | None,
        note: str,
    ) -> None:
        if entity_id is None:
            return
        rows = list(
            session.scalars(
                select(ReviewItem).where(
                    ReviewItem.book_id == book_id,
                    ReviewItem.entity_type == entity_type,
                    ReviewItem.entity_id == entity_id,
                    ReviewItem.status == "open",
                    ReviewItem.code.startswith(_CAST_REVIEW_PREFIX),
                )
            )
        )
        now = utc_now()
        for row in rows:
            row.status = "resolved"
            row.resolution_note = note
            row.resolved_at = now
            row.updated_at = now

    def _ignore_stale_reviews(
        self, session: Session, book_id: str, job_id: str
    ) -> None:
        rows = list(
            session.scalars(
                select(ReviewItem).where(
                    ReviewItem.book_id == book_id,
                    ReviewItem.status == "open",
                    ReviewItem.code.startswith(_CAST_REVIEW_PREFIX),
                )
            )
        )
        now = utc_now()
        for row in rows:
            payload = row.payload_json or {}
            if payload.get("job_id") == job_id:
                continue
            row.status = "ignored"
            row.resolution_note = "已被新的自动选角结果替代"
            row.resolved_at = now
            row.updated_at = now

    def _open_cast_reviews(self, session: Session, book_id: str) -> list[ReviewItem]:
        return list(
            session.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.book_id == book_id,
                    ReviewItem.status == "open",
                    ReviewItem.code.startswith(_CAST_REVIEW_PREFIX),
                )
                .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
            )
        )

    # ------------------------------------------------------------- state

    def _refresh_production_status(self, session: Session, book: Book) -> None:
        if self._active_job(session, book.id) is not None:
            return
        missing = self._missing_roles(session, book.id)
        reviews = self._open_cast_reviews(session, book.id)
        if missing:
            book.production_status = (
                "failed"
                if not self._active_casts(session, book.id)
                else "needs_review"
            )
        elif any(item.severity in {"blocking", "warning"} for item in reviews):
            book.production_status = "needs_review"
        else:
            book.production_status = "ready"
        book.updated_at = utc_now()

    def _fail_job(self, session: Session, job_id: str, error: Exception) -> None:
        job = session.get(GenerationJob, job_id)
        if job is None or job.status == "cancelled":
            return
        book = session.get(Book, job.book_id)
        if book is not None:
            has_casts = bool(self._active_casts(session, book.id))
            book.production_status = "needs_review" if has_casts else "failed"
            book.updated_at = utc_now()
        code = str(getattr(error, "code", "casting_failed"))
        message = str(getattr(error, "message", error))[:1200]
        self._review(
            session,
            book_id=job.book_id,
            entity_type="book",
            entity_id=job.book_id,
            code="cast_job_failed",
            severity="blocking",
            title="自动选角任务失败",
            message=message,
            payload={"job_id": job.id, "error_code": code},
        )

    # ---------------------------------------------------------------- helpers

    def _book(self, session: Session, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _actor(self, session: Session, actor_id: str) -> VoiceActor:
        validate_id(actor_id, "va")
        actor = session.get(VoiceActor, actor_id)
        if actor is None:
            raise NotFoundError("配音员不存在")
        return actor

    def _active_bible(self, session: Session, book_id: str) -> BookBible | None:
        return session.scalar(
            select(BookBible)
            .where(BookBible.book_id == book_id, BookBible.is_active.is_(True))
            .order_by(BookBible.version.desc())
            .limit(1)
        )

    def _active_job(self, session: Session, book_id: str) -> GenerationJob | None:
        return session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.target_type == "book",
                GenerationJob.target_id == book_id,
                GenerationJob.job_type == JOB_TYPE,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )

    def _latest_job(self, session: Session, book_id: str) -> GenerationJob | None:
        return session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.target_type == "book",
                GenerationJob.target_id == book_id,
                GenerationJob.job_type == JOB_TYPE,
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )

    def _active_casts(self, session: Session, book_id: str) -> list[VoiceCast]:
        return list(
            session.scalars(
                select(VoiceCast)
                .where(VoiceCast.book_id == book_id, VoiceCast.is_active.is_(True))
                .order_by(VoiceCast.priority.desc(), VoiceCast.created_at.asc())
            )
        )

    def _cast_map(
        self, casts: list[VoiceCast]
    ) -> dict[tuple[str, str | None, str | None], VoiceCast]:
        result: dict[tuple[str, str | None, str | None], VoiceCast] = {}
        for cast in casts:
            result[(cast.scope, cast.character_id, cast.age_stage_condition)] = cast
        return result

    def _cast_for_target(
        self, session: Session, book_id: str, target: _Target
    ) -> VoiceCast | None:
        conditions = [
            VoiceCast.book_id == book_id,
            VoiceCast.scope == target.scope,
            VoiceCast.is_active.is_(True),
        ]
        if target.scope == "narrator":
            conditions.extend(
                [
                    VoiceCast.character_id.is_(None),
                    VoiceCast.age_stage_condition.is_(None),
                ]
            )
        else:
            conditions.append(VoiceCast.character_id == target.character_id)
            if target.scope == "character_stage":
                conditions.append(
                    VoiceCast.age_stage_condition == target.age_stage
                )
            else:
                conditions.append(VoiceCast.age_stage_condition.is_(None))
        return session.scalar(select(VoiceCast).where(*conditions).limit(1))

    def _used_actor_ids(self, session: Session, book_id: str) -> set[str]:
        return set(
            session.scalars(
                select(VoiceCast.voice_actor_id).where(
                    VoiceCast.book_id == book_id,
                    VoiceCast.is_active.is_(True),
                )
            )
        )

    def _usage_map(
        self, session: Session, actor_ids: list[str]
    ) -> dict[str, VoiceActorUsage]:
        if not actor_ids:
            return {}
        rows = session.execute(
            select(
                VoiceCast.voice_actor_id,
                func.count(VoiceCast.id),
                func.count(func.distinct(VoiceCast.book_id)),
                func.max(VoiceCast.created_at),
            )
            .where(
                VoiceCast.voice_actor_id.in_(actor_ids),
                VoiceCast.is_active.is_(True),
            )
            .group_by(VoiceCast.voice_actor_id)
        ).all()
        result: dict[str, VoiceActorUsage] = {}
        for actor_id, cast_count, book_count, last_used in rows:
            result[str(actor_id)] = VoiceActorUsage(
                active_cast_count=int(cast_count or 0),
                book_count=int(book_count or 0),
                last_used_at=last_used,
            )
        return result

    def _actor_out(
        self, actor: VoiceActor, usage: VoiceActorUsage | None = None
    ) -> VoiceActorOut:
        params = dict(actor.generation_params_json or {})
        public = {
            key: params[key]
            for key in (
                "checksum_sha256",
                "reference_processed",
                "raw_reference_path",
                "deess",
                "auto_cast",
            )
            if key in params
        }
        return VoiceActorOut(
            id=actor.id,
            display_name=actor.display_name,
            description=actor.description,
            gender_presentation=actor.gender_presentation,
            age_impression=actor.age_impression,
            language=actor.language,
            timbre_tags=list(actor.timbre_tags_json or []),
            personality_tags=list(actor.personality_tags_json or []),
            reference_text=actor.reference_text,
            instruct_text=actor.instruct_text,
            seed=actor.seed,
            provider=actor.provider,
            model=actor.model,
            generation_params=public,
            sample_rate=actor.sample_rate,
            duration_ms=actor.duration_ms,
            status=actor.status,
            source=actor.source,
            source_book_id=actor.source_book_id,
            is_favorite=actor.is_favorite,
            is_mock=_actor_is_mock(actor),
            audio_url=(
                f"/api/v1/voice-actors/{actor.id}/audio"
                if actor.reference_audio_path
                else None
            ),
            usage=usage or VoiceActorUsage(),
            created_at=actor.created_at,
            updated_at=actor.updated_at,
        )

    def _design_voice(
        self,
        description: str,
        reference_text: str,
        seed: int | None,
        *,
        language: str,
    ) -> VoiceCandidate:
        with VOICE_DESIGN_LOCK:
            return self.registry.voice_design.design_voice(
                description,
                reference_text,
                language,
                seed=seed,
            )

    def _audio_info(self, audio_bytes: bytes) -> dict[str, int]:
        if not audio_bytes or audio_bytes[:4] != b"RIFF":
            raise AppError(
                "VoiceDesign 返回的不是 WAV 音频",
                status_code=502,
                code="invalid_audio",
            )
        try:
            info = sf.info(io.BytesIO(audio_bytes))
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                f"VoiceDesign 返回的 WAV 无法解析：{exc}",
                status_code=502,
                code="invalid_audio",
            ) from exc
        if info.samplerate <= 0 or info.frames <= 0:
            raise AppError(
                "VoiceDesign 返回了空音频",
                status_code=502,
                code="invalid_audio",
            )
        return {
            "sample_rate": int(info.samplerate),
            "duration_ms": int(round(info.frames / info.samplerate * 1000)),
        }

    def _actor_reference_path(self, actor_id: str, *, processed: bool) -> Path:
        name = "reference_deessed.wav" if processed else "reference.wav"
        return self.settings.data_dir / "voice_actors" / actor_id / name

    def _actor_file(self, actor: VoiceActor) -> Path:
        if not actor.reference_audio_path:
            raise NotFoundError("该配音员还没有参考音频")
        path = resolve_relative(self.settings.data_dir, actor.reference_audio_path)
        if not path.is_file():
            raise NotFoundError("配音员参考音频文件缺失")
        return path

    def _prepare_actor_reference(self, actor_id: str) -> None:
        with session_scope(self.session_factory) as session:
            actor = self._actor(session, actor_id)
            if _actor_is_mock(actor):
                return
            params = dict(actor.generation_params_json or {})
            if params.get("reference_processed") is True:
                return
            config = self.settings.tts.reference_deess
            if not config.enabled:
                return
            source = self._actor_file(actor)
            processed = self.registry.tts.process_reference(
                source.read_bytes(),
                params={
                    "band_low": config.band_low,
                    "band_high": config.band_high,
                    "threshold_ratio": config.threshold_ratio,
                    "full_ratio": config.full_ratio,
                    "max_reduction_db": config.max_reduction_db,
                },
            )
            if processed is None or not processed.audio_bytes:
                return
            info = self._audio_info(processed.audio_bytes)
            target = self._actor_reference_path(actor.id, processed=True)
            atomic_write_bytes(target, processed.audio_bytes)
            params.setdefault("raw_reference_path", actor.reference_audio_path)
            params["reference_processed"] = True
            params["deess"] = processed.metadata.get("deess") or {
                "threshold_ratio": config.threshold_ratio,
                "full_ratio": config.full_ratio,
                "max_reduction_db": config.max_reduction_db,
            }
            actor.reference_audio_path = relative_to_data(
                target, self.settings.data_dir
            )
            actor.generation_params_json = params
            if processed.sample_rate:
                actor.sample_rate = processed.sample_rate
            actor.duration_ms = info["duration_ms"]
            actor.updated_at = utc_now()


def recover_interrupted_casting(session: Session) -> dict[str, int]:
    """启动时把遗留的自动选角任务标记失败，并恢复书籍生产状态。"""
    jobs = list(
        session.scalars(
            select(GenerationJob).where(
                GenerationJob.job_type == JOB_TYPE,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    )
    if not jobs:
        return {"jobs": 0, "books": 0}
    now = utc_now()
    book_ids: set[str] = set()
    for job in jobs:
        job.status = "failed"
        job.error_code = "interrupted"
        job.error_message = "应用启动时发现未完成的自动选角任务，已标记失败"
        job.finished_at = now
        job.updated_at = now
        book = session.get(Book, job.book_id)
        if book is not None:
            book_ids.add(book.id)
            active_casts = int(
                session.scalar(
                    select(func.count(VoiceCast.id)).where(
                        VoiceCast.book_id == book.id,
                        VoiceCast.is_active.is_(True),
                    )
                )
                or 0
            )
            book.production_status = "needs_review" if active_casts else "failed"
            book.updated_at = now
            existing = session.scalar(
                select(ReviewItem).where(
                    ReviewItem.book_id == book.id,
                    ReviewItem.entity_type == "book",
                    ReviewItem.entity_id == book.id,
                    ReviewItem.code == "cast_job_failed",
                    ReviewItem.status == "open",
                )
            )
            if existing is None:
                session.add(
                    ReviewItem(
                        id=new_id("review"),
                        book_id=book.id,
                        entity_type="book",
                        entity_id=book.id,
                        code="cast_job_failed",
                        severity="blocking",
                        title="自动选角任务中断",
                        message="应用重启时任务尚未完成，请重新启动自动选角。",
                        payload_json={"job_id": job.id, "error_code": "interrupted"},
                        status="open",
                        source="system",
                        created_at=now,
                        updated_at=now,
                    )
                )
    return {"jobs": len(jobs), "books": len(book_ids)}