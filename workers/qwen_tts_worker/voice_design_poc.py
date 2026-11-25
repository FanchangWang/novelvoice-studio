"""阶段 0 POC：用 Qwen3-TTS VoiceDesign 生成 3 个不同角色参考音色。

前置条件：
  1. 本目录的 .venv 已安装 qwen-tts、soundfile、torch（CUDA 版）
  2. 模型已通过 scripts/download_models.ps1 下载到 ModelScope 缓存目录

用法（在仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/voice_design_poc.py

输出：
  %USERPROFILE%\\Documents\\NovelVoiceStudio\\poc_output\\voices\\
    - candidate_01.wav / candidate_02.wav / candidate_03.wav
    - candidates.json（记录 instruct 与 reference_text）
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import soundfile as sf
import torch

CACHE = Path(os.getenv(
    "NOVELVOICE_MODEL_CACHE",
    str(Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"),
))
MODEL_DIR = CACHE / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
OUT_DIR = CACHE.parent / "poc_output" / "voices"

REFERENCE_TEXT = "你竟然敢骗我？我对你那么信任，你居然骗我！"

CANDIDATES = [
    {
        "name": "中年男性-低沉沉稳",
        "instruct": "成年男性，中低音，沉稳清晰，略带疲惫感，江湖人物，说话不紧不慢。",
    },
    {
        "name": "年轻女性-温柔清亮",
        "instruct": "年轻女性，女中音偏清亮，温柔但有主见，语速适中，情绪克制。",
    },
    {
        "name": "老年男性-沙哑缓慢",
        "instruct": "老年男性，声音沙哑低沉，语速缓慢，带阅历感，像隐居的老者。",
    },
]


def main() -> None:
    if not MODEL_DIR.exists():
        raise SystemExit(f"模型目录不存在: {MODEL_DIR}")

    from qwen_tts import Qwen3TTSModel

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(MODEL_DIR),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    print(f"[INFO] 模型加载耗时 {time.perf_counter() - started:.2f}s")

    records = []
    for i, cand in enumerate(CANDIDATES, start=1):
        t0 = time.perf_counter()
        wavs, sr = model.generate_voice_design(
            text=REFERENCE_TEXT,
            language="Chinese",
            instruct=cand["instruct"],
        )
        wav = wavs[0]
        path = OUT_DIR / f"candidate_{i:02d}.wav"
        sf.write(str(path), wav, sr)
        duration_ms = int(len(wav) / sr * 1000)
        elapsed = time.perf_counter() - t0
        print(f"[OK] {cand['name']} -> {path.name}, {duration_ms}ms, {elapsed:.2f}s")
        records.append({
            "file": path.name,
            "name": cand["name"],
            "instruct": cand["instruct"],
            "reference_text": REFERENCE_TEXT,
            "sample_rate": sr,
            "duration_ms": duration_ms,
            "generation_seconds": round(elapsed, 3),
        })

    (OUT_DIR / "candidates.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共生成 {len(records)} 个候选，目录: {OUT_DIR}")
    print("[HUMAN GATE] 请人工试听确认 3 个音色是否明显不同。")


if __name__ == "__main__":
    main()
