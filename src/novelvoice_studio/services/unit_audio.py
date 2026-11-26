"""v0.5.0 P7：选中剧本单元的同步音频生成、试听与版本。

设计约束：

1. 只生成旁白/对白/内心独白；音效、环境与停顿 cue 不进入 TTS；
2. 声音来自书内当前选角绑定的全局配音员，参考音只读取数据目录内的文件；
3. 音频仍按 ``vNNN.wav`` 落盘，二进制不入 SQLite，版本只增不减；
4. 文本、表演字段或配音员/参考音变化时，已有音频标记 stale；
5. 生成失败写入异常收件箱，成功后自动收起对应失败项。
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import soundfile as sf
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    AudioAsset,
    Book,
    ReviewItem,
    SceneCharacter,
    ScriptUnit,
    ScriptVersion,
    VoiceActor,
    VoiceCast,
)
from novelvoice_studio.schemas.unit_audio import (
    ScriptUnitAudioOut,
    UnitAudioAssetOut,
    UnitAudioVersionListResponse,
)
from novelvoice_studio.services.audio import TTS_LOCK
from novelvoice_studio.services.paths import (
    BookPaths,
    atomic_write_bytes,
    new_id,
    relative_to_data,
    resolve_relative,
    validate_id,
)

TTS_UNIT_TYPES = {"narration", "dialogue", "inner_monologue"}
CUE_UNIT_TYPES = {"sfx", "ambience", "pause"}
AUDIO_FAILURE_CODE = "audio_generation_failed"
AUDIO_VOICE_CODE = "audio_voice_missing"

_SUBTYPE_BITS = {
    "PCM_S8": 8,
    "PCM_U8": 8,
    "PCM_16": 16,
    "PCM_24": 24,
    "PCM_32": 32,
    "FLOAT": 32,
    "DOUBLE": 64,
}


def render_hash_for(unit: ScriptUnit, actor: VoiceActor | None) -> str:
    """TTS 输入指纹：任一字段变化都需要重新生成。"""

    payload = {
        "unit_id": unit.id,
        "text": (unit.text or "").strip(),
        "emotion": unit.emotion,
        "intensity": unit.intensity,
        "delivery": unit.delivery,
        "tts_instruction": unit.tts_instruction or "",
        "speed": round(float(unit.speed or 1.0), 4),
        "actor": None
        if actor is None
        else {
            "id": actor.id,
            "reference_audio_path": actor.reference_audio_path,
            "description": actor.description,
            "instruct_text": actor.instruct_text,
            "seed": actor.seed,
            "language": actor.language,
            "status": actor.status,
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cast_matches(unit: ScriptUnit, cast: VoiceCast, age_stage: str | None) -> bool:
    if not cast.is_active or cast.book_id != unit.book_id:
        return False
    if unit.speaker_type == "narrator":
        return cast.scope == "narrator"
    if unit.speaker_type != "character" or not unit.speaker_id:
        return False
    if cast.character_id != unit.speaker_id:
        return False
    if cast.scope == "character_stage":
        return bool(age_stage) and cast.age_stage_condition == age_stage
    return cast.scope == "character"


def _active_casts(session: Session, book_id: str) -> list[VoiceCast]:
    return list(
        session.scalars(
            select(VoiceCast)
            .where(
                VoiceCast.book_id == book_id,
                VoiceCast.is_active.is_(True),
            )
            .order_by(
                VoiceCast.priority.desc(),
                VoiceCast.updated_at.desc(),
                VoiceCast.id.asc(),
            )
        )
    )


def _state_age(session: Session, unit: ScriptUnit) -> str | None:
    if not unit.character_state_id:
        return None
    state = session.get(SceneCharacter, unit.character_state_id)
    return state.age_stage if state is not None else None


def _find_cast(
    session: Session,
    unit: ScriptUnit,
    casts: list[VoiceCast] | None = None,
) -> VoiceCast | None:
    age_stage = _state_age(session, unit)
    rows = casts if casts is not None else _active_casts(session, unit.book_id)
    if unit.voice_cast_id:
        for cast in rows:
            if cast.id == unit.voice_cast_id and _cast_matches(unit, cast, age_stage):
                return cast
    for cast in rows:
        if _cast_matches(unit, cast, age_stage):
            return cast
    return None


def _load_actor(session: Session, cast: VoiceCast | None) -> VoiceActor | None:
    return session.get(VoiceActor, cast.voice_actor_id) if cast is not None else None


def resolve_unit_voices(
    session: Session, units: list[ScriptUnit]
) -> dict[str, tuple[VoiceCast | None, VoiceActor | None]]:
    """为列表视图批量解析当前选角，避免前端每单元再查一次。"""

    casts_by_book: dict[str, list[VoiceCast]] = {}
    actors: dict[str, VoiceActor | None] = {}
    result: dict[str, tuple[VoiceCast | None, VoiceActor | None]] = {}
    for unit in units:
        if unit.unit_type in CUE_UNIT_TYPES:
            result[unit.id] = (None, None)
            continue
        casts = casts_by_book.get(unit.book_id)
        if casts is None:
            casts = _active_casts(session, unit.book_id)
            casts_by_book[unit.book_id] = casts
        cast = _find_cast(session, unit, casts)
        if cast is None:
            result[unit.id] = (None, None)
            continue
        if cast.voice_actor_id not in actors:
            actors[cast.voice_actor_id] = _load_actor(session, cast)
        result[unit.id] = (cast, actors[cast.voice_actor_id])
    return result


def refresh_stale_units(session: Session, units: list[ScriptUnit]) -> int:
    """按当前文本与选角指纹，把已生成但输入变化的单元标为 stale。

    调用方负责 commit；只处理有当前音频的 TTS 单元，cue 永远不参与。
    """

    candidates = [
        unit
        for unit in units
        if unit.unit_type in TTS_UNIT_TYPES
        and unit.audio_status == "ready"
        and unit.current_audio_asset_id
    ]
    if not candidates:
        return 0
    cast_cache: dict[str, list[VoiceCast]] = {}
    actor_cache: dict[str, VoiceActor | None] = {}
    changed = 0
    for unit in candidates:
        casts = cast_cache.get(unit.book_id)
        if casts is None:
            casts = _active_casts(session, unit.book_id)
            cast_cache[unit.book_id] = casts
        cast = _find_cast(session, unit, casts)
        actor = None
        if cast is not None:
            if cast.voice_actor_id not in actor_cache:
                actor_cache[cast.voice_actor_id] = _load_actor(session, cast)
            actor = actor_cache[cast.voice_actor_id]
        if unit.render_hash != render_hash_for(unit, actor):
            unit.audio_status = "stale"
            unit.updated_at = utc_now()
            changed += 1
    return changed


class UnitAudioService:
    def __init__(
        self, settings: Settings, session: Session, registry: ProviderRegistry
    ) -> None:
        self.settings = settings
        self.session = session
        self.registry = registry

    # -------------------------------------------------------------- helpers

    def _book(self, book_id: str) -> Book:
        book = self.session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _unit(self, unit_id: str) -> ScriptUnit:
        validate_id(unit_id, "unit")
        unit = self.session.get(ScriptUnit, unit_id)
        if unit is None:
            raise NotFoundError("剧本单元不存在")
        self._book(unit.book_id)
        version = self.session.get(ScriptVersion, unit.script_version_id)
        if version is None or not version.is_active:
            raise ConflictError(
                "该剧本单元属于历史版本，请切换到当前剧本后再生成音频",
                code="script_version_inactive",
            )
        return unit

    def _ensure_tts_unit(self, unit: ScriptUnit) -> None:
        if unit.unit_type in CUE_UNIT_TYPES:
            raise ValidationError(
                "音效、环境与停顿单元不进入 TTS，不需要生成语音",
                code="audio_cue_not_applicable",
            )
        if unit.unit_type not in TTS_UNIT_TYPES:
            raise ValidationError("该剧本单元类型不支持音频生成")
        if not (unit.text or "").strip():
            raise ValidationError("剧本单元文本为空，无法生成音频")

    def _resolve_voice(
        self, unit: ScriptUnit
    ) -> tuple[VoiceCast, VoiceActor]:
        cast = _find_cast(self.session, unit)
        if cast is None:
            raise ConflictError(
                "该剧本单元还没有可用配音员，请先完成选角",
                code="unit_voice_missing",
                details={
                    "unit_id": unit.id,
                    "speaker_type": unit.speaker_type,
                    "speaker_id": unit.speaker_id,
                    "speaker_name": unit.speaker_name_snapshot,
                },
            )
        actor = _load_actor(self.session, cast)
        if actor is None:
            raise ConflictError(
                "选角绑定的配音员不存在，请重新选角",
                code="unit_voice_missing",
            )
        if actor.status != "ready":
            self._review_voice_missing(
                unit,
                actor,
                f"配音员“{actor.display_name}”状态为 {actor.status}，不能生成音频。",
            )
            raise ConflictError(
                f"配音员“{actor.display_name}”尚未就绪，请更换或重新生成配音员",
                code="actor_not_ready",
            )
        if not actor.reference_audio_path:
            self._review_voice_missing(
                unit, actor, f"配音员“{actor.display_name}”缺少参考音频。"
            )
            raise ConflictError(
                f"配音员“{actor.display_name}”缺少参考音频，请重新创建声线",
                code="actor_reference_missing",
            )
        path = resolve_relative(self.settings.data_dir, actor.reference_audio_path)
        if not path.is_file():
            self._review_voice_missing(
                unit, actor, f"配音员“{actor.display_name}”的参考音频文件缺失。"
            )
            raise ConflictError(
                f"配音员“{actor.display_name}”的参考音频文件缺失，请重新创建声线",
                code="actor_reference_missing",
            )
        if unit.voice_cast_id != cast.id:
            unit.voice_cast_id = cast.id
            unit.updated_at = utc_now()
        return cast, actor

    def _voice_profile(self, actor: VoiceActor) -> dict[str, Any]:
        path = resolve_relative(self.settings.data_dir, actor.reference_audio_path or "")
        return {
            "id": actor.id,
            "reference_path": str(path),
            "description": actor.description or "",
            "instruct_text": actor.instruct_text or "",
            "seed": actor.seed,
            "language": actor.language or "Chinese",
        }

    def _asset_out(
        self,
        asset: AudioAsset,
        current_id: str | None,
        actors: dict[str, VoiceActor],
    ) -> UnitAudioAssetOut:
        actor = actors.get(asset.voice_actor_id or "")
        return UnitAudioAssetOut(
            id=asset.id,
            script_unit_id=asset.script_unit_id or "",
            voice_actor_id=asset.voice_actor_id,
            voice_actor_name=actor.display_name if actor is not None else None,
            version=asset.version,
            format=asset.format,
            sample_rate=asset.sample_rate,
            channels=asset.channels,
            sample_width_bits=asset.sample_width_bits,
            duration_ms=asset.duration_ms,
            file_size_bytes=asset.file_size_bytes,
            tts_provider=asset.tts_provider,
            tts_model=asset.tts_model,
            is_valid=asset.is_valid,
            is_current=asset.id == current_id,
            audio_url=f"/api/v1/audio-assets/{asset.id}/audio",
            created_at=asset.created_at,
        )

    def _result(
        self,
        unit: ScriptUnit,
        asset: AudioAsset | None,
        actor: VoiceActor | None,
    ) -> ScriptUnitAudioOut:
        actors = {actor.id: actor} if actor is not None else {}
        return ScriptUnitAudioOut(
            unit_id=unit.id,
            audio_status=unit.audio_status,
            voice_actor_id=actor.id if actor is not None else None,
            voice_actor_name=actor.display_name if actor is not None else None,
            render_hash=unit.render_hash,
            asset=self._asset_out(asset, unit.current_audio_asset_id, actors)
            if asset is not None
            else None,
        )

    def _current_actor(self, unit: ScriptUnit) -> VoiceActor | None:
        return _load_actor(self.session, _find_cast(self.session, unit))

    # -------------------------------------------------------------- generate

    def generate(self, unit_id: str) -> ScriptUnitAudioOut:
        unit = self._unit(unit_id)
        self._ensure_tts_unit(unit)
        cast, actor = self._resolve_voice(unit)
        fingerprint = render_hash_for(unit, actor)
        chunk = (unit.text or "").strip()

        with TTS_LOCK:
            unit.audio_status = "generating"
            unit.updated_at = utc_now()
            self.session.commit()
            try:
                audio = self.registry.tts.synthesize(
                    chunk,
                    self._voice_profile(actor),
                    emotion=unit.emotion,
                    intensity=float(unit.intensity),
                    delivery=unit.delivery,
                    instruction=unit.tts_instruction,
                    speed=unit.speed,
                )
                info = self._read_info(audio.audio_bytes)
            except Exception as exc:  # noqa: BLE001
                unit.audio_status = "failed"
                unit.updated_at = utc_now()
                self._review_generation_failure(unit, exc)
                self.session.commit()
                if isinstance(exc, AppError):
                    raise
                raise AppError(
                    f"语音生成失败：{str(exc)[:300]}",
                    status_code=502,
                    code="unit_audio_failed",
                ) from exc

            version = int(
                self.session.scalar(
                    select(func.max(AudioAsset.version)).where(
                        AudioAsset.script_unit_id == unit.id
                    )
                )
                or 0
            ) + 1
            paths = BookPaths(self.settings.data_dir, unit.book_id)
            target = (
                paths.audio_dir
                / "units"
                / unit.id
                / f"v{version:03d}.wav"
            )
            atomic_write_bytes(target, audio.audio_bytes)
            relative = relative_to_data(target, self.settings.data_dir)
            asset = AudioAsset(
                id=new_id("asset"),
                segment_id=None,
                script_unit_id=unit.id,
                voice_actor_id=actor.id,
                version=version,
                file_path=relative,
                format="wav",
                sample_rate=info["sample_rate"],
                channels=info["channels"],
                sample_width_bits=info["sample_width_bits"],
                duration_ms=info["duration_ms"],
                file_size_bytes=len(audio.audio_bytes),
                checksum_sha256=hashlib.sha256(audio.audio_bytes).hexdigest(),
                tts_provider=audio.provider,
                tts_model=audio.model,
                generation_params_json={
                    **audio.metadata,
                    "voice_actor_id": actor.id,
                    "voice_cast_id": cast.id,
                    "render_hash": fingerprint,
                    "unit_type": unit.unit_type,
                    "speaker_type": unit.speaker_type,
                    "speaker_id": unit.speaker_id,
                },
                is_valid=True,
            )
            self.session.add(asset)
            unit.current_audio_asset_id = asset.id
            unit.render_hash = fingerprint
            unit.audio_status = "ready"
            unit.updated_at = utc_now()
            self._resolve_audio_reviews(unit, note="音频生成成功，对应异常自动收起。")
            self.session.commit()
            self.session.refresh(asset)
            self.session.refresh(unit)
            return self._result(unit, asset, actor)

    def _read_info(self, audio_bytes: bytes) -> dict[str, int]:
        if not audio_bytes or audio_bytes[:4] != b"RIFF":
            raise AppError(
                "语音 Provider 返回的不是 WAV 音频",
                status_code=502,
                code="invalid_audio",
            )
        try:
            info = sf.info(io.BytesIO(audio_bytes))
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                f"语音 Provider 返回的 WAV 无法解析：{exc}",
                status_code=502,
                code="invalid_audio",
            ) from exc
        if info.samplerate <= 0 or info.frames <= 0:
            raise AppError(
                "语音 Provider 返回了空音频",
                status_code=502,
                code="invalid_audio",
            )
        return {
            "sample_rate": int(info.samplerate),
            "channels": int(info.channels),
            "sample_width_bits": int(_SUBTYPE_BITS.get(info.subtype or "", 16)),
            "duration_ms": int(round(info.frames / info.samplerate * 1000)),
        }

    # -------------------------------------------------------------- versions

    def versions(self, unit_id: str) -> UnitAudioVersionListResponse:
        unit = self._unit(unit_id)
        if refresh_stale_units(self.session, [unit]):
            self.session.commit()
        rows = list(
            self.session.scalars(
                select(AudioAsset)
                .where(AudioAsset.script_unit_id == unit.id)
                .order_by(AudioAsset.version.desc())
            )
        )
        actors = {
            row.id: row
            for row in self.session.scalars(
                select(VoiceActor).where(
                    VoiceActor.id.in_(
                        [asset.voice_actor_id for asset in rows if asset.voice_actor_id]
                    )
                )
            )
        } if rows else {}
        return UnitAudioVersionListResponse(
            unit_id=unit.id,
            current_asset_id=unit.current_audio_asset_id,
            items=[
                self._asset_out(item, unit.current_audio_asset_id, actors)
                for item in rows
            ],
            total=len(rows),
        )

    def _asset(self, unit: ScriptUnit, asset_id: str) -> AudioAsset:
        validate_id(asset_id, "asset")
        asset = self.session.get(AudioAsset, asset_id)
        if (
            asset is None
            or not asset.is_valid
            or asset.script_unit_id != unit.id
        ):
            raise NotFoundError("该剧本单元没有这个音频版本")
        return asset

    def restore(self, unit_id: str, asset_id: str) -> ScriptUnitAudioOut:
        unit = self._unit(unit_id)
        asset = self._asset(unit, asset_id)
        params = asset.generation_params_json or {}
        asset_hash = params.get("render_hash")
        actor = self._current_actor(unit)
        current_hash = render_hash_for(unit, actor)
        unit.current_audio_asset_id = asset.id
        unit.render_hash = asset_hash if isinstance(asset_hash, str) else None
        unit.audio_status = (
            "ready" if isinstance(asset_hash, str) and asset_hash == current_hash else "stale"
        )
        unit.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(unit)
        return self._result(unit, asset, actor)

    # ------------------------------------------------------------- file paths

    def current_audio_path(self, unit_id: str) -> Path:
        unit = self._unit(unit_id)
        if not unit.current_audio_asset_id:
            raise NotFoundError("该剧本单元还没有生成音频")
        asset = self._asset(unit, unit.current_audio_asset_id)
        path = resolve_relative(self.settings.data_dir, asset.file_path)
        if not path.is_file():
            raise NotFoundError("音频文件缺失")
        return path

    # --------------------------------------------------------------- reviews

    def _review(
        self,
        unit: ScriptUnit,
        *,
        code: str,
        severity: str,
        title: str,
        message: str | None,
        payload: dict[str, Any],
    ) -> ReviewItem:
        existing = self.session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == unit.book_id,
                ReviewItem.entity_type == "script_unit",
                ReviewItem.entity_id == unit.id,
                ReviewItem.code == code,
                ReviewItem.status == "open",
            )
        )
        if existing is None:
            item = ReviewItem(
                id=new_id("review"),
                book_id=unit.book_id,
                entity_type="script_unit",
                entity_id=unit.id,
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
            self.session.add(item)
            self.session.flush()
            return item
        existing.severity = severity
        existing.title = title[:255]
        existing.message = message
        existing.payload_json = payload
        existing.updated_at = utc_now()
        return existing

    def _review_generation_failure(
        self, unit: ScriptUnit, error: Exception
    ) -> ReviewItem:
        message = str(getattr(error, "message", error))[:1200]
        return self._review(
            unit,
            code=AUDIO_FAILURE_CODE,
            severity="warning",
            title="剧本单元音频生成失败",
            message=message,
            payload={
                "unit_id": unit.id,
                "chapter_id": unit.chapter_id,
                "unit_index": unit.unit_index,
                "error_code": str(getattr(error, "code", "audio_failed")),
            },
        )

    def _review_voice_missing(
        self, unit: ScriptUnit, actor: VoiceActor, message: str
    ) -> None:
        self._review(
            unit,
            code=AUDIO_VOICE_CODE,
            severity="warning",
            title="配音员参考音不可用",
            message=message,
            payload={
                "unit_id": unit.id,
                "chapter_id": unit.chapter_id,
                "unit_index": unit.unit_index,
                "voice_actor_id": actor.id,
                "voice_actor_name": actor.display_name,
            },
        )
        self.session.commit()

    def _resolve_audio_reviews(self, unit: ScriptUnit, *, note: str) -> int:
        rows = list(
            self.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.book_id == unit.book_id,
                    ReviewItem.entity_type == "script_unit",
                    ReviewItem.entity_id == unit.id,
                    ReviewItem.status == "open",
                    ReviewItem.code.in_([AUDIO_FAILURE_CODE, AUDIO_VOICE_CODE]),
                )
            )
        )
        now = utc_now()
        for row in rows:
            row.status = "resolved"
            row.resolution_note = note
            row.resolved_at = now
            row.updated_at = now
        return len(rows)