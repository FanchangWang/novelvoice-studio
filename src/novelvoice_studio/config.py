"""应用配置：YAML 文件 + NOVELVOICE_ 环境变量覆盖。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


class AppConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    language: str = "zh-CN"
    data_dir: str = ""
    log_level: str = "INFO"
    auto_migrate: bool = True
    max_upload_mb: int = Field(default=64, ge=1, le=1024)
    frontend_dir: str = ""

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        allowed = {"127.0.0.1", "localhost", "::1"}
        if value not in allowed:
            raise ValueError("app.host 只能是 127.0.0.1 / localhost / ::1（本地优先）")
        return value


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest"
    enable_thinking: bool = False
    context_length: int = 8192
    keep_alive: str = "5m"
    timeout_seconds: float = 120.0
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    json_retries: int = Field(default=2, ge=0, le=5)
    # 单次调用输出上限：防止小模型在 JSON 里复读，直到 Ollama 的 repeat limit 把请求掐断。
    num_predict: int = Field(default=6144, ge=128, le=32768)
    repeat_penalty: float = Field(default=1.12, ge=1.0, le=2.0)
    repeat_last_n: int = Field(default=256, ge=0, le=4096)

    @field_validator("base_url")
    @classmethod
    def _loopback_llm(cls, value: str) -> str:
        # 小说原文属于敏感内容，真实 LLM 只允许连接本机 Ollama。
        from urllib.parse import urlparse

        host = (urlparse(value).hostname or "").lower()
        allowed = {"127.0.0.1", "localhost", "::1"}
        if host not in allowed:
            raise ValueError("llm.base_url 只能是本机回环地址，避免小说内容离开本机")
        return value.rstrip("/")


def _loopback_url(value: str, field_label: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(value).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{field_label} 只能是本机回环地址，避免音频离开本机")
    return value.rstrip("/")


class VoiceDesignConfig(BaseModel):
    provider: str = "qwen3_tts"
    model_id: str = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    worker_url: str = "http://127.0.0.1:8766"
    timeout_seconds: float = Field(default=180.0, ge=10.0, le=600.0)
    candidates: int = Field(default=3, ge=1, le=5)
    seeds: list[int] = Field(default_factory=lambda: [42, 123, 2026])
    reference_text: str = (
        "雨停以后，街上的灯一盏一盏亮起来。"
        "风从巷口吹过，带着潮湿的凉意。"
        "远处有人慢慢走过，脚步声很轻。"
    )
    narrator_description: str = (
        "成年男性，中音偏明亮，温和清晰，语感自然；"
        "咬字干净，气声少，齿音轻，句尾收得利落。"
    )

    @field_validator("worker_url")
    @classmethod
    def _loopback_worker(cls, value: str) -> str:
        return _loopback_url(value, "voice_design.worker_url")

    @field_validator("seeds")
    @classmethod
    def _seed_range(cls, value: list[int]) -> list[int]:
        cleaned = [int(item) for item in value if 0 <= int(item) <= 2**31 - 1]
        return cleaned or [42, 123, 2026]


class ReferenceDeessConfig(BaseModel):
    """参考音去齿：阶段 3C 定为 narrator_03 的默认预处理。"""

    enabled: bool = True
    band_low: float = Field(default=4000.0, ge=1000.0, le=16000.0)
    band_high: float = Field(default=10000.0, ge=2000.0, le=20000.0)
    threshold_ratio: float = Field(default=0.24, ge=0.0, le=1.0)
    full_ratio: float = Field(default=0.62, ge=0.0, le=1.0)
    max_reduction_db: float = Field(default=5.0, ge=0.0, le=30.0)


class TTSConfig(BaseModel):
    provider: str = "cosyvoice3"
    model_id: str = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
    worker_url: str = "http://127.0.0.1:8767"
    default_speed: float = 1.0
    timeout_seconds: float = Field(default=240.0, ge=10.0, le=900.0)
    default_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    max_text_chars: int = Field(default=1200, ge=100, le=4000)
    reference_deess: ReferenceDeessConfig = Field(default_factory=ReferenceDeessConfig)

    @field_validator("worker_url")
    @classmethod
    def _loopback_worker(cls, value: str) -> str:
        return _loopback_url(value, "tts.worker_url")


class AnalysisConfig(BaseModel):
    # 单章超长时明确报错并提示拆分，绝不静默截断原文。
    max_chapter_chars: int = Field(default=6000, ge=1000, le=100000)
    speaker_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    emotion_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    alias_confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # 分场/角色状态低于此置信度时保留底层诊断，不再作为待处理内容异常。
    scene_confidence_threshold: float = Field(default=0.4, ge=0.0, le=1.0)


class JobsConfig(BaseModel):
    max_gpu_concurrency: int = 1
    max_retries: int = 2
    auto_resume: bool = True
    # 角色圣经逐章抽取的并发章节数；需同时把 Ollama 的 OLLAMA_NUM_PARALLEL 调到相同值。
    bible_parallel_chapters: int = Field(default=2, ge=1, le=8)


class ExportConfig(BaseModel):
    audio_codec: str = "aac"
    audio_bitrate: str = "96k"
    chapter_gap_ms: int = 800


class ProvidersConfig(BaseModel):
    # mock: 不加载任何模型；real: 本机 Ollama + 本机语音引擎服务
    mode: Literal["mock", "real"] = "mock"


class YamlConfigSource(PydanticBaseSettingsSource):
    """从 config/config.yaml 读取配置；文件不存在时返回空。"""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        path = Path(os.environ.get("NOVELVOICE_CONFIG", str(DEFAULT_CONFIG_PATH)))
        self.path = path
        self._data: dict[str, Any] = {}
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"配置文件必须是 YAML 映射: {path}")
            self._data = loaded

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Settings(BaseSettings):
    # 仅由 scripts/server_control.py 注入，用于本机优雅停止；不对外展示。
    control_token: str = Field(default="", repr=False)

    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    voice_design: VoiceDesignConfig = Field(default_factory=VoiceDesignConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)

    model_config = SettingsConfigDict(
        env_prefix="NOVELVOICE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 优先级：显式参数 > 环境变量 > .env > YAML > secrets
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls),
            file_secret_settings,
        )

    @property
    def data_dir(self) -> Path:
        configured = self.app.data_dir.strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return (Path.home() / "Documents" / "NovelVoiceStudio").resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path.as_posix()}"

    def ensure_dirs(self) -> Path:
        data_dir = self.data_dir
        for name in ("books", "logs", "backups", "cache", "trash"):
            (data_dir / name).mkdir(parents=True, exist_ok=True)
        return data_dir

    @property
    def frontend_dist(self) -> Path:
        if self.app.frontend_dir.strip():
            return Path(self.app.frontend_dir).expanduser().resolve()
        return PROJECT_ROOT / "frontend" / "dist"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()