"""阶段 3C：Qwen 情感源 + CosyVoice VC 的句中毛刺 A/B。

对同一段包含“久/今/三”的文本，比较：
  - Qwen neutral / sad 源，不同 seed
  - VC 到 narrator_03，原始参考 vs 去齿音参考
  - VC speed 0.9 / 1.0 / 1.1
  - CosyVoice instruct2 / zero-shot 原生生成
每个输出同时生成 deess_g0 / deess_g1 / deess_extreme 三个后处理版本，便于试听。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3c_vc_ab.py
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

from stage3_ab import DEFAULT_MODEL_DIR, DEFAULT_SOURCE_DIR, audio_stats, build_prompt_text, sha256_file
from stage3c_deess import deess, read_mono, spectral_stats

STAGE3C = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3c"
QWEN_DIR = STAGE3C / "qwen_sources"
REF_DIR = STAGE3C / "refs"
OUT = STAGE3C / "vc_ab"
NARRATOR_REF = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b" / "refs_clean" / "narrator_03.wav"
REF_TEXT = "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。远处有人慢慢走过，脚步声很轻。"
TEXT = "我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。"
NEUTRAL_INSTRUCT = "用平静、克制的语气说这句话，语速适中，保持角色音色不变；气声少，齿音轻。"

POST_VARIANTS = {
    "raw": None,
    "deess_g0": dict(threshold_ratio=0.30, full_ratio=0.72, max_reduction_db=3.0),
    "deess_g1": dict(threshold_ratio=0.24, full_ratio=0.62, max_reduction_db=5.0),
    "deess_extreme": dict(threshold_ratio=0.40, full_ratio=0.85, max_reduction_db=8.0),
}


def cid(path: Path, text: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(path).encode())
    digest.update(b"\0")
    digest.update(text.encode())
    return "q_" + digest.hexdigest()[:16]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return round(float(np.dot(a, b) / denominator), 4) if denominator > 1e-12 else 0.0


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REF_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 生成去齿音参考音；只做一次。
    raw_ref, ref_sr = read_mono(NARRATOR_REF)
    deessed_ref = deess(raw_ref, ref_sr, threshold_ratio=0.24, full_ratio=0.62, max_reduction_db=5.0)[0]
    ref_deess_path = REF_DIR / "narrator_03_deess.wav"
    sf.write(str(ref_deess_path), deessed_ref, ref_sr, subtype="PCM_16")

    # 2) 准备 Qwen 源清单。
    source_manifest = json.loads((QWEN_DIR / "sources.json").read_text(encoding="utf-8"))
    sources = {item["id"]: Path(item["file"]) for item in source_manifest["records"]}

    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    t0 = time.perf_counter()
    model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
    print(f"[INFO] 模型加载耗时 {time.perf_counter() - t0:.2f}s")
    sr = model.sample_rate

    def embed(path: Path) -> np.ndarray:
        value = model.frontend._extract_spk_embedding(str(path)).detach().cpu().numpy().reshape(-1)
        return value / np.linalg.norm(value)

    ref_emb = embed(NARRATOR_REF)
    zero_cache = cid(NARRATOR_REF, build_prompt_text(REF_TEXT))
    model.add_zero_shot_spk(build_prompt_text(REF_TEXT), str(NARRATOR_REF), zero_cache)

    records = []

    def save_variants(case_id: str, audio: np.ndarray, meta: dict) -> None:
        case_dir = OUT / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        for variant, params in POST_VARIANTS.items():
            audio_out = audio if params is None else deess(audio, sr, **params)[0]
            path = case_dir / f"{case_id}_{variant}.wav"
            sf.write(str(path), audio_out, sr, subtype="PCM_16")
            emb = embed(path)
            record = {
                **meta,
                "variant": variant,
                "file": str(path),
                "sha256": sha256_file(path),
                "speaker_similarity_to_reference": cosine(emb, ref_emb),
                "stats": spectral_stats(path),
                **audio_stats(path),
            }
            records.append(record)
            print(
                f"[OK] {case_id}/{variant} {record['duration_ms']}ms "
                f"sim={record['speaker_similarity_to_reference']} "
                f"hf95={record['stats']['high_ratio_p95']}"
            )

    def run_vc(case_id: str, source_id: str, ref_path: Path, speed: float = 1.0) -> None:
        source_path = sources[source_id]
        set_all_random_seed(42)
        t1 = time.perf_counter()
        outputs = model.inference_vc(
            str(source_path), str(ref_path), stream=False, speed=speed
        )
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        if not chunks:
            raise RuntimeError(f"{case_id}: VC 未返回音频")
        audio = np.concatenate(chunks)
        elapsed = time.perf_counter() - t1
        meta = {
            "id": case_id,
            "mode": "vc",
            "source_id": source_id,
            "source_file": str(source_path),
            "reference_file": str(ref_path),
            "speed": speed,
            "text": TEXT,
            "generation_seconds": round(elapsed, 3),
            "rtf": round(elapsed / max(audio.size / sr, 1e-9), 3),
        }
        save_variants(case_id, audio, meta)

    def run_instruct(case_id: str) -> None:
        set_all_random_seed(42)
        t1 = time.perf_counter()
        outputs = model.inference_instruct2(
            TEXT,
            f"You are a helpful assistant. {NEUTRAL_INSTRUCT}<|endofprompt|>",
            str(NARRATOR_REF),
            stream=False,
            text_frontend=True,
        )
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        audio = np.concatenate(chunks)
        elapsed = time.perf_counter() - t1
        meta = {
            "id": case_id,
            "mode": "instruct2",
            "source_id": "",
            "source_file": "",
            "reference_file": str(NARRATOR_REF),
            "speed": 1.0,
            "text": TEXT,
            "instruction": NEUTRAL_INSTRUCT,
            "generation_seconds": round(elapsed, 3),
            "rtf": round(elapsed / max(audio.size / sr, 1e-9), 3),
        }
        save_variants(case_id, audio, meta)

    def run_zero(case_id: str) -> None:
        set_all_random_seed(42)
        t1 = time.perf_counter()
        outputs = model.inference_zero_shot(
            TEXT,
            build_prompt_text(REF_TEXT),
            "",
            zero_shot_spk_id=zero_cache,
            stream=False,
            text_frontend=True,
        )
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        audio = np.concatenate(chunks)
        elapsed = time.perf_counter() - t1
        meta = {
            "id": case_id,
            "mode": "zero_shot",
            "source_id": "",
            "source_file": "",
            "reference_file": str(NARRATOR_REF),
            "speed": 1.0,
            "text": TEXT,
            "generation_seconds": round(elapsed, 3),
            "rtf": round(elapsed / max(audio.size / sr, 1e-9), 3),
        }
        save_variants(case_id, audio, meta)

    # 主对比：neutral / sad，两个 seed，原始参考。
    run_vc("vc_neutral_s42", "narrator_neutral_qwen_s42", NARRATOR_REF)
    run_vc("vc_neutral_s123", "narrator_neutral_qwen_s123", NARRATOR_REF)
    run_vc("vc_sad_s42", "narrator_sad_qwen_s42", NARRATOR_REF)
    run_vc("vc_sad_s123", "narrator_sad_qwen_s123", NARRATOR_REF)
    # 参考音去齿音 + VC。
    run_vc("vc_neutral_s42_refdeess", "narrator_neutral_qwen_s42", ref_deess_path)
    run_vc("vc_sad_s42_refdeess", "narrator_sad_qwen_s42", ref_deess_path)
    # VC 速度。
    run_vc("vc_neutral_s42_speed090", "narrator_neutral_qwen_s42", NARRATOR_REF, speed=0.9)
    run_vc("vc_neutral_s42_speed110", "narrator_neutral_qwen_s42", NARRATOR_REF, speed=1.1)
    # 原生对照。
    run_instruct("instruct_neutral")
    run_zero("zero_neutral")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3C：句中气声/齿音 A/B（久/今/三）",
        "text": TEXT,
        "reference": str(NARRATOR_REF),
        "reference_deess": str(ref_deess_path),
        "records": records,
    }
    (OUT / "records.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {OUT / 'records.json'}，共 {len(records)} 个变体")


if __name__ == "__main__":
    main()