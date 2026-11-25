"""阶段 3C：生成包含用户点名音节的 Qwen 情感源。

文本把用户反馈里的三个例子合并到一段：
  “我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。”
覆盖“久 / 今 / 三”，并额外包含 q/z/c/s/sh/in 类音节。

只生成 Qwen 源，VC 与后处理在 cosyvoice_worker 的 stage3c_vc_ab.py 中完成。

用法（仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/stage3c_qwen_sources.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

DEFAULT_CACHE = Path(
    os.getenv(
        "NOVELVOICE_MODEL_CACHE",
        str(Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"),
    )
)
DEFAULT_MODEL_DIR = DEFAULT_CACHE / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_OUT_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3c" / "qwen_sources"

TEXT = "我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。"

INSTRUCTIONS = {
    "neutral": (
        "成年男性，中音偏明亮，温和清晰，咬字干净，气声少，齿音轻；"
        "语速正常，像日常说话，句尾干净，不要叹气。"
    ),
    "sad": (
        "成年男性，中音偏明亮，温和清晰，咬字干净，气声少，齿音轻；"
        "此刻在压抑地悲伤，声音发紧，像强忍着眼泪把话说完；"
        "语速正常，句尾下沉，不要拖长尾音，不要叹气。"
    ),
}

SEEDS = [42, 123]
GENERATION_KWARGS = {
    "do_sample": True,
    "top_k": 50,
    "top_p": 1.0,
    "temperature": 0.9,
    "repetition_penalty": 1.05,
    "max_new_tokens": 2048,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 3C Qwen 情感源")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seeds", default="42,123")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model_dir.exists():
        raise SystemExit(f"模型目录不存在: {args.model_dir}")
    seeds = [int(item) for item in args.seeds.split(",") if item.strip()]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    from qwen_tts import Qwen3TTSModel

    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model_dir),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    load_seconds = time.perf_counter() - started
    print(f"[INFO] 模型加载耗时 {load_seconds:.2f}s")

    records = []
    for emotion, instruct in INSTRUCTIONS.items():
        for seed in seeds:
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            case_id = f"narrator_{emotion}_qwen_s{seed}"
            t0 = time.perf_counter()
            wavs, sample_rate = model.generate_voice_design(
                text=TEXT,
                language="Chinese",
                instruct=instruct,
                non_streaming_mode=True,
                **GENERATION_KWARGS,
            )
            wav = np.asarray(wavs[0], dtype=np.float32)
            path = args.output_dir / f"{case_id}.wav"
            sf.write(str(path), wav, sample_rate, subtype="PCM_16")
            elapsed = time.perf_counter() - t0
            duration = wav.size / sample_rate
            record = {
                "id": case_id,
                "file": str(path),
                "emotion": emotion,
                "text": TEXT,
                "instruction": instruct,
                "seed": seed,
                "generation_kwargs": GENERATION_KWARGS,
                "sample_rate": sample_rate,
                "duration_ms": int(round(duration * 1000)),
                "chars_per_second": round(len(TEXT) / duration, 2),
                "generation_seconds": round(elapsed, 3),
                "rtf": round(elapsed / duration, 3),
                "sha256": sha256_file(path),
                "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
                "model_dir": str(args.model_dir),
                "load_seconds": round(load_seconds, 3),
            }
            records.append(record)
            print(
                f"[OK] {case_id} {record['duration_ms']}ms "
                f"cps={record['chars_per_second']} rtf={record['rtf']}"
            )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3C：用户点名音节（久/今/三）的 Qwen 情感源",
        "text": TEXT,
        "instructions": INSTRUCTIONS,
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "model_dir": str(args.model_dir),
        "records": records,
    }
    (args.output_dir / "sources.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {args.output_dir / 'sources.json'}")


if __name__ == "__main__":
    main()