"""阶段 3 音质 spike：生成中性参考音候选。

与阶段 0 的区别：
  - 参考文本改为中性叙述，避免愤怒语气污染音色锚点；
  - 每个角色生成 3 个候选，便于人工挑选更干净的一条；
  - 记录随机种子、生成参数与客观音频指标；
  - 输出到独立目录，不覆盖阶段 0 样本。

用法（仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/voice_design_stage3.py
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
DEFAULT_OUT_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3" / "voices"

# 中性、无强烈情绪、句尾收束清楚的参考文本。
REFERENCE_TEXT = (
    "雨停以后，街上的灯一盏一盏亮起来。"
    "风从巷口吹过，带着潮湿的凉意。"
    "远处有人慢慢走过，脚步声很轻。"
)

CHARACTERS = [
    {
        "key": "narrator",
        "name": "旁白-中年男性",
        "variants": [
            {
                "label": "沉稳平缓",
                "instruct": (
                    "成年男性，中低音，沉稳清晰，语速平缓，情绪克制，"
                    "像纪录片旁白；发声干净，句尾自然收束。"
                ),
            },
            {
                "label": "低沉厚实",
                "instruct": (
                    "成年男性，低音厚实，语速稍慢，平静叙述；"
                    "气息稳定，句尾不要拖长或漏气。"
                ),
            },
            {
                "label": "明亮温和",
                "instruct": (
                    "成年男性，中音偏明亮，温和清晰，语速适中；"
                    "咬字干净，句尾收得利落。"
                ),
            },
        ],
    },
    {
        "key": "young_female",
        "name": "年轻女性",
        "variants": [
            {
                "label": "清亮温柔",
                "instruct": (
                    "年轻女性，清亮温柔，语速适中，情绪平静；"
                    "咬字清楚，句尾自然收束，不带气声。"
                ),
            },
            {
                "label": "偏低沉静",
                "instruct": (
                    "年轻女性，女中音偏低，沉静克制，语速稍慢；"
                    "声音干净，尾音稳定。"
                ),
            },
            {
                "label": "轻盈干净",
                "instruct": (
                    "年轻女性，声音轻盈但不虚，清晰自然，语速适中；"
                    "像平静地讲一件旧事，句尾不要漏气。"
                ),
            },
        ],
    },
    {
        "key": "old_male",
        "name": "老年男性",
        "variants": [
            {
                "label": "沙哑缓慢",
                "instruct": (
                    "老年男性，声音沙哑低沉，语速缓慢，平静叙述；"
                    "气息稳定，句尾收束干净。"
                ),
            },
            {
                "label": "低缓阅历感",
                "instruct": (
                    "老年男性，低音，语速慢，带阅历感但不悲情；"
                    "咬字清楚，尾音不要拖出气声。"
                ),
            },
            {
                "label": "清瘦克制",
                "instruct": (
                    "老年男性，声音清瘦偏干，语速适中偏慢，情绪克制；"
                    "像在灯下平静说话，句尾利落。"
                ),
            },
        ],
    },
]

SEEDS = [42, 123, 2026]
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
    amplitude = np.abs(samples)
    threshold = 10 ** (threshold_db / 20.0)
    non_silent = np.flatnonzero(amplitude > threshold)
    if non_silent.size == 0:
        return int(samples.size / sample_rate * 1000), 0
    lead_ms = int(non_silent[0] / sample_rate * 1000)
    tail_ms = int((samples.size - 1 - non_silent[-1]) / sample_rate * 1000)
    return lead_ms, tail_ms


def audio_stats(path: Path) -> dict:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
    lead_ms, tail_ms = silence_edges(mono, sample_rate)
    clipping = float(np.mean(np.abs(mono) >= 0.999)) if mono.size else 0.0
    return {
        "sample_rate": sample_rate,
        "channels": int(samples.shape[1]),
        "duration_ms": int(round(mono.size / sample_rate * 1000)),
        "peak": round(peak, 4),
        "rms_dbfs": round(20 * np.log10(max(rms, 1e-9)), 2),
        "dc_offset": round(float(np.mean(mono)), 6),
        "clipping_ratio": round(clipping, 6),
        "leading_silence_ms": lead_ms,
        "trailing_silence_ms": tail_ms,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成阶段 3 中性参考音候选")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--characters",
        default="narrator,young_female,old_male",
        help="逗号分隔的角色 key，默认全部",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model_dir.exists():
        raise SystemExit(f"模型目录不存在: {args.model_dir}")
    selected = {item.strip() for item in args.characters.split(",") if item.strip()}
    characters = [item for item in CHARACTERS if item["key"] in selected]
    if not characters:
        raise SystemExit(f"没有匹配的角色: {args.characters}")

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
    for character in characters:
        for index, variant in enumerate(character["variants"]):
            seed = SEEDS[index % len(SEEDS)]
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
            filename = f"{character['key']}_{index + 1:02d}.wav"
            path = args.output_dir / filename
            sf.write(str(path), wav, sample_rate)
            elapsed = time.perf_counter() - t0
            stats = audio_stats(path)
            record = {
                "file": filename,
                "character_key": character["key"],
                "character_name": character["name"],
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
                f"[OK] {filename} {character['name']}/{variant['label']} "
                f"{stats['duration_ms']}ms peak={stats['peak']} "
                f"tail_silence={stats['trailing_silence_ms']}ms "
                f"{elapsed:.2f}s seed={seed}"
            )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3 中性参考音候选；不覆盖阶段 0 样本",
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "model_dir": str(args.model_dir),
        "reference_text": REFERENCE_TEXT,
        "generation_kwargs": GENERATION_KWARGS,
        "records": records,
    }
    manifest_path = args.output_dir / "candidates.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共生成 {len(records)} 个候选，目录: {args.output_dir}")
    print("[HUMAN GATE] 请人工试听并按角色挑选最干净的一条。")


if __name__ == "__main__":
    main()