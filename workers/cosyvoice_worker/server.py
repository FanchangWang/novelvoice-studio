"""CosyVoice3 本机 HTTP Worker：Voice Conversion 与参考音去齿。

只监听 127.0.0.1，不下载模型、不读取任意路径。所有音频通过请求体传入，
内部写临时文件交给 CosyVoice，再返回 WAV 字节。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/server.py

环境变量：
  NOVELVOICE_MODEL_CACHE       模型缓存根目录
  NOVELVOICE_COSYVOICE_MODEL_DIR 直接指定模型目录（优先）
  NOVELVOICE_COSYVOICE_PRELOAD=1 启动时立即加载模型
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gc
import io
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from stage3c_deess import deess

MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
DEFAULT_CACHE = Path(
    os.getenv(
        "NOVELVOICE_MODEL_CACHE",
        str(Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"),
    )
)
DEFAULT_MODEL_DIR = Path(
    os.getenv("NOVELVOICE_COSYVOICE_MODEL_DIR", str(DEFAULT_CACHE / "Fun-CosyVoice3-0.5B-2512"))
)
SOURCE_DIR = Path(__file__).resolve().parent / "CosyVoice"

_model: Any | None = None
_model_lock = threading.Lock()
_infer_lock = threading.Lock()
_loaded_at: str | None = None
_load_seconds: float | None = None
_load_error: str | None = None


class VCRequest(BaseModel):
    source_b64: str = Field(min_length=16)
    reference_b64: str = Field(min_length=16)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)


class DeessRequest(BaseModel):
    audio_b64: str = Field(min_length=16)
    threshold_ratio: float = Field(default=0.24, ge=0.0, le=1.0)
    full_ratio: float = Field(default=0.62, ge=0.0, le=1.0)
    max_reduction_db: float = Field(default=5.0, ge=0.0, le=30.0)
    band_low: float = Field(default=4000.0, ge=1000.0, le=16000.0)
    band_high: float = Field(default=10000.0, ge=2000.0, le=20000.0)


def _decode_audio(value: str, label: str) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{label} 不是有效的 base64") from exc
    if len(data) < 64:
        raise HTTPException(status_code=422, detail=f"{label} 太小，不是有效音频")
    return data


def _ensure_model():  # type: ignore[no-untyped-def]
    global _model, _loaded_at, _load_seconds, _load_error
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not DEFAULT_MODEL_DIR.is_dir():
            _load_error = f"模型目录不存在：{DEFAULT_MODEL_DIR}"
            raise RuntimeError(_load_error)
        if not SOURCE_DIR.is_dir():
            _load_error = f"CosyVoice 源码目录不存在：{SOURCE_DIR}"
            raise RuntimeError(_load_error)
        sys.path.insert(0, str(SOURCE_DIR))
        sys.path.insert(0, str(SOURCE_DIR / "third_party" / "Matcha-TTS"))
        from cosyvoice.cli.cosyvoice import AutoModel

        started = time.perf_counter()
        _model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
        _load_seconds = round(time.perf_counter() - started, 3)
        _loaded_at = datetime.now(timezone.utc).isoformat()
        _load_error = None
        return _model


def _load_reference(data: bytes, directory: Path, name: str) -> Path:
    try:
        samples, sample_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"{name} 不是有效 WAV：{exc}") from exc
    if samples.size == 0:
        raise HTTPException(status_code=422, detail=f"{name} 为空")
    path = directory / f"{name}.wav"
    sf.write(str(path), samples, sample_rate, subtype="PCM_16")
    return path


app = FastAPI(title="NovelVoice CosyVoice Worker", version="0.1.0")


@app.get("/health")
def health() -> dict[str, Any]:
    import torch

    gpu: dict[str, Any] = {"cuda_available": bool(torch.cuda.is_available())}
    if torch.cuda.is_available():
        gpu["device"] = torch.cuda.get_device_name(0)
        gpu["memory_allocated_mib"] = round(torch.cuda.memory_allocated(0) / 1024**2, 1)
    return {
        "ok": True,
        "worker": "cosyvoice3_vc",
        "model_id": MODEL_ID,
        "model_dir": str(DEFAULT_MODEL_DIR),
        "model_dir_exists": DEFAULT_MODEL_DIR.is_dir(),
        "source_dir_exists": SOURCE_DIR.is_dir(),
        "model_loaded": _model is not None,
        "loaded_at": _loaded_at,
        "load_seconds": _load_seconds,
        "load_error": _load_error,
        "sample_rate": getattr(_model, "sample_rate", None),
        "torch": torch.__version__,
        "gpu": gpu,
    }


@app.post("/v1/vc")
def voice_conversion(payload: VCRequest) -> Response:
    started = time.perf_counter()
    source_bytes = _decode_audio(payload.source_b64, "source_b64")
    reference_bytes = _decode_audio(payload.reference_b64, "reference_b64")
    try:
        model = _ensure_model()
        from cosyvoice.utils.common import set_all_random_seed

        with tempfile.TemporaryDirectory(prefix="nvs_vc_") as temp_dir:
            directory = Path(temp_dir)
            source_path = _load_reference(source_bytes, directory, "source")
            reference_path = _load_reference(reference_bytes, directory, "reference")
            with _infer_lock:
                set_all_random_seed(payload.seed)
                outputs = model.inference_vc(
                    str(source_path), str(reference_path), stream=False, speed=payload.speed
                )
                chunks = [
                    item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs
                ]
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"CosyVoice VC 失败：{exc}") from exc

    if not chunks:
        raise HTTPException(status_code=500, detail="CosyVoice VC 未返回音频")
    wave = np.concatenate(chunks).astype(np.float32)
    wave = np.nan_to_num(wave, nan=0.0, posinf=0.0, neginf=0.0)
    peak = float(np.max(np.abs(wave))) if wave.size else 0.0
    if peak > 0.999:
        wave = wave / peak * 0.99
    sample_rate = int(model.sample_rate)
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
            "X-NVS-Model-Id": MODEL_ID,
            "X-NVS-Speed": str(payload.speed),
        },
    )


@app.post("/v1/deess")
def deess_audio(payload: DeessRequest) -> Response:
    data = _decode_audio(payload.audio_b64, "audio_b64")
    try:
        samples, sample_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"audio_b64 不是有效 WAV：{exc}") from exc
    if samples.size == 0:
        raise HTTPException(status_code=422, detail="音频为空")
    mono = samples.mean(axis=1).astype(np.float64)
    if payload.band_high <= payload.band_low:
        raise HTTPException(status_code=422, detail="band_high 必须大于 band_low")
    processed, stats = deess(
        mono,
        sample_rate,
        band_low=payload.band_low,
        band_high=payload.band_high,
        threshold_ratio=payload.threshold_ratio,
        full_ratio=payload.full_ratio,
        max_reduction_db=payload.max_reduction_db,
    )
    buffer = io.BytesIO()
    sf.write(buffer, processed, sample_rate, format="WAV", subtype="PCM_16")
    return Response(
        content=buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "X-NVS-Sample-Rate": str(sample_rate),
            "X-NVS-Duration-Ms": str(int(round(processed.size / sample_rate * 1000))),
            "X-NVS-Deess-Stats": json.dumps(stats, separators=(",", ":")),
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
    parser = argparse.ArgumentParser(description="CosyVoice3 VC Worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--preload", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("出于本地优先原则，Worker 只允许绑定回环地址")
    if args.preload or os.getenv("NOVELVOICE_COSYVOICE_PRELOAD") == "1":
        try:
            _ensure_model()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 预加载失败，将在首次请求时重试：{exc}")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()