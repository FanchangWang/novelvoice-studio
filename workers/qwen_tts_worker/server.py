"""Qwen3-TTS VoiceDesign 本机 HTTP 引擎服务。

只监听 127.0.0.1，不下载模型、不读取任意路径。模型在第一次请求时加载，
可用 /v1/unload 主动释放显存。

用法（仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/server.py

环境变量：
  NOVELVOICE_MODEL_CACHE   模型缓存根目录（默认 Documents/ModelScope）
  NOVELVOICE_QWEN_MODEL_DIR 直接指定模型目录（优先）
  NOVELVOICE_QWEN_PRELOAD=1 启动时立即加载模型
"""

from __future__ import annotations

import argparse
import gc
import hmac
import io
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
ENGINE_INSTANCE_ID = os.getenv("NOVELVOICE_ENGINE_INSTANCE_ID", "")
ENGINE_CONTROL_TOKEN = os.getenv("NOVELVOICE_ENGINE_CONTROL_TOKEN", "")
DEFAULT_CACHE = Path(
    os.getenv(
        "NOVELVOICE_MODEL_CACHE",
        str(Path.home() / "Documents" / "ModelScope"),
    )
)
DEFAULT_MODEL_DIR = Path(os.getenv("NOVELVOICE_QWEN_MODEL_DIR", str(DEFAULT_CACHE / "Qwen3-TTS-12Hz-1.7B-VoiceDesign")))

GENERATION_KWARGS = {
    "do_sample": True,
    "top_k": 50,
    "top_p": 1.0,
    "temperature": 0.9,
    "repetition_penalty": 1.05,
    "max_new_tokens": 2048,
}

_LANGUAGE_MAP = {
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "chinese": "Chinese",
    "en": "English",
    "en-us": "English",
    "english": "English",
    "auto": "Auto",
}

_model: Any | None = None
_model_lock = threading.Lock()
_generate_lock = threading.Lock()
_loaded_at: str | None = None
_load_seconds: float | None = None
_load_error: str | None = None


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    instruct: str = Field(min_length=2, max_length=4000)
    language: str = "Chinese"
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


def _resolve_language(value: str) -> str:
    key = (value or "").strip().lower()
    return _LANGUAGE_MAP.get(key, value if value in {"Chinese", "English", "Auto"} else "Chinese")


def _ensure_model():  # type: ignore[no-untyped-def]
    global _model, _loaded_at, _load_seconds, _load_error
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not DEFAULT_MODEL_DIR.is_dir():
            _load_error = (
                f"模型目录不存在：{DEFAULT_MODEL_DIR}。"
                "请在项目根目录手动运行 pwsh.exe -File scripts/download_models.ps1，"
                "下载完成后重启引擎服务；应用不会自动下载模型。"
            )
            raise RuntimeError(_load_error)
        import torch
        from qwen_tts import Qwen3TTSModel

        started = time.perf_counter()
        _model = Qwen3TTSModel.from_pretrained(
            str(DEFAULT_MODEL_DIR),
            device_map="cuda:0" if torch.cuda.is_available() else "cpu",
            dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            attn_implementation="sdpa",
        )
        _load_seconds = round(time.perf_counter() - started, 3)
        _loaded_at = datetime.now(timezone.utc).isoformat()
        _load_error = None
        return _model


app = FastAPI(title="NovelVoice Qwen TTS Engine Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    import torch

    loaded = _model is not None
    gpu: dict[str, Any] = {"cuda_available": bool(torch.cuda.is_available())}
    if torch.cuda.is_available():
        gpu["device"] = torch.cuda.get_device_name(0)
        gpu["memory_allocated_mib"] = round(torch.cuda.memory_allocated(0) / 1024**2, 1)
    return {
        "ok": True,
        "worker": "qwen3_tts_voice_design",
        "instance_id": ENGINE_INSTANCE_ID,
        "pid": os.getpid(),
        "model_id": MODEL_ID,
        "model_dir": str(DEFAULT_MODEL_DIR),
        "model_dir_exists": DEFAULT_MODEL_DIR.is_dir(),
        "model_loaded": loaded,
        "loaded_at": _loaded_at,
        "load_seconds": _load_seconds,
        "load_error": _load_error,
        "torch": torch.__version__,
        "gpu": gpu,
    }


@app.post("/v1/shutdown")
def shutdown(
    x_nvs_engine_token: str | None = Header(default=None, alias="X-NVS-Engine-Token"),
) -> dict[str, Any]:
    """只在本控制面注入令牌时启用；由引擎服务控制器请求 uvicorn 优雅退出。"""
    if not ENGINE_CONTROL_TOKEN:
        raise HTTPException(status_code=404, detail="未启用控制接口")
    if not x_nvs_engine_token or not hmac.compare_digest(
        x_nvs_engine_token, ENGINE_CONTROL_TOKEN
    ):
        raise HTTPException(status_code=403, detail="控制令牌无效")
    server = getattr(app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=409, detail="服务尚未就绪")
    timer = threading.Timer(0.1, lambda: setattr(server, "should_exit", True))
    timer.daemon = True
    timer.start()
    return {"ok": True, "pid": os.getpid(), "instance_id": ENGINE_INSTANCE_ID}


@app.post("/v1/generate")
def generate(payload: GenerateRequest) -> Response:
    started = time.perf_counter()
    try:
        model = _ensure_model()
        import torch

        with _generate_lock:
            seed = payload.seed
            if seed is None:
                seed = int(time.time() * 1000) % (2**31 - 1)
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            wavs, sample_rate = model.generate_voice_design(
                text=payload.text,
                language=_resolve_language(payload.language),
                instruct=payload.instruct,
                non_streaming_mode=True,
                **GENERATION_KWARGS,
            )
        if not wavs:
            raise RuntimeError("模型没有返回音频")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Qwen 生成失败：{exc}") from exc

    wave = np.asarray(wavs[0], dtype=np.float32)
    wave = np.nan_to_num(wave, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > 0.999:
        wave = wave / peak * 0.99
    buffer = io.BytesIO()
    sf.write(buffer, wave, sample_rate, format="WAV", subtype="PCM_16")
    duration_ms = int(round(wave.size / sample_rate * 1000))
    elapsed = round(time.perf_counter() - started, 3)
    return Response(
        content=buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "X-NVS-Sample-Rate": str(sample_rate),
            "X-NVS-Duration-Ms": str(duration_ms),
            "X-NVS-Elapsed-Seconds": str(elapsed),
            "X-NVS-Seed": str(seed),
            "X-NVS-Model-Id": MODEL_ID,
        },
    )


@app.post("/v1/unload")
def unload() -> dict[str, Any]:
    global _model, _loaded_at, _load_seconds
    with _model_lock:
        _model = None
        _loaded_at = None
        _load_seconds = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "model_loaded": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Qwen3-TTS VoiceDesign Engine Service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--preload", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("出于本地优先原则，引擎服务只允许绑定回环地址")
    if args.preload or os.getenv("NOVELVOICE_QWEN_PRELOAD") == "1":
        try:
            _ensure_model()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 预加载失败，将在首次请求时重试：{exc}")
    import uvicorn

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    # 供 /v1/shutdown（控制面注入令牌时）触发优雅退出。
    app.state.uvicorn_server = server
    server.run()


if __name__ == "__main__":
    main()