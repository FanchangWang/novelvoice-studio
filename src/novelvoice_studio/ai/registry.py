"""Provider 注册表。

mock 模式：全部使用确定性 Mock，不加载模型。
real 模式：LLM 接本机 Ollama；VoiceDesign / TTS 接本机 HTTP 引擎服务。
引擎服务未启动时应用仍可启动，语音相关接口会返回 503 与启动提示。
"""

from __future__ import annotations

from novelvoice_studio.ai.base import LLMProvider, TTSProvider, VoiceDesignProvider
from novelvoice_studio.ai.mock import (
    MockLLMProvider,
    MockTTSProvider,
    MockVoiceDesignProvider,
)
from novelvoice_studio.ai.ollama import OllamaLLMProvider
from novelvoice_studio.ai.real_voice import QwenCosyVoiceTTSProvider, QwenVoiceDesignProvider
from novelvoice_studio.ai.workers import CosyVoiceWorkerClient, QwenWorkerClient
from novelvoice_studio.config import Settings


class ProviderRegistry:
    def __init__(self, settings: Settings) -> None:
        self.mode = settings.providers.mode
        if self.mode == "real":
            llm_config = settings.llm
            self.llm: LLMProvider = OllamaLLMProvider(
                base_url=llm_config.base_url,
                model=llm_config.model,
                context_length=llm_config.context_length,
                keep_alive=llm_config.keep_alive,
                timeout_seconds=llm_config.timeout_seconds,
                temperature=llm_config.temperature,
                json_retries=llm_config.json_retries,
            )
            voice_config = settings.voice_design
            tts_config = settings.tts
            qwen_client = QwenWorkerClient(
                voice_config.worker_url, timeout_seconds=voice_config.timeout_seconds
            )
            cosy_client = CosyVoiceWorkerClient(
                tts_config.worker_url, timeout_seconds=tts_config.timeout_seconds
            )
            self.voice_design: VoiceDesignProvider = QwenVoiceDesignProvider(
                qwen_client, voice_config
            )
            self.tts: TTSProvider = QwenCosyVoiceTTSProvider(
                qwen_client, cosy_client, tts_config
            )
        else:
            self.llm = MockLLMProvider()
            self.voice_design = MockVoiceDesignProvider()
            self.tts = MockTTSProvider()

    @property
    def is_mock(self) -> bool:
        return self.llm.is_mock and self.voice_design.is_mock and self.tts.is_mock

    def summary(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "is_mock": self.is_mock,
            "llm": {"name": self.llm.name, "model": self.llm.model, "is_mock": self.llm.is_mock},
            "voice_design": {
                "name": self.voice_design.name,
                "model": self.voice_design.model,
                "is_mock": self.voice_design.is_mock,
            },
            "tts": {"name": self.tts.name, "model": self.tts.model, "is_mock": self.tts.is_mock},
        }