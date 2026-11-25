"""阶段 3B 试点：用“情绪锚点”零样本迁移悲伤/愤怒，并逐句截掉气声尾。

思路：
  1. 先用 instruct2 生成一条可控的情绪锚点（已有 3B 样本即可）；
  2. 把锚点当作 zero-shot 参考音，生成新台词；
  3. 每个分句 chunk 单独做句尾气声检测与淡出，再拼接。

试点只跑旁白，验证方向是否值得扩展。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_anchor_pilot.py
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
import torch

from stage3_ab import (
    DEFAULT_MODEL_DIR,
    DEFAULT_SOURCE_DIR,
    audio_stats,
    build_prompt_text,
    sha256_file,
)
from stage3b_tail import trim_tail

STAGE3B = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
OUT = STAGE3B / "anchor_pilot"
REFS = STAGE3B / "refs_clean"

NEUTRAL_REF = REFS / "narrator_03.wav"
NEUTRAL_REF_TEXT = "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。远处有人慢慢走过，脚步声很轻。"
SAD_ANCHOR = STAGE3B / "experiments" / "emo_narrator_sad_c.wav"
ANGRY_ANCHOR = STAGE3B / "experiments" / "sel_narrator_angry.wav"
CORE_TEXT = "我等你很久了。你答应过今天一定回来，可现在天都黑了。"

TEXTS = {
    "neutral": "夜里的风从窗缝里钻进来，屋里很安静。",
    "sad": "我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。",
    "angry": "你答应过的事，为什么总是做不到？你让我怎么相信你？",
}

CASES = [
    ("neutral_instruct", "neutral", "instruct2", None, "用平静、克制的语气说这句话，语速适中，保持角色音色不变。"),
    ("neutral_zero_ref", "neutral", "zero_shot", NEUTRAL_REF, NEUTRAL_REF_TEXT),
    ("sad_instruct_c", "sad", "instruct2", None, "平静地陈述一件让人难过的事，句子之间短暂停顿；声音略低，但语速正常，不要每个字都拖慢。"),
    ("sad_instruct_s0", "sad", "instruct2", None, "悲伤、压抑地低声说，语速较慢，保持角色音色不变。"),
    ("sad_zero_anchor", "sad", "zero_shot", SAD_ANCHOR, CORE_TEXT),
    ("angry_instruct_b", "angry", "instruct2", None, "压低声音，克制地表达不满，咬字更重；不要喊叫，不要提高音调，保持原来的音色。"),
    ("angry_zero_anchor", "angry", "zero_shot", ANGRY_ANCHOR, CORE_TEXT),
]


def cache_id(path: Path, prompt_text: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(path).encode())
    digest.update(b"\0")
    digest.update(prompt_text.encode())
    return "p_" + digest.hexdigest()[:16]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed
    from cosyvoice.cli.cosyvoice import CosyVoice3

    started = time.perf_counter()
    model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
    print(f"[INFO] load {time.perf_counter()-started:.2f}s, fp16={getattr(model,'fp16',None)}, frontend={model.frontend.text_frontend}")

    cache_ids: dict[str, str] = {}

    def get_cache(path: Path, prompt_text: str) -> str:
        key = f"{path}|{prompt_text}"
        if key not in cache_ids:
            cid = cache_id(path, prompt_text)
            t0 = time.perf_counter()
            model.add_zero_shot_spk(build_prompt_text(prompt_text), str(path), cid)
            print(f"[CACHE] {path.name} {time.perf_counter()-t0:.3f}s")
            cache_ids[key] = cid
        return cache_ids[key]

    records = []
    for case_id, emotion, mode, anchor, instruction in CASES:
        text = TEXTS[emotion]
        set_all_random_seed(42)
        t0 = time.perf_counter()
        if mode == "zero_shot":
            cid = get_cache(anchor, instruction)
            outputs = model.inference_zero_shot(
                text,
                build_prompt_text(instruction),
                "",
                zero_shot_spk_id=cid,
                stream=False,
                text_frontend=True,
            )
        else:
            outputs = model.inference_instruct2(
                text,
                f"You are a helpful assistant. {instruction}<|endofprompt|>",
                str(NEUTRAL_REF),
                stream=False,
                text_frontend=True,
            )
        chunks = []
        for item in outputs:
            chunks.append(item["tts_speech"].squeeze(0).float().cpu().numpy())
        elapsed = time.perf_counter() - t0
        raw = np.concatenate(chunks)
        sr = model.sample_rate

        # 每句单独截尾，并拼接 180ms 静音。
        cleaned_chunks = []
        chunk_meta = []
        for chunk in chunks:
            trimmed, kept, removed = trim_tail(chunk, sr)
            cleaned_chunks.append(trimmed)
            chunk_meta.append({"samples": int(chunk.size), "kept": int(kept), "removed_ms": round(removed / sr * 1000, 1)})
        pause = np.zeros(int(sr * 0.18), dtype=np.float32)
        joined = cleaned_chunks[0]
        for chunk in cleaned_chunks[1:]:
            joined = np.concatenate([joined, pause, chunk])

        raw_path = OUT / f"{case_id}_raw.wav"
        clean_path = OUT / f"{case_id}_clean.wav"
        sf.write(str(raw_path), raw, sr, subtype="PCM_16")
        sf.write(str(clean_path), joined, sr, subtype="PCM_16")
        raw_stats = audio_stats(raw_path)
        clean_stats = audio_stats(clean_path)
        record = {
            "id": case_id,
            "emotion": emotion,
            "mode": mode,
            "text": text,
            "instruction": instruction if mode == "instruct2" else "",
            "anchor": str(anchor) if anchor else "",
            "anchor_text": instruction if mode == "zero_shot" else "",
            "chunks": chunk_meta,
            "chunk_count": len(chunks),
            "generation_seconds": round(elapsed, 3),
            "generation_rtf": round(elapsed / (raw.size / sr), 3),
            "sample_rate": sr,
            "raw_file": raw_path.name,
            "clean_file": clean_path.name,
            "raw_sha256": sha256_file(raw_path),
            "clean_sha256": sha256_file(clean_path),
            "raw": raw_stats,
            "clean": clean_stats,
        }
        records.append(record)
        print(f"[OK] {case_id:<22} chunks={len(chunks)} raw={raw_stats['duration_ms']}ms clean={clean_stats['duration_ms']}ms gen={elapsed:.2f}s tail_hf={clean_stats['tail_200ms_hf_ratio']}")
        (OUT / "pilot.json").write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()