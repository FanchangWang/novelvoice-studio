"""系统状态与健康检查模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    timestamp: datetime
    providers_mode: str


class ComponentStatus(BaseModel):
    id: str
    label: str
    ok: bool
    critical: bool = False
    detail: str
    meta: dict[str, Any] = Field(default_factory=dict)


class SystemStatusResponse(BaseModel):
    checked_at: datetime
    overall_ok: bool
    mock_mode: bool
    providers_mode: str
    version: str
    data_dir: str
    components: list[ComponentStatus]


class PublicSettingsResponse(BaseModel):
    app: dict[str, Any]
    llm: dict[str, Any]
    voice_design: dict[str, Any]
    tts: dict[str, Any]
    analysis: dict[str, Any]
    jobs: dict[str, Any]
    export: dict[str, Any]
    providers: dict[str, Any]
    read_only: bool = True