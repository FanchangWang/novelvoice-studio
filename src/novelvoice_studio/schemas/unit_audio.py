"""v0.5.0 P7：剧本单元音频生成与版本响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UnitAudioAssetOut(BaseModel):
    id: str
    script_unit_id: str
    voice_actor_id: str | None = None
    voice_actor_name: str | None = None
    version: int
    format: str
    sample_rate: int
    channels: int
    sample_width_bits: int
    duration_ms: int
    file_size_bytes: int
    tts_provider: str | None = None
    tts_model: str | None = None
    is_valid: bool
    is_current: bool = False
    audio_url: str
    created_at: datetime


class ScriptUnitAudioOut(BaseModel):
    unit_id: str
    audio_status: str
    voice_actor_id: str | None = None
    voice_actor_name: str | None = None
    render_hash: str | None = None
    asset: UnitAudioAssetOut | None = None


class UnitAudioVersionListResponse(BaseModel):
    unit_id: str
    current_asset_id: str | None = None
    items: list[UnitAudioAssetOut] = Field(default_factory=list)
    total: int = 0