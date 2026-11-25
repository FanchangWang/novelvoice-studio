"""阶段 3B：单段 RTF 稳态基准。

CosyVoice 的 RTF 会受 GPU 冷启动、时钟和调度影响。本脚本先做预热，再对同一
参考音、同一指令、同一文本连续生成多次，记录每次耗时、音频时长和 RTF 分布。
不把单次 RTF 当作结论。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3b_rtf_bench.py --ref full --fp16
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3b_rtf_bench.py --ref short --fp16 --no-cache
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from stage3_ab import (
    DEFAULT_MODEL_DIR,
    DEFAULT_SOURCE_DIR,
    build_instruct_text,
    sha256_file,
)

STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)
TEXT = "我等你很久了。你答应过今天一定回来，可现在天都黑了。"
INSTRUCTION = "用平静、克制的语气说这句话，语速适中，保持角色音色不变。"
REFERENCES = {
    "full": ("refs_clean/narrator_03.wav", "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。远处有人慢慢走过，脚步声很轻。"),
    "short": ("refs_short/narrator_03_s1.wav", "雨停以后，街上的灯一盏一盏亮起来。"),
    "old_b08": ("refs_clean/new_old_male_b08.wav", "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。"),
    "old_b08_short": ("refs_short/new_old_male_b08_s1.wav", "雨停以后，街上的灯一盏一盏亮起来。"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 3B 单段 RTF 稳态基准")
    parser.add_argument("--ref", choices=sorted(REFERENCES), default="full")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.model_dir.exists():
        raise SystemExit(f"模型目录不存在: {args.model_dir}")
    ref_file, ref_text = REFERENCES[args.ref]
    ref_path = (STAGE3B_DIR / ref_file).resolve()
    if not ref_path.exists():
        raise SystemExit(f"参考音不存在: {ref_path}")

    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed
    import torch

    load_started = time.perf_counter()
    model = AutoModel(model_dir=str(args.model_dir), fp16=args.fp16)
    load_seconds = time.perf_counter() - load_started
    print(f"[INFO] load={load_seconds:.2f}s fp16={args.fp16} cache={not args.no_cache} ref={args.ref}")

    cache_id = ""
    cache_seconds = 0.0
    if not args.no_cache:
        prompt_text = build_instruct_text(INSTRUCTION)
        cache_id = f"rtf_{args.ref}"
        cache_started = time.perf_counter()
        model.add_zero_shot_spk(prompt_text, str(ref_path), cache_id)
        cache_seconds = time.perf_counter() - cache_started
        print(f"[CACHE] {cache_id} {cache_seconds:.3f}s")

    def generate() -> tuple[np.ndarray, float, float]:
        set_all_random_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = time.perf_counter()
        outputs = model.inference_instruct2(
            TEXT,
            build_instruct_text(INSTRUCTION),
            "" if cache_id else str(ref_path),
            zero_shot_spk_id=cache_id,
            stream=False,
            speed=1.0,
            text_frontend=True,
        )
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        merged = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        return merged, elapsed, merged.size / model.sample_rate

    for index in range(args.warmup):
        _, elapsed, duration = generate()
        print(f"[WARMUP] {index + 1}: {elapsed:.3f}s / {duration:.3f}s = {elapsed / duration:.3f}")

    runs = []
    last_audio = None
    for index in range(args.runs):
        audio, elapsed, duration = generate()
        last_audio = audio
        rtf = elapsed / max(duration, 1e-9)
        runs.append(
            {
                "index": index + 1,
                "generation_seconds": round(elapsed, 4),
                "duration_seconds": round(duration, 4),
                "rtf": round(rtf, 4),
            }
        )
        print(f"[RUN] {index + 1}: {elapsed:.3f}s / {duration:.3f}s = {rtf:.3f}")

    rtfs = [item["rtf"] for item in runs]
    timings = [item["generation_seconds"] for item in runs]
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ref": args.ref,
        "reference_wav": str(ref_path),
        "reference_text": ref_text,
        "fp16": args.fp16,
        "cache_enabled": not args.no_cache,
        "cache_seconds": round(cache_seconds, 4),
        "load_seconds": round(load_seconds, 4),
        "warmup": args.warmup,
        "runs": runs,
        "rtf": {
            "min": round(min(rtfs), 4),
            "median": round(statistics.median(rtfs), 4),
            "mean": round(statistics.mean(rtfs), 4),
            "max": round(max(rtfs), 4),
        },
        "generation_seconds": {
            "min": round(min(timings), 4),
            "median": round(statistics.median(timings), 4),
            "mean": round(statistics.mean(timings), 4),
            "max": round(max(timings), 4),
        },
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "sample_rate": model.sample_rate,
        "sha256_last": None,
    }
    if last_audio is not None:
        out_dir = STAGE3B_DIR / "experiments"
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{args.ref}_{'fp16' if args.fp16 else 'fp32'}_{'cache' if not args.no_cache else 'nocache'}"
        wav_path = out_dir / f"rtf_bench_{tag}.wav"
        sf.write(str(wav_path), last_audio, model.sample_rate, subtype="PCM_16")
        summary["output_wav"] = str(wav_path)
        summary["sha256_last"] = sha256_file(wav_path)
        report_path = out_dir / f"rtf_bench_{tag}.json"
    else:
        report_path = STAGE3B_DIR / "experiments" / "rtf_bench_empty.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] median RTF={summary['rtf']['median']} min={summary['rtf']['min']} max={summary['rtf']['max']}")
    print(f"[DONE] report: {report_path}")


if __name__ == "__main__":
    main()