"""Provider 抽象接口与通用数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

Basis = Literal["explicit", "inferred", "unknown"]
Gender = Literal["male", "female", "other", "unknown"]
AgeRange = Literal[
    "child", "teenager", "young_adult", "adult", "middle_aged", "elderly", "unknown"
]
CharacterType = Literal["human", "animal", "non_human", "unknown"]
RoleTier = Literal["protagonist", "major", "minor", "extra", "unknown"]
TimeMode = Literal["present", "flashback", "flashforward", "dream", "unknown"]
NarrativeMode = Literal["linear", "nonlinear", "unknown"]
SegmentType = Literal["narration", "dialogue", "inner_monologue", "unknown"]
SpeakerType = Literal["character", "narrator", "unknown"]
Emotion = Literal[
    "neutral",
    "happy",
    "excited",
    "angry",
    "sad",
    "fearful",
    "surprised",
    "doubtful",
    "sarcastic",
    "tender",
    "embarrassed",
    "determined",
    "tired",
]
Delivery = Literal[
    "normal",
    "whispering",
    "shouting",
    "crying",
    "laughing",
    "breathless",
    "slow",
    "hesitant",
]

AGE_GROUP_ALIASES = {
    "teen": "teenager",
    "young": "young_adult",
    "middle": "middle_aged",
    "senior": "elderly",
    "elder": "elderly",
}


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioResult:
    audio_bytes: bytes
    sample_rate: int
    channels: int
    duration_ms: int
    format: str = "wav"
    provider: str = "mock"
    model: str = "mock"
    is_mock: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VoiceCandidate:
    candidate_id: str
    name: str
    description: str
    instruct_text: str
    reference_text: str
    seed: int
    audio: AudioResult | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class EvidenceDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chapter_id: str | None = None
    quote: str = ""
    reason: str | None = None


class AliasDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    confidence: float = 0.0
    basis: Basis = "inferred"
    evidence: list[EvidenceDraft] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))


def _coerce_evidence(value: object) -> list[EvidenceDraft] | object:
    if not isinstance(value, list):
        return value
    result: list[EvidenceDraft | dict[str, Any] | str] = []
    for item in value:
        if isinstance(item, str):
            result.append(EvidenceDraft(quote=item))
        else:
            result.append(item)
    return result


def _coerce_aliases(value: object) -> object:
    if not isinstance(value, list):
        return value
    result: list[AliasDraft | dict[str, Any] | str] = []
    for item in value:
        if isinstance(item, str):
            result.append(AliasDraft(name=item))
        else:
            result.append(item)
    return result


class CharacterDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(validation_alias=AliasChoices("name", "canonical_name"))
    aliases: list[AliasDraft] = Field(default_factory=list)
    gender: Gender = "unknown"
    gender_basis: Basis = "unknown"
    age_range: AgeRange = Field(
        default="unknown", validation_alias=AliasChoices("age_range", "age_group")
    )
    age_basis: Basis = "unknown"
    character_type: CharacterType = "unknown"
    identity_description: str | None = None
    personality: list[str] = Field(default_factory=list)
    personality_desc: str | None = None
    appearance_desc: str | None = None
    voice_description: str | None = None
    evidence: list[EvidenceDraft] = Field(default_factory=list)
    confidence: float = 0.0
    is_provisional: bool = False

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases_before(cls, value: object) -> object:
        return _coerce_aliases(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_before(cls, value: object) -> object:
        return _coerce_evidence(value)

    @field_validator("age_range", mode="before")
    @classmethod
    def _age_alias(cls, value: object) -> object:
        if isinstance(value, str):
            return AGE_GROUP_ALIASES.get(value.strip().lower(), value.strip().lower())
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))

    @field_validator("personality", mode="before")
    @classmethod
    def _personality_before(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value


class RelationshipDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    source_name: str = Field(
        validation_alias=AliasChoices("source_name", "source", "character_a")
    )
    target_name: str = Field(
        validation_alias=AliasChoices("target_name", "target", "character_b")
    )
    relationship_type: str = "unknown"
    description: str | None = None
    evidence: list[EvidenceDraft] = Field(default_factory=list)
    confidence: float = 0.0
    is_provisional: bool = False

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_before(cls, value: object) -> object:
        return _coerce_evidence(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))


class CharacterAnalysisResult(BaseModel):
    characters: list[CharacterDraft] = Field(default_factory=list)
    relationships: list[RelationshipDraft] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class SegmentDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paragraph_index: int = 0
    source_text: str = Field(min_length=1)
    segment_type: SegmentType = "unknown"
    speaker_type: SpeakerType = "unknown"
    speaker_name: str | None = None
    speaker_confidence: float = 0.0
    emotion: Emotion = "neutral"
    emotion_confidence: float = 0.0
    intensity: int = Field(default=1, ge=1, le=3)
    delivery: Delivery = "normal"
    tts_instruction: str | None = None
    evidence: list[EvidenceDraft] = Field(default_factory=list)

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_before(cls, value: object) -> object:
        return _coerce_evidence(value)

    @field_validator("speaker_confidence", "emotion_confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))


class SegmentAnalysisResult(BaseModel):
    segments: list[SegmentDraft] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ChapterSummaryResult(BaseModel):
    summary: str = Field(min_length=1)
    key_events: list[str] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class BibleTimelineDraft(BaseModel):
    """单章在剧情时间线上的提示；不是章节顺序。"""

    model_config = ConfigDict(extra="ignore")

    story_time_label: str | None = None
    time_mode: TimeMode = "unknown"
    note: str | None = None
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))


class BibleCharacterDraft(CharacterDraft):
    """单章的人物观察：既含全书身份，也含本章所处的年龄/心理阶段。"""

    role_tier: RoleTier = "unknown"
    role_hint: str | None = None
    arc_note: str | None = None


class BibleChapterResult(BaseModel):
    characters: list[BibleCharacterDraft] = Field(default_factory=list)
    relationships: list[RelationshipDraft] = Field(default_factory=list)
    timeline: BibleTimelineDraft = Field(default_factory=BibleTimelineDraft)
    meta: dict[str, Any] = Field(default_factory=dict)


class BibleArcDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    label: str = "未分阶段"
    age_stage: AgeRange = Field(
        default="unknown", validation_alias=AliasChoices("age_stage", "age_range")
    )
    chapter_start: int | None = Field(
        default=None, validation_alias=AliasChoices("chapter_start", "start_sequence")
    )
    chapter_end: int | None = Field(
        default=None, validation_alias=AliasChoices("chapter_end", "end_sequence")
    )
    story_time_start: str | None = None
    story_time_end: str | None = None
    summary: str | None = None
    voice_hint: str | None = None
    confidence: float = 0.0

    @field_validator("age_stage", mode="before")
    @classmethod
    def _age_alias(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            return AGE_GROUP_ALIASES.get(lowered, lowered)
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))


class BibleMergeCharacterDraft(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    merge_names: list[str] = Field(default_factory=list)
    role_tier: RoleTier = "unknown"
    overview: str | None = None
    arcs: list[BibleArcDraft] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("merge_names", mode="before")
    @classmethod
    def _merge_names_before(cls, value: object) -> object:
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))


class BibleMergeResult(BaseModel):
    characters: list[BibleMergeCharacterDraft] = Field(default_factory=list)
    summary: str | None = None
    notes: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class BibleTimelineChapterDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sequence: int
    story_time_label: str | None = None
    story_order: int | None = None
    time_mode: TimeMode = "unknown"


class BibleTimelineResult(BaseModel):
    narrative_mode: NarrativeMode = "unknown"
    chapters: list[BibleTimelineChapterDraft] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class LLMProvider(ABC):
    name = "llm"
    model = ""
    is_mock = False

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    @abstractmethod
    def analyze_characters(
        self,
        text: str,
        known_characters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> CharacterAnalysisResult: ...

    @abstractmethod
    def analyze_segments(
        self,
        text: str,
        known_characters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> SegmentAnalysisResult: ...

    @abstractmethod
    def summarize_chapter(
        self, text: str, known_characters: list[dict[str, Any]]
    ) -> ChapterSummaryResult: ...

    def extract_bible_characters(
        self,
        text: str,
        known_characters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> BibleChapterResult:
        """P2 单章/单窗口人物观察。默认不支持，由具体 Provider 实现。"""
        raise NotImplementedError("当前 LLM Provider 不支持全书角色圣经分析")

    def merge_bible_candidates(
        self,
        candidates: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> BibleMergeResult:
        """P2 全局归并：只在确定性聚类后处理歧义、层级与人物弧。"""
        raise NotImplementedError("当前 LLM Provider 不支持角色圣经归并")

    def synthesize_bible_timeline(
        self,
        chapters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> BibleTimelineResult:
        """P2 剧情时间线综合；输入是章节摘要，不是整本书原文。"""
        raise NotImplementedError("当前 LLM Provider 不支持剧情时间线综合")

    def unload_model(self) -> None:
        return None


class VoiceDesignProvider(ABC):
    name = "voice_design"
    model = ""
    is_mock = False

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    def design_voice(
        self,
        description: str,
        reference_text: str,
        language: str = "zh-CN",
        *,
        seed: int | None = None,
    ) -> VoiceCandidate: ...

    @abstractmethod
    def preview_voice(self, candidate: VoiceCandidate, text: str) -> AudioResult: ...

    def unload_model(self) -> None:
        return None


class TTSProvider(ABC):
    name = "tts"
    model = ""
    is_mock = False

    @abstractmethod
    def health_check(self) -> ProviderHealth: ...

    @abstractmethod
    def load_voice_profile(self, profile: dict[str, Any]) -> None: ...

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice_profile: dict[str, Any],
        *,
        emotion: str | None = None,
        intensity: float = 1.0,
        delivery: str | None = None,
        instruction: str | None = None,
        speed: float = 1.0,
    ) -> AudioResult: ...

    def process_reference(
        self, audio_bytes: bytes, *, params: dict[str, Any] | None = None
    ) -> AudioResult | None:
        """可选：绑定音色时预处理参考音；None 表示该 Provider 不处理。"""
        return None

    def unload_model(self) -> None:
        return None