"""阶段 3B 参考音准备：复制用户选定的阶段 3 候选，并清洗新的老年男性候选。

不覆盖阶段 3 的 refs_clean。输出到 stage3b/refs_clean。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_prepare_refs.py
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from stage3_ab import audio_stats, read_audio, sha256_file, trim_and_process

DEFAULT_STAGE3_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3"
)
DEFAULT_STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 3B 参考音准备")
    parser.add_argument("--stage3-dir", type=Path, default=DEFAULT_STAGE3_DIR)
    parser.add_argument("--stage3b-dir", type=Path, default=DEFAULT_STAGE3B_DIR)
    parser.add_argument("--target-rms-dbfs", type=float, default=-23.0)
    parser.add_argument("--peak-limit", type=float, default=0.95)
    parser.add_argument(
        "--old-male-files",
        default="old_male_b07.wav,old_male_b08.wav",
        help="新的老年男性候选文件名，逗号分隔；默认 b07/b08（客观语速最快）",
    )
    args = parser.parse_args()

    stage3 = args.stage3_dir.resolve()
    out_dir = args.stage3b_dir.resolve() / "refs_clean"
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []

    # 用户选定的阶段 3 候选已经做过最小处理，直接复制，避免二次处理改变音色。
    for name in ["narrator_03.wav", "young_female_01.wav", "old_male_03.wav"]:
        source = stage3 / "refs_clean" / name
        if not source.exists():
            raise SystemExit(f"阶段 3 参考音不存在: {source}")
        target = out_dir / name
        shutil.copy2(source, target)
        record = {
            "file": name,
            "source_file": str(source),
            "action": "copied_from_stage3",
            "sha256": sha256_file(target),
            **audio_stats(target),
        }
        records.append(record)
        print(f"[COPY] {name} {record['duration_ms']}ms rms={record['rms_dbfs']}dBFS")

    # 新的老年男性候选需要去首尾静音、短淡入淡出、RMS 归一。
    old_male_dir = args.stage3b_dir.resolve() / "voices_old_male"
    for name in [item.strip() for item in args.old_male_files.split(",") if item.strip()]:
        source = old_male_dir / name
        if not source.exists():
            raise SystemExit(f"新的老年男性候选不存在: {source}")
        samples, sample_rate = read_audio(source)
        processed, params = trim_and_process(
            samples,
            sample_rate,
            target_rms_dbfs=args.target_rms_dbfs,
            peak_limit=args.peak_limit,
        )
        target_name = f"new_{name}"
        target = out_dir / target_name
        sf.write(str(target), processed, sample_rate, subtype="PCM_16")
        record = {
            "file": target_name,
            "source_file": str(source),
            "action": "trim_normalize",
            "target_rms_dbfs": args.target_rms_dbfs,
            "peak_limit": args.peak_limit,
            "postprocess": params,
            "sha256": sha256_file(target),
            **audio_stats(target),
        }
        records.append(record)
        print(
            f"[OK] {target_name} {record['duration_ms']}ms "
            f"peak={record['peak']} rms={record['rms_dbfs']}dBFS"
        )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_stage3": str(stage3),
        "target_rms_dbfs": args.target_rms_dbfs,
        "peak_limit": args.peak_limit,
        "records": records,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共准备 {len(records)} 条参考音，目录: {out_dir}")


if __name__ == "__main__":
    main()