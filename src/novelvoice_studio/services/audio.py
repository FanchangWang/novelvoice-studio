"""单段语音生成、音频版本与安全文件定位。"""

from __future__ import annotations

import hashlib
import io
import threading
from pathlib import Path

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
    Chapter,
    Character,
    ScriptUnit,
    Segment,
    VoiceProfile,
)
from novelvoice_studio.schemas.voice import (
    AudioAssetOut,
    AudioVersionListResponse,
    SegmentAudioOut,
)
from novelvoice_studio.services.paths import (
    BookPaths,
    atomic_write_bytes,
    new_id,
    relative_to_data,
    resolve_relative,
    validate_id,
)

# 单 GPU：串行执行整条 Qwen + CosyVoice 链路，避免并发抢占显存。
# P7 剧本单元生成复用同一把锁，避免与旧片段生成同时占用显存。
TTS_LOCK = threading.Lock()

_SUBTYPE_BITS = {
    "PCM_S8": 8,
    "PCM_U8": 8,
    "PCM_16": 16,
    "PCM_24": 24,
    "PCM_32": 32,
    "FLOAT": 32,
    "DOUBLE": 64,
}


class AudioService:
    def __init__(
        self, settings: Settings, session: Session, registry: ProviderRegistry
    ) -> None:
        self.settings = settings
        self.session = session
        self.registry = registry

    # -------------------------------------------------------------- helpers

    def _segment(self, segment_id: str) -> Segment:
        validate_id(segment_id, "seg")
        segment = self.session.get(Segment, segment_id)
        if segment is None:
            raise NotFoundError("片段不存在")
        return segment

    def _book(self, book_id: str) -> Book:
        book = self.session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _context(self, segment: Segment) -> tuple[Chapter, Book]:
        chapter = self.session.get(Chapter, segment.chapter_id)
        if chapter is None:
            raise NotFoundError("片段所属章节不存在")
        return chapter, self._book(segment.book_id)

    def _owner(self, segment: Segment, book: Book) -> tuple[str, str, str]:
        if segment.speaker_type == "character" and segment.speaker_id:
            character = self.session.get(Character, segment.speaker_id)
            if character is None or character.book_id != book.id:
                raise ConflictError("片段说话角色不存在，请先在片段审核中重新指定")
            if character.merged_into_id:
                raise ConflictError("片段说话角色已被合并，请重新指定说话人")
            return "character", character.id, character.canonical_name
        return "narrator", book.id, "旁白"

    def _selected_profile(self, segment: Segment, book: Book) -> VoiceProfile:
        owner_type, owner_id, owner_name = self._owner(segment, book)
        profile: VoiceProfile | None = None
        if segment.voice_profile_id:
            candidate = self.session.get(VoiceProfile, segment.voice_profile_id)
            if (
                candidate is not None
                and candidate.is_selected
                and candidate.owner_type == owner_type
                and candidate.owner_id == owner_id
                and candidate.reference_audio_path
            ):
                profile = candidate
        if profile is None:
            profile = self.session.scalars(
                select(VoiceProfile)
                .where(
                    VoiceProfile.owner_type == owner_type,
                    VoiceProfile.owner_id == owner_id,
                    VoiceProfile.is_selected.is_(True),
                )
                .order_by(VoiceProfile.updated_at.desc())
            ).first()
        if profile is None:
            raise ConflictError(
                f"尚未为“{owner_name}”绑定固定音色。请先在声音库选择或设计音色。",
                details={
                    "owner_type": owner_type,
                    "owner_id": owner_id,
                    "owner_name": owner_name,
                },
            )
        if not profile.reference_audio_path:
            raise ConflictError(f"“{owner_name}”的音色缺少参考音频，请重新选择音色")
        return profile

    def _profile_dict(self, profile: VoiceProfile) -> dict:
        path = resolve_relative(self.settings.data_dir, profile.reference_audio_path or "")
        if not path.is_file():
            raise ConflictError("音色参考音频文件缺失，请重新选择或重新设计音色")
        params = profile.generation_params_json or {}
        return {
            "id": profile.id,
            "reference_path": str(path),
            "description": profile.description or "",
            "instruct_text": profile.instruct_text or "",
            "seed": profile.seed,
            "language": params.get("language") or "Chinese",
        }

    def _asset_out(self, asset: AudioAsset, current_id: str | None) -> AudioAssetOut:
        return AudioAssetOut(
            id=asset.id,
            segment_id=asset.segment_id,
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
        self, segment: Segment, asset: AudioAsset | None
    ) -> SegmentAudioOut:
        return SegmentAudioOut(
            segment_id=segment.id,
            audio_status=segment.audio_status,
            voice_profile_id=segment.voice_profile_id,
            asset=self._asset_out(asset, segment.current_audio_asset_id)
            if asset is not None
            else None,
        )

    # -------------------------------------------------------------- generate

    def generate(self, segment_id: str) -> SegmentAudioOut:
        segment = self._segment(segment_id)
        chapter, book = self._context(segment)
        profile = self._selected_profile(segment, book)
        voice_profile = self._profile_dict(profile)
        if not (segment.speech_text or "").strip():
            raise ValidationError("片段文本为空，无法生成")

        with TTS_LOCK:
            segment.audio_status = "generating"
            segment.updated_at = utc_now()
            self.session.commit()
            try:
                audio = self.registry.tts.synthesize(
                    segment.speech_text,
                    voice_profile,
                    emotion=segment.emotion,
                    intensity=float(segment.intensity),
                    delivery=segment.delivery,
                    instruction=segment.tts_instruction,
                    speed=segment.speed,
                )
                info = self._read_info(audio.audio_bytes)
            except Exception:
                segment.audio_status = "failed"
                segment.updated_at = utc_now()
                self.session.commit()
                raise

            version = int(
                self.session.scalar(
                    select(func.max(AudioAsset.version)).where(
                        AudioAsset.segment_id == segment.id
                    )
                )
                or 0
            ) + 1
            paths = BookPaths(self.settings.data_dir, book.id)
            target = (
                paths.audio_dir
                / chapter.id
                / segment.id
                / f"v{version:03d}.wav"
            )
            atomic_write_bytes(target, audio.audio_bytes)
            relative = relative_to_data(target, self.settings.data_dir)
            asset = AudioAsset(
                id=new_id("asset"),
                segment_id=segment.id,
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
                    "voice_profile_id": profile.id,
                    "text_version": segment.text_version,
                },
                is_valid=True,
            )
            self.session.add(asset)
            segment.current_audio_asset_id = asset.id
            segment.voice_profile_id = profile.id
            segment.audio_status = "ready"
            segment.updated_at = utc_now()
            self.session.commit()
            self.session.refresh(asset)
            self.session.refresh(segment)
            return self._result(segment, asset)

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

    def versions(self, segment_id: str) -> AudioVersionListResponse:
        segment = self._segment(segment_id)
        self._context(segment)
        rows = list(
            self.session.scalars(
                select(AudioAsset)
                .where(AudioAsset.segment_id == segment.id)
                .order_by(AudioAsset.version.desc())
            )
        )
        return AudioVersionListResponse(
            segment_id=segment.id,
            current_asset_id=segment.current_audio_asset_id,
            items=[self._asset_out(item, segment.current_audio_asset_id) for item in rows],
            total=len(rows),
        )

    def _asset(self, asset_id: str) -> AudioAsset:
        validate_id(asset_id, "asset")
        asset = self.session.get(AudioAsset, asset_id)
        if asset is None or not asset.is_valid:
            raise NotFoundError("音频版本不存在")
        return asset

    def restore(self, asset_id: str) -> SegmentAudioOut:
        asset = self._asset(asset_id)
        segment = self._segment(asset.segment_id)
        self._context(segment)
        params = asset.generation_params_json or {}
        profile_id = params.get("voice_profile_id")
        if isinstance(profile_id, str) and self.session.get(VoiceProfile, profile_id):
            segment.voice_profile_id = profile_id
        segment.current_audio_asset_id = asset.id
        segment.audio_status = "ready"
        segment.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(segment)
        return self._result(segment, asset)

    # ------------------------------------------------------------- file paths

    def asset_audio_path(self, asset_id: str) -> Path:
        asset = self._asset(asset_id)
        if asset.script_unit_id:
            validate_id(asset.script_unit_id, "unit")
            unit = self.session.get(ScriptUnit, asset.script_unit_id)
            if unit is None:
                raise NotFoundError("音频所属剧本单元不存在")
            self._book(unit.book_id)
        elif asset.segment_id:
            segment = self._segment(asset.segment_id)
            self._context(segment)
        else:
            raise NotFoundError("音频版本没有归属")
        path = resolve_relative(self.settings.data_dir, asset.file_path)
        if not path.is_file():
            raise NotFoundError("音频文件缺失")
        return path

    def current_audio_path(self, segment_id: str) -> Path:
        segment = self._segment(segment_id)
        self._context(segment)
        if not segment.current_audio_asset_id:
            raise NotFoundError("该片段还没有生成音频")
        return self.asset_audio_path(segment.current_audio_asset_id)