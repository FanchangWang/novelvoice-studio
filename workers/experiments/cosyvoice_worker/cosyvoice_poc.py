"""阶段 0 POC：用 CosyVoice3 把同一段参考音合成为 neutral / angry / sad。

前置条件：
  1. CosyVoice 源码已克隆到 workers/cosyvoice_worker/CosyVoice
  2. 该源码目录依赖已用 Python 3.10 环境安装
  3. 模型已通过 scripts/download_models.ps1 下载到 ModelScope 缓存目录
  4. 已运行 qwen_tts_worker/voice_design_poc.py 生成参考音

用法（在仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/cosyvoice_poc.py

输出：
  %USERPROFILE%\\Documents\\NovelVoiceStudio\\poc_output\\emotions\\
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import soundfile as sf
import torch

ROOT = Path(__file__).resolve().parents[2] / "cosyvoice_worker"
SOURCE_DIR = ROOT / "CosyVoice"
CACHE = Path(os.getenv(
    "NOVELVOICE_MODEL_CACHE",
    str(Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"),
))
MODEL_DIR = CACHE / "Fun-CosyVoice3-0.5B-2512"
REF_WAV = CACHE.parent / "poc_output" / "voices" / "candidate_01.wav"
OUT_DIR = CACHE.parent / "poc_output" / "emotions"

REF_TEXT = "你竟然敢骗我？我对你那么信任，你居然骗我！"
TEXT = "你竟然敢骗我！我以为你是真心的，原来全都是假的。"

EMOTIONS = [
    ("neutral", "用平静、克制的语气说这句话。"),
    ("angry", "愤怒而激动地大声质问，语速略快，保持角色音色不变。"),
    ("sad", "悲伤、压抑地低声说，语速较慢，保持角色音色不变。"),
]


def main() -> None:
    if not SOURCE_DIR.exists():
        raise SystemExit(f"CosyVoice 源码不存在: {SOURCE_DIR}")
    if not MODEL_DIR.exists():
        raise SystemExit(f"模型目录不存在: {MODEL_DIR}")
    if not REF_WAV.exists():
        raise SystemExit(f"参考音不存在，请先运行 voice_design_poc.py: {REF_WAV}")

    sys.path.insert(0, str(SOURCE_DIR))
    sys.path.insert(0, str(SOURCE_DIR / "third_party" / "Matcha-TTS"))

    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    model = AutoModel(model_dir=str(MODEL_DIR))
    print(f"[INFO] 模型加载耗时 {time.perf_counter() - started:.2f}s")
    print(f"[INFO] 文本前端: {model.frontend.text_frontend or '无（ttsfrd/wetext 均未装配）'}")

    records = []
    for name, instruction in EMOTIONS:
        t0 = time.perf_counter()
        set_all_random_seed(42)
        # CosyVoice3 instruct2 支持自然语言指令 + 参考音，适合验证跨模型音色迁移。
        outputs = model.inference_instruct2(
            TEXT,
            f"You are a helpful assistant. {instruction}<|endofprompt|>",
            str(REF_WAV),
            stream=False,
        )
        # inference_instruct2 返回生成器，且可能按句产出多个 chunk，必须遍历并拼接。
        chunks = [item["tts_speech"] for item in outputs]
        if not chunks:
            raise RuntimeError(f"{name}: 推理未返回任何音频")
        wav = torch.cat(chunks, dim=-1) if len(chunks) > 1 else chunks[0]
        sr = model.sample_rate
        path = OUT_DIR / f"{name}.wav"
        sf.write(str(path), wav.squeeze(0).float().cpu().numpy(), sr)
        elapsed = time.perf_counter() - t0
        duration_ms = int(wav.shape[-1] / sr * 1000)
        print(f"[OK] {name} -> {path.name}, {duration_ms}ms, {elapsed:.2f}s, chunks={len(chunks)}")
        records.append({
            "emotion": name,
            "instruction": instruction,
            "file": path.name,
            "api": "inference_instruct2",
            "reference_wav": str(REF_WAV),
            "reference_text": REF_TEXT,
            "text_frontend": model.frontend.text_frontend,
            "seed": 42,
            "sample_rate": sr,
            "duration_ms": duration_ms,
            "generation_seconds": round(elapsed, 3),
        })

    (OUT_DIR / "emotions.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共生成 {len(records)} 个情绪样本，目录: {OUT_DIR}")
    print("[HUMAN GATE] 请人工试听：三种情绪是否可辨，且仍像同一个角色。")


if __name__ == "__main__":
    main()
