"""确定性 Mock Provider：不加载模型、不访问网络，保证阶段 1 流程可测试。"""

from __future__ import annotations

import hashlib
import io
import math
import re

import numpy as np
import soundfile as sf

from novelvoice_studio.ai.base import (
    AudioResult,
    ChapterSummaryResult,
    CharacterAnalysisResult,
    CharacterDraft,
    LLMProvider,
    ProviderHealth,
    SegmentAnalysisResult,
    SegmentDraft,
    TTSProvider,
    VoiceCandidate,
    VoiceDesignProvider,
)

_NAME_HINT = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:说|道|问|答|喊|叫|笑|叹|想)")
_QUOTE_MARKERS = ("「", "“", "『", '"')


def _tone_wav(text: str, *, frequency: float, duration_ms: int, sample_rate: int = 16000) -> AudioResult:
    duration_ms = max(300, min(duration_ms, 8000))
    frames = int(sample_rate * duration_ms / 1000)
    time = np.arange(frames, dtype=np.float32) / sample_rate
    envelope = np.ones_like(time)
    fade = min(frames // 4, int(sample_rate * 0.02))
    if fade > 0:
        envelope[:fade] = np.linspace(0, 1, fade, dtype=np.float32)
        envelope[-fade:] = np.linspace(1, 0, fade, dtype=np.float32)
    wave = 0.12 * np.sin(2 * math.pi * frequency * time) * envelope
    buffer = io.BytesIO()
    sf.write(buffer, wave.astype(np.float32), sample_rate, format="WAV", subtype="PCM_16")
    return AudioResult(
        audio_bytes=buffer.getvalue(),
        sample_rate=sample_rate,
        channels=1,
        duration_ms=duration_ms,
        format="wav",
        provider="mock",
        model="mock-tone",
        is_mock=True,
        metadata={"text_length": len(text), "frequency": frequency},
    )


class MockLLMProvider(LLMProvider):
    name = "mock_llm"
    model = "mock-llm"
    is_mock = True

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="Mock LLM 始终可用（不调用模型）", meta={"mock": True})

    def list_models(self) -> list[str]:
        return [self.model]

    def analyze_characters(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> CharacterAnalysisResult:
        names: list[str] = []
        for item in known_characters:
            name = item.get("canonical_name") or item.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        # 模拟模式用极简启发式补一点角色名，方便演示审核流程；不要当作真实分析。
        for match in _NAME_HINT.findall(text):
            if match not in names:
                names.append(match)
        characters = [
            CharacterDraft(
                name=name,
                aliases=[],
                gender="unknown",
                age_range="unknown",
                character_type="unknown",
                identity_description="模拟模式仅用于流程验证，不代表真实分析结果。",
                personality=["待确认"],
                evidence=[text[:60]] if text else [],
                confidence=0.2,
                is_provisional=True,
            )
            for name in names[:12]
        ]
        return CharacterAnalysisResult(
            characters=characters,
            meta={"mock": True, "prompt_version": "mock"},
        )

    def analyze_segments(
        self, text: str, known_characters: list[dict], context: dict | None = None
    ) -> SegmentAnalysisResult:
        context = context or {}
        paragraphs = context.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            paragraphs = [
                {"index": index, "text": line.strip()}
                for index, line in enumerate(text.splitlines())
                if line.strip()
            ]
        segments: list[SegmentDraft] = []
        for item in paragraphs[:80]:
            index = int(item.get("index", 0))
            paragraph = str(item.get("text", "")).strip()
            if not paragraph:
                continue
            is_dialogue = paragraph[0] in _QUOTE_MARKERS
            segments.append(
                SegmentDraft(
                    paragraph_index=index,
                    source_text=paragraph,
                    segment_type="dialogue" if is_dialogue else "narration",
                    speaker_type="unknown" if is_dialogue else "narrator",
                    speaker_name=None,
                    speaker_confidence=0.1 if is_dialogue else 0.9,
                    emotion="neutral",
                    emotion_confidence=0.1 if is_dialogue else 0.9,
                    intensity=1,
                    delivery="normal",
                    tts_instruction=None,
                    evidence=[
                        "模拟模式按段落切分；对白说话人需人工确认。"
                    ],
                )
            )
        return SegmentAnalysisResult(
            segments=segments,
            meta={"mock": True, "prompt_version": "mock"},
        )

    def summarize_chapter(
        self, text: str, known_characters: list[dict]
    ) -> ChapterSummaryResult:
        summary = text.strip().replace("\n", " ")[:120] or "（空章节）"
        names = [
            str(item.get("canonical_name") or item.get("name"))
            for item in known_characters[:5]
            if item.get("canonical_name") or item.get("name")
        ]
        return ChapterSummaryResult(
            summary=summary,
            characters=names,
            meta={"mock": True, "prompt_version": "mock"},
        )


class MockVoiceDesignProvider(VoiceDesignProvider):
    name = "mock_voice_design"
    model = "mock-voice-design"
    is_mock = True

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="Mock VoiceDesign 可用（不调用模型）", meta={"mock": True})

    def design_voice(
        self,
        description: str,
        reference_text: str,
        language: str = "zh-CN",
        *,
        seed: int | None = None,
    ) -> VoiceCandidate:
        if seed is None:
            digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
            seed = int(digest[:8], 16) % 10000
        audio = _tone_wav(reference_text, frequency=180 + seed % 200, duration_ms=1200)
        return VoiceCandidate(
            candidate_id=f"mock_voice_{seed:04d}",
            name=f"模拟音色 {seed:04d}",
            description=description,
            instruct_text=description,
            reference_text=reference_text,
            seed=seed,
            audio=audio,
            metadata={"language": language, "mock": True},
        )

    def preview_voice(self, candidate: VoiceCandidate, text: str) -> AudioResult:
        return _tone_wav(text, frequency=180 + candidate.seed % 200, duration_ms=len(text) * 80)


class MockTTSProvider(TTSProvider):
    name = "mock_tts"
    model = "mock-tts"
    is_mock = True

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="Mock TTS 可用（生成提示音，不是语音）", meta={"mock": True})

    def load_voice_profile(self, profile: dict) -> None:
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
        seed = int(voice_profile.get("seed", 0) or 0)
        duration = int(max(400, len(text) * 70 / max(0.5, speed)))
        result = _tone_wav(text, frequency=160 + seed % 240, duration_ms=duration)
        return AudioResult(
            audio_bytes=result.audio_bytes,
            sample_rate=result.sample_rate,
            channels=result.channels,
            duration_ms=result.duration_ms,
            format=result.format,
            provider=self.name,
            model=self.model,
            is_mock=True,
            metadata={
                "emotion": emotion,
                "intensity": intensity,
                "delivery": delivery,
                "instruction": instruction,
                "speed": speed,
                "mock": True,
            },
        )