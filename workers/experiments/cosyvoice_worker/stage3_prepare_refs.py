"""阶段 3 参考音最小处理：去首尾静音、短淡入淡出、统一 RMS。

输出到 stage3/refs_clean，不覆盖原始候选。TTS 实验优先使用处理后的参考音，
避免参考音电平差异影响 CosyVoice 的特征提取。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3_prepare_refs.py
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from stage3_ab import audio_stats, read_audio, sha256_file, trim_and_process

DEFAULT_STAGE3_DIR = (
    Path.home()
    / "Documents"
    / "NovelVoiceStudio"
    / "cache"
    / "poc_output"
    / "stage3"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 3 参考音最小处理")
    parser.add_argument("--stage3-dir", type=Path, default=DEFAULT_STAGE3_DIR)
    parser.add_argument("--target-rms-dbfs", type=float, default=-23.0)
    parser.add_argument("--peak-limit", type=float, default=0.95)
    args = parser.parse_args()

    base = args.stage3_dir.resolve()
    manifest_path = base / "voices" / "candidates.json"
    if not manifest_path.exists():
        raise SystemExit(f"候选清单不存在: {manifest_path}")
    candidates = json.loads(manifest_path.read_text(encoding="utf-8"))
    out_dir = base / "refs_clean"
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        (record["file"], base / "voices" / record["file"], record)
        for record in candidates["records"]
    ]
    legacy = base.parent / "voices" / "candidate_01.wav"
    if legacy.exists():
        tasks.append(("legacy_candidate_01.wav", legacy, {"file": "candidate_01.wav", "legacy": True}))

    records = []
    for output_name, source_path, source_record in tasks:
        samples, sample_rate = read_audio(source_path)
        processed, params = trim_and_process(
            samples,
            sample_rate,
            target_rms_dbfs=args.target_rms_dbfs,
            peak_limit=args.peak_limit,
        )
        output_path = out_dir / output_name
        sf.write(str(output_path), processed, sample_rate, subtype="PCM_16")
        record = {
            "file": output_name,
            "source_file": str(source_path),
            "source_record": source_record,
            "target_rms_dbfs": args.target_rms_dbfs,
            "peak_limit": args.peak_limit,
            "postprocess": params,
            "sha256": sha256_file(output_path),
            **audio_stats(output_path),
        }
        records.append(record)
        print(
            f"[OK] {output_name} {record['duration_ms']}ms "
            f"peak={record['peak']} rms={record['rms_dbfs']}dBFS"
        )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(manifest_path),
        "target_rms_dbfs": args.target_rms_dbfs,
        "peak_limit": args.peak_limit,
        "records": records,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共处理 {len(records)} 条参考音，目录: {out_dir}")


if __name__ == "__main__":
    main()