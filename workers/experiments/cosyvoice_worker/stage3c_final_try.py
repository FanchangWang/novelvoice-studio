"""阶段 3C 最后一轮：针对“菜 / 三”像 ci 的小范围尝试。

只做 6 条新生成：
  - 同一 Qwen 源换 seed 123
  - 更强的参考音去齿（-8dB）
  - 更强参考 + 输出温和去齿
  - 擦音尾部收短
  - 悲伤句 seed 123 / 更强参考

不覆盖已有 3C 试听包；输出到 stage3c/final_try。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from stage3_ab import DEFAULT_MODEL_DIR, DEFAULT_SOURCE_DIR, audio_stats, sha256_file

# stage3c_deess.py 仍是生产依赖，留在 cosyvoice_worker/。
RUNTIME_WORKER_DIR = Path(__file__).resolve().parents[2] / "cosyvoice_worker"
if str(RUNTIME_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_WORKER_DIR))
from stage3c_deess import deess, read_mono, sibilant_shorten, spectral_stats

STAGE3C = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3c"
QWEN_DIR = STAGE3C / "qwen_sources"
OUT = STAGE3C / "final_try"
REF = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b" / "refs_clean" / "narrator_03.wav"
TEXT = "我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {
        item["id"]: Path(item["file"])
        for item in json.loads((QWEN_DIR / "sources.json").read_text(encoding="utf-8"))["records"]
    }
    raw_ref, ref_sr = read_mono(REF)
    ref_g1 = deess(raw_ref, ref_sr, threshold_ratio=0.24, full_ratio=0.62, max_reduction_db=5.0)[0]
    ref_g2 = deess(raw_ref, ref_sr, threshold_ratio=0.20, full_ratio=0.58, max_reduction_db=8.0)[0]
    ref_g1_path = OUT / "narrator_03_ref_g1.wav"
    ref_g2_path = OUT / "narrator_03_ref_g2.wav"
    sf.write(str(ref_g1_path), ref_g1, ref_sr, subtype="PCM_16")
    sf.write(str(ref_g2_path), ref_g2, ref_sr, subtype="PCM_16")

    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    started = time.perf_counter()
    model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
    print(f"[INFO] 模型加载耗时 {time.perf_counter() - started:.2f}s")
    sr = model.sample_rate
    records = []

    def run(case_id: str, source_id: str, ref_path: Path, output_post: str = "raw") -> Path:
        set_all_random_seed(42)
        t0 = time.perf_counter()
        outputs = model.inference_vc(str(sources[source_id]), str(ref_path), stream=False)
        audio = np.concatenate(
            [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        )
        elapsed = time.perf_counter() - t0
        case_dir = OUT / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        raw_path = case_dir / f"{case_id}_raw.wav"
        sf.write(str(raw_path), audio, sr, subtype="PCM_16")

        variants = {"raw": raw_path}
        if output_post in {"g0", "g2out"}:
            name = "deess_g0" if output_post == "g0" else "deess_g0"
            processed = deess(audio, sr, threshold_ratio=0.30, full_ratio=0.72, max_reduction_db=3.0)[0]
            path = case_dir / f"{case_id}_{name}.wav"
            sf.write(str(path), processed, sr, subtype="PCM_16")
            variants[name] = path
        if output_post == "shorten":
            processed, info = sibilant_shorten(audio, sr)
            path = case_dir / f"{case_id}_shorten.wav"
            sf.write(str(path), processed, sr, subtype="PCM_16")
            variants["shorten"] = path
            records.append({"id": case_id, "variant": "shorten_info", **info})

        for variant, path in variants.items():
            records.append(
                {
                    "id": case_id,
                    "variant": variant,
                    "file": str(path),
                    "source_id": source_id,
                    "reference": str(ref_path),
                    "text": TEXT,
                    "generation_seconds": round(elapsed, 3),
                    "rtf": round(elapsed / max(audio.size / sr, 1e-9), 3),
                    "sha256": sha256_file(path),
                    "stats": spectral_stats(path),
                    **audio_stats(path),
                }
            )
            print(f"[OK] {case_id}/{variant} {audio_stats(path)['duration_ms']}ms")
        return raw_path

    run("neutral_s123_refg1", "narrator_neutral_qwen_s123", ref_g1_path)
    run("neutral_s42_refg2", "narrator_neutral_qwen_s42", ref_g2_path)
    run("neutral_s42_refg2_outg0", "narrator_neutral_qwen_s42", ref_g2_path, output_post="g2out")
    run("neutral_s42_refg1_shorten", "narrator_neutral_qwen_s42", ref_g1_path, output_post="shorten")
    run("sad_s123_refg1", "narrator_sad_qwen_s123", ref_g1_path)
    run("sad_s42_refg2", "narrator_sad_qwen_s42", ref_g2_path)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3C 最后一轮：菜/三 像 ci 的小范围尝试",
        "text": TEXT,
        "reference_g1": str(ref_g1_path),
        "reference_g2": str(ref_g2_path),
        "records": records,
    }
    (OUT / "records.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {OUT / 'records.json'}")


if __name__ == "__main__":
    main()