"""Qwen3-TTS VoiceDesign 本机 HTTP Worker。

只监听 127.0.0.1，不下载模型、不读取任意路径。模型在第一次请求时加载，
可用 /v1/unload 主动释放显存。

用法（仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/server.py

环境变量：
  NOVELVOICE_MODEL_CACHE   模型缓存根目录（默认 Documents/NovelVoiceStudio/cache/modelscope）
  NOVELVOICE_QWEN_MODEL_DIR 直接指定模型目录（优先）
  NOVELVOICE_QWEN_PRELOAD=1 启动时立即加载模型
"""

from __future__ import annotations

import argparse
import gc
import io
import os
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

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_CACHE = Path(
    os.getenv(
        "NOVELVOICE_MODEL_CACHE",
        str(Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"),
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
            _load_error = f"模型目录不存在：{DEFAULT_MODEL_DIR}"
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


app = FastAPI(title="NovelVoice Qwen TTS Worker", version="0.1.0")


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
    parser = argparse.ArgumentParser(description="Qwen3-TTS VoiceDesign Worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--preload", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("出于本地优先原则，Worker 只允许绑定回环地址")
    if args.preload or os.getenv("NOVELVOICE_QWEN_PRELOAD") == "1":
        try:
            _ensure_model()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 预加载失败，将在首次请求时重试：{exc}")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()