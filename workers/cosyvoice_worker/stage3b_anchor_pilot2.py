"""阶段 3B 试点 2：分句生成 + 速度补偿，验证句尾气声与悲伤拖慢。

只跑旁白，新增：
  - sad 指令 + speed=1.10 / 1.15 对照；
  - sad 情绪锚点 zero-shot，按句生成，每句单独截尾；
  - 中性 zero-shot 按句生成对照。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3b_anchor_pilot2.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

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
CORE_TEXT = "我等你很久了。你答应过今天一定回来，可现在天都黑了。"
SAD_TEXT = "我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。"
NEUTRAL_TEXT = "夜里的风从窗缝里钻进来，屋里很安静。"
SAD_INSTRUCT = "平静地陈述一件让人难过的事，句子之间短暂停顿；声音略低，但语速正常，不要每个字都拖慢。"


def split_sentences(text: str) -> list[str]:
    parts = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    return [item.strip() for item in parts if item.strip()]


def cache_id(path: Path, prompt_text: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(path).encode())
    digest.update(b"\0")
    digest.update(prompt_text.encode())
    return "p2_" + digest.hexdigest()[:16]


def save_pair(records: list[dict], case_id: str, raw: np.ndarray, cleaned: np.ndarray, sr: int, meta: dict) -> None:
    raw_path = OUT / f"{case_id}_raw.wav"
    clean_path = OUT / f"{case_id}_clean.wav"
    sf.write(str(raw_path), raw, sr, subtype="PCM_16")
    sf.write(str(clean_path), cleaned, sr, subtype="PCM_16")
    records.append(
        {
            "id": case_id,
            "raw_file": raw_path.name,
            "clean_file": clean_path.name,
            "raw_sha256": sha256_file(raw_path),
            "clean_sha256": sha256_file(clean_path),
            "raw": audio_stats(raw_path),
            "clean": audio_stats(clean_path),
            **meta,
        }
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    started = time.perf_counter()
    model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
    print(f"[INFO] load {time.perf_counter()-started:.2f}s, frontend={model.frontend.text_frontend}")
    sr = model.sample_rate
    records: list[dict] = []

    # 缓存
    cid_neutral = cache_id(NEUTRAL_REF, build_prompt_text(NEUTRAL_REF_TEXT))
    model.add_zero_shot_spk(build_prompt_text(NEUTRAL_REF_TEXT), str(NEUTRAL_REF), cid_neutral)
    cid_sad = cache_id(SAD_ANCHOR, build_prompt_text(CORE_TEXT))
    model.add_zero_shot_spk(build_prompt_text(CORE_TEXT), str(SAD_ANCHOR), cid_sad)

    def run_instruct(case_id: str, text: str, instruction: str, speed: float) -> None:
        set_all_random_seed(42)
        t0 = time.perf_counter()
        outputs = model.inference_instruct2(
            text,
            f"You are a helpful assistant. {instruction}<|endofprompt|>",
            str(NEUTRAL_REF),
            stream=False,
            speed=speed,
            text_frontend=True,
        )
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        elapsed = time.perf_counter() - t0
        raw = np.concatenate(chunks)
        trimmed = []
        meta_chunks = []
        for chunk in chunks:
            item, kept, removed = trim_tail(chunk, sr)
            trimmed.append(item)
            meta_chunks.append({"samples": int(chunk.size), "kept": int(kept), "removed_ms": round(removed / sr * 1000, 1)})
        cleaned = np.concatenate(trimmed) if len(trimmed) == 1 else np.concatenate(
            [part for i, chunk in enumerate(trimmed) for part in ([chunk] if i == 0 else [np.zeros(int(sr * 0.18), dtype=np.float32), chunk])]
        )
        save_pair(
            records,
            case_id,
            raw,
            cleaned,
            sr,
            {
                "mode": "instruct2",
                "text": text,
                "instruction": instruction,
                "speed": speed,
                "chunks": meta_chunks,
                "generation_seconds": round(elapsed, 3),
                "generation_rtf": round(elapsed / (raw.size / sr), 3),
            },
        )
        print(f"[OK] {case_id:<28} {raw.size/sr:.2f}s -> {cleaned.size/sr:.2f}s gen={elapsed:.2f}s tail={audio_stats(OUT/f'{case_id}_clean.wav')['tail_200ms_hf_ratio']}")

    def run_zero_per_sentence(case_id: str, text: str, cid: str, prompt_body: str) -> None:
        sentences = split_sentences(text)
        set_all_random_seed(42)
        t0 = time.perf_counter()
        raw_parts = []
        clean_parts = []
        chunk_meta = []
        for index, sentence in enumerate(sentences):
            outputs = model.inference_zero_shot(
                sentence,
                build_prompt_text(prompt_body),
                "",
                zero_shot_spk_id=cid,
                stream=False,
                text_frontend=True,
            )
            chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
            raw_chunk = np.concatenate(chunks)
            trimmed, kept, removed = trim_tail(raw_chunk, sr)
            if index > 0:
                raw_parts.append(np.zeros(int(sr * 0.18), dtype=np.float32))
                clean_parts.append(np.zeros(int(sr * 0.18), dtype=np.float32))
            raw_parts.append(raw_chunk)
            clean_parts.append(trimmed)
            chunk_meta.append({"sentence": sentence, "samples": int(raw_chunk.size), "kept": int(kept), "removed_ms": round(removed / sr * 1000, 1)})
        elapsed = time.perf_counter() - t0
        raw = np.concatenate(raw_parts)
        cleaned = np.concatenate(clean_parts)
        save_pair(
            records,
            case_id,
            raw,
            cleaned,
            sr,
            {
                "mode": "zero_shot_per_sentence",
                "text": text,
                "sentences": sentences,
                "chunks": chunk_meta,
                "generation_seconds": round(elapsed, 3),
                "generation_rtf": round(elapsed / (raw.size / sr), 3),
            },
        )
        print(f"[OK] {case_id:<28} {raw.size/sr:.2f}s -> {cleaned.size/sr:.2f}s gen={elapsed:.2f}s tail={audio_stats(OUT/f'{case_id}_clean.wav')['tail_200ms_hf_ratio']}")

    run_instruct("sad_instruct_c_speed110", SAD_TEXT, SAD_INSTRUCT, 1.10)
    run_instruct("sad_instruct_c_speed115", SAD_TEXT, SAD_INSTRUCT, 1.15)
    run_zero_per_sentence("sad_zero_anchor_per_sentence", SAD_TEXT, cid_sad, CORE_TEXT)
    run_zero_per_sentence("neutral_zero_ref_per_sentence", NEUTRAL_TEXT, cid_neutral, NEUTRAL_REF_TEXT)

    (OUT / "pilot2.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] {OUT / 'pilot2.json'}")


if __name__ == "__main__":
    main()