"""阶段 3B：重新设计老年男性参考音，重点修正“刻意拖慢”。

阶段 3 的 old_male_01/02/03 参考音都带有明显慢速，用户选择 old_male_03 后仍反馈
“老年声音就是故意拖慢”。本脚本只生成老年男性候选，使用更短的中性参考文本和
明确的“正常语速、不拖腔”描述，输出到独立目录，不覆盖阶段 3 候选。

用法（仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/voice_design_stage3b.py
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
DEFAULT_OUT_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3b" / "voices_old_male"

# 两句话、32 字，比阶段 3 的三句更短，便于控制老年男声的参考音时长。
REFERENCE_TEXT = "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。"

VARIANTS = [
    {
        "label": "自然语速-清瘦",
        "instruct": (
            "六十岁左右的男性，声音清瘦偏干，语速正常自然，像日常说话；"
            "咬字清楚，句尾干净，不要拖长音，不要叹气。"
        ),
    },
    {
        "label": "自然语速-中低音",
        "instruct": (
            "老年男性，约六十五岁，中低音，语速自然，不刻意放慢；"
            "平静叙述，句子之间不要长停顿，尾音收住。"
        ),
    },
    {
        "label": "沙哑但利落",
        "instruct": (
            "老年男性，声音沙哑但精神，正常语速，说话利落；"
            "不要拖腔，不要气声，句尾干净。"
        ),
    },
    {
        "label": "清瘦略快",
        "instruct": (
            "老年男性，清瘦温和，语速略快于普通老人但不急促；"
            "像在灯下平静说话，句子之间不要长停顿，句尾利落。"
        ),
    },
    {
        "label": "对照-阶段3清瘦克制",
        "instruct": (
            "老年男性，声音清瘦偏干，语速适中偏慢，情绪克制；"
            "像在灯下平静说话，句尾利落。"
        ),
    },
]

SEEDS = [42, 2026]
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


def silence_edges(samples: np.ndarray, sample_rate: int, threshold_db: float = -50.0) -> tuple[int, int]:
    if samples.size == 0:
        return 0, 0
    threshold = 10 ** (threshold_db / 20.0)
    non_silent = np.flatnonzero(np.abs(samples) > threshold)
    if non_silent.size == 0:
        return int(samples.size / sample_rate * 1000), 0
    return (
        int(non_silent[0] / sample_rate * 1000),
        int((samples.size - 1 - non_silent[-1]) / sample_rate * 1000),
    )


def audio_stats(path: Path) -> dict:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
    lead_ms, tail_ms = silence_edges(mono, sample_rate)
    clipping = float(np.mean(np.abs(mono) >= 0.999)) if mono.size else 0.0
    duration_s = mono.size / sample_rate if sample_rate else 0.0
    return {
        "sample_rate": sample_rate,
        "channels": int(samples.shape[1]),
        "duration_ms": int(round(duration_s * 1000)),
        "chars_per_second": round(len(REFERENCE_TEXT) / duration_s, 2) if duration_s else None,
        "peak": round(peak, 4),
        "rms_dbfs": round(20 * np.log10(max(rms, 1e-9)), 2),
        "dc_offset": round(float(np.mean(mono)), 6),
        "clipping_ratio": round(clipping, 6),
        "leading_silence_ms": lead_ms,
        "trailing_silence_ms": tail_ms,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成阶段 3B 老年男性参考音候选")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--variants", default="", help="可选，逗号分隔的 label；默认全部")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model_dir.exists():
        raise SystemExit(f"模型目录不存在: {args.model_dir}")
    selected_labels = {item.strip() for item in args.variants.split(",") if item.strip()}
    variants = [item for item in VARIANTS if not selected_labels or item["label"] in selected_labels]
    if not variants:
        raise SystemExit(f"没有匹配的 variants: {args.variants}")

    from qwen_tts import Qwen3TTSModel

    args.output_dir.mkdir(parents=True, exist_ok=True)
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
    index = 0
    for variant in variants:
        for seed in SEEDS:
            index += 1
            torch.manual_seed(seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed)
            t0 = time.perf_counter()
            wavs, sample_rate = model.generate_voice_design(
                text=REFERENCE_TEXT,
                language="Chinese",
                instruct=variant["instruct"],
                non_streaming_mode=True,
                **GENERATION_KWARGS,
            )
            wav = np.asarray(wavs[0], dtype=np.float32)
            filename = f"old_male_b{index:02d}.wav"
            path = args.output_dir / filename
            sf.write(str(path), wav, sample_rate)
            elapsed = time.perf_counter() - t0
            stats = audio_stats(path)
            record = {
                "file": filename,
                "character_key": "old_male",
                "character_name": "老年男性",
                "variant": variant["label"],
                "instruct": variant["instruct"],
                "reference_text": REFERENCE_TEXT,
                "seed": seed,
                "generation_kwargs": GENERATION_KWARGS,
                "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
                "model_dir": str(args.model_dir),
                "load_seconds": round(load_seconds, 3),
                "generation_seconds": round(elapsed, 3),
                "sha256": sha256_file(path),
                **stats,
            }
            records.append(record)
            print(
                f"[OK] {filename} {variant['label']} {stats['duration_ms']}ms "
                f"cps={stats['chars_per_second']} peak={stats['peak']} "
                f"tail_silence={stats['trailing_silence_ms']}ms seed={seed}"
            )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3B 老年男性参考音候选；重点修正刻意拖慢，不覆盖阶段 3",
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "model_dir": str(args.model_dir),
        "reference_text": REFERENCE_TEXT,
        "generation_kwargs": GENERATION_KWARGS,
        "records": records,
    }
    (args.output_dir / "candidates.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共生成 {len(records)} 个候选，目录: {args.output_dir}")
    print("[HUMAN GATE] 请试听并选择语速自然、句尾干净的一条。")


if __name__ == "__main__":
    main()