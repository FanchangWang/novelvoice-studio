"""AI Provider 抽象与 Mock 实现。"""

from novelvoice_studio.ai.base import (
    AudioResult,
    ChapterSummaryResult,
    CharacterAnalysisResult,
    CharacterDraft,
    LLMProvider,
    ProviderHealth,
    RelationshipDraft,
    SegmentAnalysisResult,
    SegmentDraft,
    TTSProvider,
    VoiceCandidate,
    VoiceDesignProvider,
)
from novelvoice_studio.ai.registry import ProviderRegistry

__all__ = [
    "AudioResult",
    "ChapterSummaryResult",
    "CharacterAnalysisResult",
    "CharacterDraft",
    "LLMProvider",
    "ProviderHealth",
    "ProviderRegistry",
    "RelationshipDraft",
    "SegmentAnalysisResult",
    "SegmentDraft",
    "TTSProvider",
    "VoiceCandidate",
    "VoiceDesignProvider",
]