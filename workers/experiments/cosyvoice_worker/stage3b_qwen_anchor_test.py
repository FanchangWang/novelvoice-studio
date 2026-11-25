"""阶段 3B：验证“Qwen 情绪音源 + CosyVoice VC”作为情绪锚点是否可迁移到新台词。

流程：
  Qwen VoiceDesign 生成带情绪的句子 -> CosyVoice3 VC 换成角色音色 -> 作为 zero-shot 锚点
  -> 对新台词做 zero-shot，检查语速、音色相似度与句尾指标。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_qwen_anchor_test.py
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

from stage3_ab import (
    DEFAULT_MODEL_DIR,
    DEFAULT_SOURCE_DIR,
    audio_stats,
    build_prompt_text,
    sha256_file,
)
from stage3b_tail import trim_tail

STAGE3B = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
OUT = STAGE3B / "qwen_vc_anchor_test"
REFS = STAGE3B / "refs_clean"
NEUTRAL_REF = REFS / "narrator_03.wav"
NEUTRAL_REF_TEXT = "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。远处有人慢慢走过，脚步声很轻。"
SAD_ANCHOR_QWEN = STAGE3B / "vc_test" / "vc_narrator_sad_qwen_s42.wav"
SAD_ANCHOR_TEXT = "我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。"
SAD_ANCHOR_COSY = STAGE3B / "experiments" / "emo_narrator_sad_c.wav"
CORE_TEXT = "我等你很久了。你答应过今天一定回来，可现在天都黑了。"
SAD_NEW = "你走以后，屋子空得让人害怕。我总以为门外会响起你的脚步声。"
ANGRY_NEW = "你每次都这样，说走就走。你有没有想过我会怎么想？"
NEUTRAL_NEW = "夜深了，窗外的风慢慢停下来。"
SAD_INSTRUCT = "平静地陈述一件让人难过的事，句子之间短暂停顿；声音略低，但语速正常，不要每个字都拖慢。"
ANGRY_INSTRUCT = "压低声音，克制地表达不满，咬字更重；不要喊叫，不要提高音调，保持原来的音色。"


def cid(path: Path, text: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(path).encode())
    digest.update(b"\0")
    digest.update(text.encode())
    return "q_" + digest.hexdigest()[:16]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    t0 = time.perf_counter()
    model = AutoModel(model_dir=str(DEFAULT_MODEL_DIR))
    print(f"[INFO] load {time.perf_counter()-t0:.2f}s")
    sr = model.sample_rate
    caches: dict[str, str] = {}

    def get_cache(path: Path, prompt_body: str) -> str:
        key = f"{path}|{prompt_body}"
        if key not in caches:
            built = build_prompt_text(prompt_body)
            caches[key] = cid(path, built)
            t1 = time.perf_counter()
            model.add_zero_shot_spk(built, str(path), caches[key])
            print(f"[CACHE] {path.name} {time.perf_counter()-t1:.2f}s")
        return caches[key]

    def embed(path: Path) -> np.ndarray:
        value = model.frontend._extract_spk_embedding(str(path)).detach().cpu().numpy().reshape(-1)
        return value / np.linalg.norm(value)

    ref_emb = embed(NEUTRAL_REF)
    records = []

    def run(case_id: str, mode: str, text: str, anchor: Path | None, prompt_body: str, instruction: str = "") -> None:
        set_all_random_seed(42)
        t1 = time.perf_counter()
        if mode == "zero":
            cache = get_cache(anchor, prompt_body)
            outputs = model.inference_zero_shot(
                text,
                build_prompt_text(prompt_body),
                "",
                zero_shot_spk_id=cache,
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
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        elapsed = time.perf_counter() - t1
        raw = np.concatenate(chunks)
        trimmed = []
        for index, chunk in enumerate(chunks):
            part, _, _ = trim_tail(chunk, sr)
            if index > 0:
                trimmed.append(np.zeros(int(sr * 0.18), dtype=np.float32))
            trimmed.append(part)
        clean = np.concatenate(trimmed)
        raw_path = OUT / f"{case_id}_raw.wav"
        clean_path = OUT / f"{case_id}_clean.wav"
        sf.write(str(raw_path), raw, sr, subtype="PCM_16")
        sf.write(str(clean_path), clean, sr, subtype="PCM_16")
        emb = embed(clean_path)
        duration = clean.size / sr
        record = {
            "id": case_id,
            "mode": mode,
            "text": text,
            "anchor": str(anchor) if anchor else "",
            "instruction": instruction,
            "generation_seconds": round(elapsed, 3),
            "generation_rtf": round(elapsed / (raw.size / sr), 3),
            "chunks": len(chunks),
            "raw_file": raw_path.name,
            "clean_file": clean_path.name,
            "raw_sha256": sha256_file(raw_path),
            "clean_sha256": sha256_file(clean_path),
            "chars_per_second": round(len(text) / duration, 2),
            "speaker_similarity_to_neutral_ref": round(float(np.dot(emb, ref_emb)), 4),
            "raw": audio_stats(raw_path),
            "clean": audio_stats(clean_path),
        }
        records.append(record)
        print(
            f"[OK] {case_id:<28} {duration:.2f}s {record['chars_per_second']}cps "
            f"sim={record['speaker_similarity_to_neutral_ref']} gen={elapsed:.2f}s "
            f"tail={record['clean']['tail_200ms_hf_ratio']}"
        )

    run("sad_zero_qwen_vc", "zero", SAD_NEW, SAD_ANCHOR_QWEN, SAD_ANCHOR_TEXT)
    run("sad_zero_cosy_c", "zero", SAD_NEW, SAD_ANCHOR_COSY, CORE_TEXT)
    run("sad_instruct_c", "instruct", SAD_NEW, None, "", SAD_INSTRUCT)
    run("angry_zero_qwen_vc", "zero", ANGRY_NEW, STAGE3B / "vc_test" / "vc_narrator_angry_qwen_s42.wav", "你答应过的事，为什么总是做不到？你让我怎么相信你？")
    run("angry_instruct_b", "instruct", ANGRY_NEW, None, "", ANGRY_INSTRUCT)
    run("neutral_zero_ref", "zero", NEUTRAL_NEW, NEUTRAL_REF, NEUTRAL_REF_TEXT)

    (OUT / "records.json").write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] {OUT / 'records.json'}")


if __name__ == "__main__":
    main()