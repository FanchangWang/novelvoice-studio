"""健康检查、系统状态与只读配置。"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from novelvoice_studio import __version__
from novelvoice_studio.api.deps import EngineDep, RegistryDep, SettingsDep
from novelvoice_studio.schemas.system import (
    HealthResponse,
    PublicSettingsResponse,
    SystemStatusResponse,
)
from novelvoice_studio.services.system_status import collect_system_status, public_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(registry: RegistryDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.now(timezone.utc),
        providers_mode=registry.mode,
    )


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status(
    settings: SettingsDep,
    engine: EngineDep,
    registry: RegistryDep,
) -> SystemStatusResponse:
    return await collect_system_status(settings, engine, registry)


@router.get("/system/settings", response_model=PublicSettingsResponse)
def get_public_settings(
    settings: SettingsDep,
    registry: RegistryDep,
) -> PublicSettingsResponse:
    return public_settings(settings, registry)


@router.post("/system/shutdown", include_in_schema=False)
def shutdown(request: Request, settings: SettingsDep) -> dict[str, str]:
    """仅当启动器注入了控制令牌时可用，供本机脚本优雅停止服务。"""
    if not settings.control_token:
        raise HTTPException(status_code=404, detail="Not Found")
    provided = request.headers.get("x-nvs-control", "")
    if not hmac.compare_digest(provided, settings.control_token):
        raise HTTPException(status_code=403, detail="控制令牌无效")
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=409, detail="当前进程不支持受控停止")
    logger.warning("收到本机停机请求，准备优雅退出")
    server.should_exit = True
    return {"status": "stopping"}