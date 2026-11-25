"""阶段 3B：从用户选定参考音中截取单句短参考，测试 RTF 与克隆稳定性。

截取点来自人工检查的停顿边界：保留第一句语音 + 100ms 静音 + 30ms 淡出，
参考文本只写第一句，避免文本与音频不一致。输出到 stage3b/refs_short，不覆盖原参考。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3b_make_short_refs.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from stage3_ab import audio_stats, read_audio, sha256_file

STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)
FIRST_SENTENCE = "雨停以后，街上的灯一盏一盏亮起来。"

TASKS = [
    {
        "source": "narrator_03.wav",
        "output": "narrator_03_s1.wav",
        "cut_seconds": 3.14,
        "reference_text": FIRST_SENTENCE,
    },
    {
        "source": "young_female_01.wav",
        "output": "young_female_01_s1.wav",
        "cut_seconds": 3.48,
        "reference_text": FIRST_SENTENCE,
    },
    {
        "source": "new_old_male_b08.wav",
        "output": "new_old_male_b08_s1.wav",
        "cut_seconds": 4.34,
        "reference_text": FIRST_SENTENCE,
    },
]


def main() -> None:
    base = STAGE3B_DIR.resolve()
    out_dir = base / "refs_short"
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for task in TASKS:
        source_path = base / "refs_clean" / task["source"]
        samples, sample_rate = read_audio(source_path)
        end = min(samples.size, int((task["cut_seconds"] + 0.10) * sample_rate))
        out = samples[:end].copy()
        fade = min(int(sample_rate * 0.03), out.size)
        if fade:
            out[-fade:] *= np.linspace(1.0, 0.0, fade, endpoint=False)
        target_rms = 10 ** (-23.0 / 20.0)
        rms = float(np.sqrt(np.mean(np.square(out))))
        gain = target_rms / max(rms, 1e-9)
        peak_after = float(np.max(np.abs(out))) * gain
        if peak_after > 0.95:
            gain = 0.95 / max(float(np.max(np.abs(out))), 1e-9)
        out *= gain
        output_path = out_dir / task["output"]
        sf.write(str(output_path), out, sample_rate, subtype="PCM_16")
        record = {
            "file": task["output"],
            "source_file": str(source_path),
            "cut_seconds": task["cut_seconds"],
            "reference_text": task["reference_text"],
            "applied_gain": round(float(gain), 4),
            "sha256": sha256_file(output_path),
            **audio_stats(output_path),
        }
        records.append(record)
        print(
            f"[OK] {task['output']} {record['duration_ms']}ms "
            f"rms={record['rms_dbfs']}dBFS peak={record['peak']}"
        )

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3B 单句短参考；用于测试 RTF 与短参考克隆稳定性",
        "records": records,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 共生成 {len(records)} 条短参考，目录: {out_dir}")


if __name__ == "__main__":
    main()