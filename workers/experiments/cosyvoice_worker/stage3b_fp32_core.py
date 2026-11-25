"""阶段 3B：用 fp32 重跑选定角色的 neutral / angry / sad 核心样本。

阶段 3B 的 selected_core 是 fp16；本脚本给出同参考音、同文本、同指令的 fp32
对照，便于判断精度是否影响音质/句尾，并补齐 b08 的 angry。
输出到 stage3b/fp32_core，不覆盖已有实验。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_fp32_core.py
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
from stage3b_tail import trim_tail

STAGE3B = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
OUT = STAGE3B / "fp32_core"
TEXT = "我等你很久了。你答应过今天一定回来，可现在天都黑了。"
INSTRUCTIONS = {
    "neutral": "用平静、克制的语气说这句话，语速适中，保持角色音色不变。",
    "angry": "愤怒而压抑地质问，语速略快，情绪有力度，保持角色音色不变。",
    "sad": "悲伤、压抑地低声说，语速较慢，声音发紧，尾音自然收住，不要拖出气声。",
}
CHARACTERS = {
    "narrator": ("refs_clean/narrator_03.wav", "旁白-中年男性（narrator_03）"),
    "young_female": ("refs_clean/young_female_01.wav", "年轻女性（young_female_01）"),
    "old_male_b08": ("refs_clean/new_old_male_b08.wav", "老年男性（b08 清瘦略快）"),
}


def cache_id(path: Path, prompt_text: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(path).encode())
    digest.update(b"\0")
    digest.update(prompt_text.encode())
    return "f_" + digest.hexdigest()[:16]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    started = time.perf_counter()
    model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
    print(f"[INFO] load {time.perf_counter()-started:.2f}s fp16={getattr(model,'fp16',None)}")
    sr = model.sample_rate
    caches: dict[str, str] = {}
    ref_embs = {}

    def embed(path: Path) -> np.ndarray:
        value = model.frontend._extract_spk_embedding(str(path)).detach().cpu().numpy().reshape(-1)
        return value / np.linalg.norm(value)

    def get_cache(path: Path, prompt_text: str) -> str:
        key = f"{path}|{prompt_text}"
        if key not in caches:
            caches[key] = cache_id(path, prompt_text)
            t0 = time.perf_counter()
            model.add_zero_shot_spk(prompt_text, str(path), caches[key])
            print(f"[CACHE] {path.name} {time.perf_counter()-t0:.2f}s")
        return caches[key]

    records = []
    for char_key, (ref_rel, label) in CHARACTERS.items():
        ref = STAGE3B / ref_rel
        built = f"You are a helpful assistant. {INSTRUCTIONS['neutral']}<|endofprompt|>"
        prompt_text = f"You are a helpful assistant. {INSTRUCTIONS['neutral']}<|endofprompt|>"
        get_cache(ref, built)
        ref_embs[char_key] = embed(ref)
        for emotion, instruction in INSTRUCTIONS.items():
            set_all_random_seed(42)
            t0 = time.perf_counter()
            outputs = model.inference_instruct2(
                TEXT,
                f"You are a helpful assistant. {instruction}<|endofprompt|>",
                str(ref),
                stream=False,
                text_frontend=True,
            )
            chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
            elapsed = time.perf_counter() - t0
            raw = np.concatenate(chunks)
            cleaned_parts = []
            chunk_meta = []
            for index, chunk in enumerate(chunks):
                part, kept, removed = trim_tail(chunk, sr)
                if index > 0:
                    cleaned_parts.append(np.zeros(int(sr * 0.18), dtype=np.float32))
                cleaned_parts.append(part)
                chunk_meta.append({"samples": int(chunk.size), "kept": int(kept), "removed_ms": round(removed / sr * 1000, 1)})
            clean = np.concatenate(cleaned_parts)
            case_id = f"{char_key}_{emotion}"
            raw_path = OUT / f"{case_id}_raw.wav"
            clean_path = OUT / f"{case_id}_clean.wav"
            sf.write(str(raw_path), raw, sr, subtype="PCM_16")
            sf.write(str(clean_path), clean, sr, subtype="PCM_16")
            duration = clean.size / sr
            records.append(
                {
                    "id": case_id,
                    "character": char_key,
                    "character_label": label,
                    "emotion": emotion,
                    "text": TEXT,
                    "instruction": instruction,
                    "reference_wav": str(ref),
                    "fp16": bool(getattr(model, "fp16", False)),
                    "chunks": chunk_meta,
                    "generation_seconds": round(elapsed, 3),
                    "generation_rtf": round(elapsed / (raw.size / sr), 3),
                    "raw_file": raw_path.name,
                    "clean_file": clean_path.name,
                    "raw_sha256": sha256_file(raw_path),
                    "clean_sha256": sha256_file(clean_path),
                    "chars_per_second": round(len(TEXT) / duration, 2),
                    "speaker_similarity_to_ref": round(float(np.dot(embed(clean_path), ref_embs[char_key])), 4),
                    "raw": audio_stats(raw_path),
                    "clean": audio_stats(clean_path),
                }
            )
            print(
                f"[OK] {case_id:<24} {duration:.2f}s {records[-1]['chars_per_second']}cps "
                f"sim={records[-1]['speaker_similarity_to_ref']} tail={records[-1]['clean']['tail_200ms_hf_ratio']}"
            )

    (OUT / "records.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] {OUT / 'records.json'}")


if __name__ == "__main__":
    main()