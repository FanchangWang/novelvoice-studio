"""系统状态检查：数据库、磁盘、FFmpeg、Ollama、语音引擎服务、GPU。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Engine, text

from novelvoice_studio import __version__
from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import Settings
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.schemas.system import (
    ComponentStatus,
    PublicSettingsResponse,
    SystemStatusResponse,
)


def _db_status(engine: Engine, db_path: Path) -> ComponentStatus:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            try:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).scalar_one_or_none()
            except Exception:
                revision = None
        size = db_path.stat().st_size if db_path.is_file() else 0
        return ComponentStatus(
            id="database",
            label="SQLite 数据库",
            ok=True,
            critical=True,
            detail="连接正常",
            meta={
                "path": str(db_path),
                "size_bytes": size,
                "migration": revision or "unknown",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentStatus(
            id="database",
            label="SQLite 数据库",
            ok=False,
            critical=True,
            detail=f"连接失败：{exc}",
            meta={"path": str(db_path)},
        )


def _disk_status(data_dir: Path) -> ComponentStatus:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(data_dir)
        free_gb = usage.free / 1024**3
        total_gb = usage.total / 1024**3
        ok = free_gb >= 1
        return ComponentStatus(
            id="disk",
            label="数据目录",
            ok=ok,
            critical=True,
            detail=(
                f"可用 {free_gb:.1f} GB / 共 {total_gb:.1f} GB"
                if ok
                else f"磁盘空间不足（可用 {free_gb:.2f} GB）"
            ),
            meta={
                "path": str(data_dir),
                "free_bytes": usage.free,
                "total_bytes": usage.total,
            },
        )
    except OSError as exc:
        return ComponentStatus(
            id="disk",
            label="数据目录",
            ok=False,
            critical=True,
            detail=f"无法访问数据目录：{exc}",
            meta={"path": str(data_dir)},
        )


def _ffmpeg_status() -> ComponentStatus:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        return ComponentStatus(
            id="ffmpeg",
            label="FFmpeg",
            ok=False,
            detail="未找到 ffmpeg（导出音频时需要）",
            meta={"ffprobe": ffprobe or ""},
        )
    try:
        completed = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = (completed.stdout or "").splitlines()[0] if completed.stdout else ""
        return ComponentStatus(
            id="ffmpeg",
            label="FFmpeg",
            ok=completed.returncode == 0 and bool(ffprobe),
            detail=first_line or f"退出码 {completed.returncode}",
            meta={"ffmpeg": ffmpeg, "ffprobe": ffprobe or ""},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ComponentStatus(
            id="ffmpeg",
            label="FFmpeg",
            ok=False,
            detail=f"调用失败：{exc}",
            meta={"ffmpeg": ffmpeg, "ffprobe": ffprobe or ""},
        )


def _gpu_status() -> ComponentStatus:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ComponentStatus(
            id="gpu",
            label="NVIDIA GPU",
            ok=False,
            detail="未找到 nvidia-smi（CPU/Mock 模式仍可使用基础功能）",
        )
    try:
        completed = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        if completed.returncode != 0 or not lines:
            return ComponentStatus(
                id="gpu",
                label="NVIDIA GPU",
                ok=False,
                detail=(completed.stderr or "nvidia-smi 未返回 GPU 信息").strip()[:300],
            )
        devices = []
        for line in lines:
            parts = [part.strip() for part in line.split(",")]
            devices.append(
                {
                    "name": parts[0] if len(parts) > 0 else "unknown",
                    "memory_total_mib": parts[1] if len(parts) > 1 else "",
                    "driver": parts[2] if len(parts) > 2 else "",
                }
            )
        return ComponentStatus(
            id="gpu",
            label="NVIDIA GPU",
            ok=True,
            detail=f"检测到 {len(devices)} 块 GPU",
            meta={"devices": devices},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ComponentStatus(
            id="gpu",
            label="NVIDIA GPU",
            ok=False,
            detail=f"调用 nvidia-smi 失败：{exc}",
        )


async def _ollama_status(settings: Settings) -> ComponentStatus:
    base = settings.llm.base_url.rstrip("/")
    url = f"{base}/api/tags"
    try:
        # 本机服务检查不能被 HTTP_PROXY / 空的 ALL_PROXY 干扰。
        async with httpx.AsyncClient(timeout=2.5, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        models = [item.get("name", "") for item in payload.get("models", []) if item.get("name")]
        configured = settings.llm.model
        found = any(
            name == configured or name.split(":")[0] == configured.split(":")[0]
            for name in models
        )
        return ComponentStatus(
            id="ollama",
            label="Ollama",
            ok=True,
            detail=(
                f"已连接，共 {len(models)} 个模型"
                + ("" if found else "；尚未找到配置的模型，真实分析不可用")
            ),
            meta={
                "url": base,
                "configured_model": configured,
                "model_available": found,
                "models": models[:20],
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentStatus(
            id="ollama",
            label="Ollama",
            ok=False,
            detail="未连接；Mock 模式分析仍可使用",
            meta={
                "url": base,
                "configured_model": settings.llm.model,
                "error_type": exc.__class__.__name__,
            },
        )


async def _worker_status(
    worker_id: str, label: str, base_url: str, model_id: str
) -> ComponentStatus:
    url = f"{base_url.rstrip('/')}/health"
    try:
        # 引擎服务与本机 Provider 一样，不得经过系统代理。
        async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
            response = await client.get(url)
        health: dict[str, Any] = {}
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            health = parsed
        ok = response.status_code == 200 and health.get("ok", True) is not False
        loaded = health.get("model_loaded")
        if ok and loaded is True:
            detail = "引擎服务已响应，模型已加载"
        elif ok and loaded is False:
            detail = "引擎服务已响应，模型按需加载"
        elif ok:
            detail = f"引擎服务已响应（HTTP {response.status_code}）"
        elif response.status_code != 200:
            detail = f"引擎服务返回 HTTP {response.status_code}"
        else:
            detail = "引擎服务报告未就绪"
        return ComponentStatus(
            id=worker_id,
            label=label,
            ok=ok,
            detail=detail,
            meta={
                "url": base_url,
                "model_id": model_id,
                "health_url": url,
                "worker": health.get("worker"),
                "model_loaded": loaded,
                "pid": health.get("pid"),
                "load_seconds": health.get("load_seconds"),
                "load_error": health.get("load_error"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ComponentStatus(
            id=worker_id,
            label=label,
            ok=False,
            detail="引擎服务未启动（音色设计与配音生成需要）",
            meta={
                "url": base_url,
                "model_id": model_id,
                "health_url": url,
                "error_type": exc.__class__.__name__,
            },
        )


async def collect_system_status(
    settings: Settings, engine: Engine, registry: ProviderRegistry
) -> SystemStatusResponse:
    db_status = _db_status(engine, settings.db_path)
    disk_status = _disk_status(settings.data_dir)
    external = await asyncio.gather(
        asyncio.to_thread(_ffmpeg_status),
        asyncio.to_thread(_gpu_status),
        _ollama_status(settings),
        _worker_status(
            "voice_design_worker",
            "Qwen3-TTS VoiceDesign 引擎服务",
            settings.voice_design.worker_url,
            settings.voice_design.model_id,
        ),
        _worker_status(
            "tts_worker",
            "CosyVoice3 引擎服务",
            settings.tts.worker_url,
            settings.tts.model_id,
        ),
    )
    components = [db_status, disk_status, *external]
    overall_ok = all(item.ok for item in components if item.critical)
    return SystemStatusResponse(
        checked_at=utc_now(),
        overall_ok=overall_ok,
        mock_mode=registry.is_mock,
        providers_mode=registry.mode,
        version=__version__,
        data_dir=str(settings.data_dir),
        components=components,
    )


def public_settings(settings: Settings, registry: ProviderRegistry) -> PublicSettingsResponse:
    return PublicSettingsResponse(
        app={**settings.app.model_dump(), "data_dir": str(settings.data_dir)},
        llm=settings.llm.model_dump(),
        voice_design=settings.voice_design.model_dump(),
        tts=settings.tts.model_dump(),
        analysis=settings.analysis.model_dump(),
        jobs=settings.jobs.model_dump(),
        export=settings.export.model_dump(),
        providers={"mode": registry.mode, "is_mock": registry.is_mock},
        read_only=True,
    )