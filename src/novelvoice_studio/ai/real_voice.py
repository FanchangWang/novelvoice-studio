"""真实语音 Provider：Qwen VoiceDesign + CosyVoice3 VC。

阶段 3 定稿链路：
  情感源 = Qwen VoiceDesign（按角色音色描述 + 情绪指令生成）
  最终音色 = CosyVoice3 inference_vc 转换到固定参考音
参考音去齿在绑定音色时完成（见 VoiceService），生成阶段不再重复处理。
"""

from __future__ import annotations

from pathlib import Path

from novelvoice_studio.ai.base import (
    AudioResult,
    ProviderHealth,
    TTSProvider,
    VoiceCandidate,
    VoiceDesignProvider,
)
from novelvoice_studio.ai.voice_prompts import (
    build_performance_instruction,
    build_reference_instruction,
    profile_base_description,
)
from novelvoice_studio.ai.workers import CosyVoiceWorkerClient, QwenWorkerClient
from novelvoice_studio.config import TTSConfig, VoiceDesignConfig
from novelvoice_studio.core.errors import ConflictError, ValidationError


class QwenVoiceDesignProvider(VoiceDesignProvider):
    name = "qwen3_tts_worker"

    def __init__(self, client: QwenWorkerClient, config: VoiceDesignConfig) -> None:
        self.client = client
        self.config = config
        self.model = config.model_id

    def health_check(self) -> ProviderHealth:
        try:
            payload = self.client.health()
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(
                ok=False,
                detail=str(exc),
                meta={"worker_url": self.client.base_url},
            )
        loaded = bool(payload.get("model_loaded"))
        return ProviderHealth(
            ok=bool(payload.get("ok", True)),
            detail=(
                f"Qwen Worker 已连接；模型{'已加载' if loaded else '尚未加载（首次生成时加载）'}"
            ),
            meta={key: payload.get(key) for key in ("model_id", "model_dir", "load_seconds")},
        )

    def design_voice(
        self,
        description: str,
        reference_text: str,
        language: str = "zh-CN",
        *,
        seed: int | None = None,
    ) -> VoiceCandidate:
        actual_seed = self.config.seeds[0] if seed is None else seed
        instruct = build_reference_instruction(description)
        audio = self.client.generate(
            text=reference_text,
            instruct=instruct,
            language=language,
            seed=actual_seed,
        )
        return VoiceCandidate(
            candidate_id=f"qwen_{actual_seed}",
            name=description.strip()[:24] or "未命名音色",
            description=description.strip(),
            instruct_text=instruct,
            reference_text=reference_text,
            seed=actual_seed,
            audio=audio,
            metadata={
                "language": language,
                "provider": self.name,
                "model": self.model,
                "worker": audio.metadata,
            },
        )

    def preview_voice(self, candidate: VoiceCandidate, text: str) -> AudioResult:
        return self.client.generate(
            text=text,
            instruct=candidate.instruct_text,
            language=str(candidate.metadata.get("language") or "Chinese"),
            seed=candidate.seed,
        )

    def unload_model(self) -> None:
        self.client.unload()


class QwenCosyVoiceTTSProvider(TTSProvider):
    name = "qwen3_tts+cosyvoice3"

    def __init__(
        self,
        qwen: QwenWorkerClient,
        cosy: CosyVoiceWorkerClient,
        config: TTSConfig,
    ) -> None:
        self.qwen = qwen
        self.cosy = cosy
        self.config = config
        self.model = f"{config.model_id} + Qwen3-TTS-12Hz-1.7B-VoiceDesign"

    def health_check(self) -> ProviderHealth:
        problems: list[str] = []
        meta: dict[str, object] = {}
        try:
            qwen_health = self.qwen.health()
            meta["voice_design"] = qwen_health
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Qwen：{exc}")
        try:
            cosy_health = self.cosy.health()
            meta["tts"] = cosy_health
        except Exception as exc:  # noqa: BLE001
            problems.append(f"CosyVoice：{exc}")
        if problems:
            return ProviderHealth(ok=False, detail="；".join(problems), meta=meta)
        return ProviderHealth(
            ok=True,
            detail="Qwen VoiceDesign 与 CosyVoice3 Worker 均已连接",
            meta=meta,
        )

    def load_voice_profile(self, profile: dict) -> None:
        # 参考音按路径读取，无需在 Provider 内缓存。
        return None

    def synthesize(
        self,
        text: str,
        voice_profile: dict,
        *,
        emotion: str | None = None,
        intensity: float = 1.0,
        delivery: str | None = None,
        instruction: str | None = None,
        speed: float = 1.0,
    ) -> AudioResult:
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValidationError("待合成文本为空")
        if len(cleaned) > self.config.max_text_chars:
            raise ValidationError(
                f"片段过长（{len(cleaned)} 字），请先在片段审核中拆分；"
                f"当前上限 {self.config.max_text_chars} 字"
            )

        reference_value = voice_profile.get("reference_path")
        reference_path = Path(str(reference_value)) if reference_value else None
        if reference_path is None or not reference_path.is_file():
            raise ConflictError("该音色的参考音频缺失，请重新选择或重新设计音色")
        try:
            reference_bytes = reference_path.read_bytes()
        except OSError as exc:
            raise ConflictError(f"无法读取音色参考音频：{exc}") from exc

        seed = int(voice_profile.get("seed") or self.config.default_seed)
        language = str(voice_profile.get("language") or "Chinese")
        instruct = build_performance_instruction(
            profile_base_description(voice_profile),
            emotion=emotion,
            intensity=intensity,
            delivery=delivery,
            extra=instruction,
        )
        actual_speed = max(0.5, min(2.0, float(speed or 1.0)))

        source = self.qwen.generate(
            text=cleaned,
            instruct=instruct,
            language=language,
            seed=seed,
        )
        final = self.cosy.vc(
            source_bytes=source.audio_bytes,
            reference_bytes=reference_bytes,
            speed=actual_speed,
            seed=seed,
        )
        return AudioResult(
            audio_bytes=final.audio_bytes,
            sample_rate=final.sample_rate,
            channels=final.channels,
            duration_ms=final.duration_ms,
            format="wav",
            provider=self.name,
            model=self.model,
            is_mock=False,
            metadata={
                "instruct": instruct,
                "seed": seed,
                "speed": actual_speed,
                "emotion": emotion,
                "intensity": intensity,
                "delivery": delivery,
                "profile_id": voice_profile.get("id"),
                "qwen": source.metadata,
                "cosyvoice": final.metadata,
            },
        )

    def process_reference(
        self, audio_bytes: bytes, *, params: dict | None = None
    ) -> AudioResult | None:
        resolved = params or {}
        deess = self.config.reference_deess
        return self.cosy.deess(
            audio_bytes=audio_bytes,
            band_low=float(resolved.get("band_low", deess.band_low)),
            band_high=float(resolved.get("band_high", deess.band_high)),
            threshold_ratio=float(resolved.get("threshold_ratio", deess.threshold_ratio)),
            full_ratio=float(resolved.get("full_ratio", deess.full_ratio)),
            max_reduction_db=float(
                resolved.get("max_reduction_db", deess.max_reduction_db)
            ),
        )

    def unload_model(self) -> None:
        for client in (self.qwen, self.cosy):
            try:
                client.unload()
            except Exception:  # noqa: BLE001
                pass