"""音色库：候选设计、试听、绑定与参考音预处理。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from novelvoice_studio.ai.base import AudioResult
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
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Book, Character, Segment, VoiceProfile
from novelvoice_studio.schemas.voice import (
    VoiceDesignError,
    VoiceDesignRequest,
    VoiceDesignResponse,
    VoiceLibraryResponse,
    VoiceOwnerOut,
    VoicePreviewRequest,
    VoiceProfileOut,
    VoiceUpdateRequest,
)
from novelvoice_studio.services.paths import (
    BookPaths,
    atomic_write_bytes,
    new_id,
    relative_to_data,
    resolve_relative,
    validate_id,
)

MAX_PROFILES_PER_OWNER = 40
_PUBLIC_PARAM_KEYS = (
    "candidate_index",
    "language",
    "provider",
    "model",
    "reference_processed",
    "deess",
)


def _public_params(raw: dict | None) -> dict:
    source = raw or {}
    return {key: source[key] for key in _PUBLIC_PARAM_KEYS if key in source}


class VoiceService:
    def __init__(
        self, settings: Settings, session: Session, registry: ProviderRegistry
    ) -> None:
        self.settings = settings
        self.session = session
        self.registry = registry

    # -------------------------------------------------------------- helpers

    def _book(self, book_id: str) -> Book:
        validate_id(book_id, "book")
        book = self.session.get(Book, book_id)
        if book is None or book.deleted_at is not None:
            raise NotFoundError("书籍不存在或已在回收站")
        return book

    def _profile(self, voice_id: str) -> VoiceProfile:
        validate_id(voice_id, "voice")
        profile = self.session.get(VoiceProfile, voice_id)
        if profile is None:
            raise NotFoundError("音色不存在")
        return profile

    def _book_for_profile(self, profile: VoiceProfile) -> Book:
        if profile.owner_type == "narrator":
            return self._book(profile.owner_id)
        validate_id(profile.owner_id, "char")
        character = self.session.get(Character, profile.owner_id)
        if character is None:
            raise NotFoundError("音色所属角色不存在")
        return self._book(character.book_id)

    def _active_characters(self, book_id: str) -> list[Character]:
        return list(
            self.session.scalars(
                select(Character)
                .where(Character.book_id == book_id, Character.merged_into_id.is_(None))
                .order_by(Character.canonical_name.asc())
            )
        )

    def _owner_info(
        self, book: Book, owner_type: str, owner_id: str
    ) -> tuple[str, str, Character | None]:
        if owner_type == "narrator":
            validate_id(owner_id, "book")
            if owner_id != book.id:
                raise ValidationError("旁白音色必须绑定到当前书籍")
            return "旁白", self.settings.voice_design.narrator_description, None
        if owner_type != "character":
            raise ValidationError("owner_type 只能是 character 或 narrator")
        validate_id(owner_id, "char")
        character = self.session.get(Character, owner_id)
        if character is None or character.book_id != book.id:
            raise ValidationError("角色不属于当前书籍")
        if character.merged_into_id:
            raise ValidationError("该角色已合并，不能单独绑定音色")
        description = describe_character_voice(
            gender=character.gender,
            age_range=character.age_range,
            voice_description=character.voice_description,
            personality_desc=character.personality_desc,
        )
        return character.canonical_name, description, character

    def _serialize(self, profile: VoiceProfile, owner_name: str) -> VoiceProfileOut:
        return VoiceProfileOut(
            id=profile.id,
            owner_type=profile.owner_type,
            owner_id=profile.owner_id,
            owner_name=owner_name,
            name=profile.name,
            description=profile.description,
            reference_text=profile.reference_text,
            instruct_text=profile.instruct_text,
            seed=profile.seed,
            status=profile.status,
            is_selected=profile.is_selected,
            is_mock=profile.is_mock,
            sample_rate=profile.sample_rate,
            duration_ms=profile.duration_ms,
            generation_params=_public_params(profile.generation_params_json),
            audio_url=f"/api/v1/voices/{profile.id}/audio",
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def _profiles_for_owner(self, owner_type: str, owner_id: str) -> list[VoiceProfile]:
        rows = list(
            self.session.scalars(
                select(VoiceProfile).where(
                    VoiceProfile.owner_type == owner_type,
                    VoiceProfile.owner_id == owner_id,
                )
            )
        )
        return sorted(
            rows,
            key=lambda item: (
                not item.is_selected,
                -item.created_at.timestamp() if isinstance(item.created_at, datetime) else 0,
            ),
        )

    def _owner_out(
        self,
        *,
        owner_type: str,
        owner_id: str,
        name: str,
        description: str,
        character: Character | None = None,
        profiles: list[VoiceProfile] | None = None,
    ) -> VoiceOwnerOut:
        items = profiles if profiles is not None else self._profiles_for_owner(owner_type, owner_id)
        selected = next((item for item in items if item.is_selected), None)
        return VoiceOwnerOut(
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
            description=description,
            gender=character.gender if character else None,
            age_range=character.age_range if character else None,
            has_selected=selected is not None,
            selected_profile_id=selected.id if selected else None,
            profile_count=len(items),
            profiles=[self._serialize(item, name) for item in items],
        )

    # ----------------------------------------------------------------- read

    def library(self, book_id: str) -> VoiceLibraryResponse:
        book = self._book(book_id)
        narrator_name, narrator_description, _ = self._owner_info(
            book, "narrator", book.id
        )
        narrator_profiles = self._profiles_for_owner("narrator", book.id)
        narrator = self._owner_out(
            owner_type="narrator",
            owner_id=book.id,
            name=narrator_name,
            description=narrator_description,
            profiles=narrator_profiles,
        )

        characters: list[VoiceOwnerOut] = []
        without_voice: list[str] = []
        selected_owners = 1 if narrator.has_selected else 0
        total_profiles = len(narrator_profiles)
        for character in self._active_characters(book.id):
            description = describe_character_voice(
                gender=character.gender,
                age_range=character.age_range,
                voice_description=character.voice_description,
                personality_desc=character.personality_desc,
            )
            profiles = self._profiles_for_owner("character", character.id)
            owner = self._owner_out(
                owner_type="character",
                owner_id=character.id,
                name=character.canonical_name,
                description=description,
                character=character,
                profiles=profiles,
            )
            characters.append(owner)
            total_profiles += len(profiles)
            if owner.has_selected:
                selected_owners += 1
            else:
                without_voice.append(character.canonical_name)

        return VoiceLibraryResponse(
            book_id=book.id,
            narrator=narrator,
            characters=characters,
            total_profiles=total_profiles,
            selected_owners=selected_owners,
            characters_without_voice=without_voice,
        )

    def reference_audio(self, voice_id: str) -> Path:
        profile = self._profile(voice_id)
        self._book_for_profile(profile)
        if not profile.reference_audio_path:
            raise NotFoundError("该音色还没有参考音频")
        path = resolve_relative(self.settings.data_dir, profile.reference_audio_path)
        if not path.is_file():
            raise NotFoundError("音色参考音频文件缺失")
        return path

    # --------------------------------------------------------------- design

    def design(self, book_id: str, payload: VoiceDesignRequest) -> VoiceDesignResponse:
        book = self._book(book_id)
        owner_name, owner_description, _owner = self._owner_info(
            book, payload.owner_type, payload.owner_id
        )
        existing = self.session.scalar(
            select(func.count(VoiceProfile.id)).where(
                VoiceProfile.owner_type == payload.owner_type,
                VoiceProfile.owner_id == payload.owner_id,
            )
        )
        existing_count = int(existing or 0)
        if existing_count >= MAX_PROFILES_PER_OWNER:
            raise ConflictError(
                f"该角色已有 {existing_count} 个音色候选，请先清理不需要的候选"
            )

        description = (payload.description or owner_description).strip()
        if not description:
            raise ValidationError("请填写音色描述")
        reference_text = (payload.reference_text or self.settings.voice_design.reference_text).strip()
        if not reference_text:
            raise ValidationError("参考文本不能为空")

        requested = payload.candidates or self.settings.voice_design.candidates
        requested = max(1, min(5, requested, MAX_PROFILES_PER_OWNER - existing_count))
        seeds = self._design_seeds(payload.seed, requested)

        paths = BookPaths(self.settings.data_dir, book.id)
        direction = f"{payload.owner_type}_{payload.owner_id}"
        directory = paths.voices_dir / direction / "candidates"

        items: list[VoiceProfile] = []
        errors: list[VoiceDesignError] = []
        dependency_error: DependencyUnavailableError | None = None
        for index, seed in enumerate(seeds):
            voice_id = new_id("voice")
            try:
                candidate = self.registry.voice_design.design_voice(
                    description,
                    reference_text,
                    payload.language,
                    seed=seed,
                )
                audio = candidate.audio
                if audio is None or not audio.audio_bytes:
                    raise AppError(
                        "声音设计没有返回音频",
                        status_code=502,
                        code="voice_design_empty",
                    )
                path = directory / f"{voice_id}.wav"
                atomic_write_bytes(path, audio.audio_bytes)
                relative = relative_to_data(path, self.settings.data_dir)
                profile = VoiceProfile(
                    id=voice_id,
                    owner_type=payload.owner_type,
                    owner_id=payload.owner_id,
                    name=f"{owner_name} · 候选 {index + 1:02d}",
                    description=description,
                    reference_text=reference_text,
                    reference_audio_path=relative,
                    instruct_text=candidate.instruct_text,
                    seed=candidate.seed,
                    generation_params_json={
                        "candidate_index": index,
                        "language": payload.language,
                        "provider": audio.provider,
                        "model": audio.model,
                        "reference_processed": False,
                        "checksum_sha256": hashlib.sha256(audio.audio_bytes).hexdigest(),
                    },
                    sample_rate=audio.sample_rate or None,
                    duration_ms=audio.duration_ms or None,
                    is_selected=False,
                    is_mock=audio.is_mock,
                    status="draft",
                )
                self.session.add(profile)
                items.append(profile)
            except DependencyUnavailableError as exc:
                dependency_error = exc
                errors.append(VoiceDesignError(index=index, message=exc.message))
                break
            except AppError as exc:
                errors.append(VoiceDesignError(index=index, message=exc.message))
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    VoiceDesignError(index=index, message=f"{exc.__class__.__name__}: {exc}")
                )

        if not items:
            if dependency_error is not None:
                raise dependency_error
            raise AppError(
                "音色设计失败",
                status_code=502,
                code="voice_design_failed",
                details={"errors": [item.model_dump() for item in errors]},
            )
        self.session.commit()
        return VoiceDesignResponse(
            items=[self._serialize(item, owner_name) for item in items],
            errors=errors,
            requested=requested,
            book_id=book.id,
        )

    def _design_seeds(self, seed: int | None, count: int) -> list[int]:
        if seed is not None:
            return [(seed + index * 101) % (2**31 - 1) for index in range(count)]
        base = self.settings.voice_design.seeds or [42]
        return [
            (base[index % len(base)] + (index // len(base)) * 101) % (2**31 - 1)
            for index in range(count)
        ]

    # --------------------------------------------------------------- select

    def select(self, voice_id: str) -> VoiceProfileOut:
        profile = self._profile(voice_id)
        book = self._book_for_profile(profile)
        owner_name, _description, _character = self._owner_info(
            book, profile.owner_type, profile.owner_id
        )
        if profile.is_selected:
            return self._serialize(profile, owner_name)
        if not profile.reference_audio_path:
            raise ConflictError("该音色没有参考音频，不能绑定")
        source = resolve_relative(self.settings.data_dir, profile.reference_audio_path)
        if not source.is_file():
            raise ConflictError("音色参考音频文件缺失，请重新生成候选")

        raw_relative = profile.reference_audio_path
        params = dict(profile.generation_params_json or {})
        deess_config = self.settings.tts.reference_deess
        if deess_config.enabled and not profile.is_mock:
            processed = self.registry.tts.process_reference(
                source.read_bytes(),
                params={
                    "band_low": deess_config.band_low,
                    "band_high": deess_config.band_high,
                    "threshold_ratio": deess_config.threshold_ratio,
                    "full_ratio": deess_config.full_ratio,
                    "max_reduction_db": deess_config.max_reduction_db,
                },
            )
            if processed is not None and processed.audio_bytes:
                paths = BookPaths(self.settings.data_dir, book.id)
                target = (
                    paths.voices_dir
                    / f"{profile.owner_type}_{profile.owner_id}"
                    / "reference"
                    / f"{profile.id}.wav"
                )
                atomic_write_bytes(target, processed.audio_bytes)
                params.setdefault("raw_reference_path", raw_relative)
                params["reference_processed"] = True
                params["deess"] = processed.metadata.get("deess") or {
                    "threshold_ratio": deess_config.threshold_ratio,
                    "full_ratio": deess_config.full_ratio,
                    "max_reduction_db": deess_config.max_reduction_db,
                }
                profile.reference_audio_path = relative_to_data(
                    target, self.settings.data_dir
                )
                if processed.sample_rate:
                    profile.sample_rate = processed.sample_rate
                if processed.duration_ms:
                    profile.duration_ms = processed.duration_ms
        else:
            params.setdefault("reference_processed", False)
        profile.generation_params_json = params

        self.session.execute(
            update(VoiceProfile)
            .where(
                VoiceProfile.owner_type == profile.owner_type,
                VoiceProfile.owner_id == profile.owner_id,
                VoiceProfile.id != profile.id,
            )
            .values(is_selected=False)
        )
        profile.is_selected = True
        profile.status = "ready"
        profile.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(profile)
        return self._serialize(profile, owner_name)

    # --------------------------------------------------------------- update

    def update(self, voice_id: str, payload: VoiceUpdateRequest) -> VoiceProfileOut:
        profile = self._profile(voice_id)
        book = self._book_for_profile(profile)
        owner_name, _description, _character = self._owner_info(
            book, profile.owner_type, profile.owner_id
        )
        fields = payload.model_fields_set
        if "name" in fields and payload.name is not None:
            profile.name = payload.name
        if "description" in fields and payload.description is not None:
            profile.description = payload.description
        if "user_locked" in fields and payload.user_locked is not None:
            profile.user_locked = payload.user_locked
        profile.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(profile)
        return self._serialize(profile, owner_name)

    # --------------------------------------------------------------- delete

    def delete(self, voice_id: str) -> None:
        profile = self._profile(voice_id)
        self._book_for_profile(profile)
        if profile.is_selected:
            raise ConflictError("已绑定的音色不能直接删除，请先选择其他音色")
        usage = int(
            self.session.scalar(
                select(func.count(Segment.id)).where(Segment.voice_profile_id == voice_id)
            )
            or 0
        )
        if usage:
            raise ConflictError(f"已有 {usage} 个片段使用过该音色，不能删除")
        params = profile.generation_params_json or {}
        candidates = {profile.reference_audio_path, params.get("raw_reference_path")}
        for relative in candidates:
            if not isinstance(relative, str) or not relative:
                continue
            try:
                path = resolve_relative(self.settings.data_dir, relative)
            except AppError:
                continue
            path.unlink(missing_ok=True)
        self.session.delete(profile)
        self.session.commit()

    # -------------------------------------------------------------- preview

    def preview(self, voice_id: str, payload: VoicePreviewRequest) -> AudioResult:
        profile = self._profile(voice_id)
        self._book_for_profile(profile)
        if not profile.reference_audio_path:
            raise ConflictError("该音色还没有参考音频")
        reference = resolve_relative(
            self.settings.data_dir, profile.reference_audio_path
        )
        if not reference.is_file():
            raise ConflictError("音色参考音频文件缺失，请重新设计")
        params = profile.generation_params_json or {}
        voice_profile = {
            "id": profile.id,
            "reference_path": str(reference),
            "description": profile.description or "",
            "instruct_text": profile.instruct_text or "",
            "seed": profile.seed,
            "language": params.get("language") or "Chinese",
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