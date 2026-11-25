from __future__ import annotations

import io

import soundfile as sf

from novelvoice_studio.ai.mock import (
    MockLLMProvider,
    MockTTSProvider,
    MockVoiceDesignProvider,
)
from novelvoice_studio.ai.ollama import OllamaLLMProvider
from novelvoice_studio.ai.real_voice import (
    QwenCosyVoiceTTSProvider,
    QwenVoiceDesignProvider,
)
from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import ProvidersConfig, Settings


def _read_wav(data: bytes) -> tuple[object, int]:
    return sf.read(io.BytesIO(data), dtype="float32")


def test_mock_voice_design_deterministic() -> None:
    provider = MockVoiceDesignProvider()
    first = provider.design_voice("低沉的中年男声", "测试参考文本")
    second = provider.design_voice("低沉的中年男声", "测试参考文本")
    assert first.candidate_id == second.candidate_id
    assert first.seed == second.seed
    assert first.audio is not None
    audio, sample_rate = _read_wav(first.audio.audio_bytes)
    assert sample_rate == 16000
    assert len(audio) > 0
    assert first.audio.is_mock is True


def test_mock_tts_returns_valid_wav() -> None:
    provider = MockTTSProvider()
    result = provider.synthesize(
        "这是一段测试文本。",
        {"seed": 42},
        emotion="sad",
        intensity=1.2,
        speed=0.9,
    )
    assert result.is_mock is True
    assert result.provider == "mock_tts"
    assert result.duration_ms >= 400
    audio, sample_rate = _read_wav(result.audio_bytes)
    assert sample_rate == 16000
    assert len(audio) == int(sample_rate * result.duration_ms / 1000)


def test_mock_llm_outputs() -> None:
    provider = MockLLMProvider()
    result = provider.analyze_characters(
        "林见秋说：“你好。”", [{"name": "林见秋"}, {"name": "沈砚"}]
    )
    assert [item.name for item in result.characters] == ["林见秋", "沈砚"]
    assert provider.health_check().ok is True
    summary = provider.summarize_chapter("第一句。第二句。", [{"name": "林见秋"}])
    assert "第一句" in summary.summary
    segments = provider.analyze_segments("第一段\n第二段", [])
    assert [item.source_text for item in segments.segments] == ["第一段", "第二段"]
    assert [item.paragraph_index for item in segments.segments] == [0, 1]


def test_registry_mock_summary() -> None:
    registry = ProviderRegistry(Settings(providers=ProvidersConfig(mode="mock")))
    assert registry.is_mock is True
    summary = registry.summary()
    assert summary["mode"] == "mock"
    assert registry.llm.is_mock is True


def test_registry_real_mode_uses_local_providers() -> None:
    """阶段 3：real 模式使用本机 Ollama 与两个回环语音 Worker。"""
    registry = ProviderRegistry(Settings(providers=ProvidersConfig(mode="real")))
    assert registry.mode == "real"
    assert isinstance(registry.llm, OllamaLLMProvider)
    assert isinstance(registry.voice_design, QwenVoiceDesignProvider)
    assert isinstance(registry.tts, QwenCosyVoiceTTSProvider)
    assert registry.llm.is_mock is False
    assert registry.voice_design.is_mock is False
    assert registry.tts.is_mock is False
    assert registry.is_mock is False
    summary = registry.summary()
    assert summary["llm"]["is_mock"] is False
    assert summary["voice_design"]["is_mock"] is False
    assert summary["tts"]["is_mock"] is False