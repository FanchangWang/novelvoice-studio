"""阶段 3B：用 Qwen VoiceDesign 生成“有情绪的音源”，供 CosyVoice3 VC 换回角色音色。

目的：CosyVoice3 instruct2 的悲伤表现力弱，而 Qwen VoiceDesign 对语气描述更敏感。
     先用 Qwen 生成带悲伤/愤怒语气的中性音源，再用 CosyVoice3 `inference_vc`
     把音色换回用户选定的参考音，从而同时获得情绪与音色一致性。

用法（仓库根目录）：
  workers/qwen_tts_worker/.venv/Scripts/python.exe workers/experiments/qwen_tts_worker/stage3b_emotion_sources.py \
      --character narrator --emotion sad --seeds 42,123
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

DEFAULT_CACHE = Path(
    os.getenv(
        "NOVELVOICE_MODEL_CACHE",
        str(Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"),
    )
)
DEFAULT_MODEL_DIR = DEFAULT_CACHE / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
DEFAULT_OUT_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3b" / "qwen_sources"

TEXTS = {
    "sad": "我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。",
    "angry": "你答应过的事，为什么总是做不到？你让我怎么相信你？",
    "neutral": "夜里的风从窗缝里钻进来，屋里很安静。",
}

CHARACTERS = {
    "narrator": {
        "base": "成年男性，中音偏明亮，温和清晰，咬字干净；",
        "sad": "此刻在压抑地悲伤，声音发紧，像强忍着眼泪把话说完；语速正常，句尾下沉，不要拖长尾音，不要气声。",
        "angry": "此刻压抑着怒意，声音压低，咬字更重；不要喊叫，不要提高音调，句尾收住。",
    },
    "young_female": {
        "base": "年轻女性，清亮温柔，咬字清楚；",
        "sad": "此刻很难过，声音放低，像强忍着眼泪；语速正常，句尾收住，不要哭腔，不要气声。",
        "angry": "此刻压着怒气，语气变硬，句子短促；不要喊叫，不要失控，句尾收住。",
    },
    "old_male": {
        "base": "老年男性，清瘦温和，语速略快于普通老人但不急促；",
        "sad": "此刻疲惫而难过，声音发沉，句尾下沉；语速正常，句尾利落，不要拖长音，不要叹气。",
        "angry": "此刻压着怒气，声音发沉，咬字更重；不要喊叫，不要拖长音，句尾利落。",
    },
}

GENERATION_KWARGS = {
    "do_sample": True,
    "top_k": 50,
    "top_p": 1.0,
    "temperature": 0.9,
    "repetition_penalty": 1.05,
    "max_new_tokens": 2048,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Qwen 情绪音源")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--character", default="narrator", choices=sorted(CHARACTERS))
    parser.add_argument("--emotion", default="sad", choices=sorted(TEXTS))
    parser.add_argument("--seeds", default="42,123")
    args = parser.parse_args()

    if not args.model_dir.exists():
        raise SystemExit(f"模型目录不存在: {args.model_dir}")
    seeds = [int(item) for item in args.seeds.split(",") if item.strip()]
    spec = CHARACTERS[args.character]
    instruct = spec["base"] + spec.get(args.emotion, "")
    text = TEXTS[args.emotion]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    from qwen_tts import Qwen3TTSModel

    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(args.model_dir),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    print(f"[INFO] load {time.perf_counter()-started:.2f}s")

    records = []
    for seed in seeds:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        t0 = time.perf_counter()
        wavs, sample_rate = model.generate_voice_design(
            text=text,
            language="Chinese",
            instruct=instruct,
            non_streaming_mode=True,
            **GENERATION_KWARGS,
        )
        elapsed = time.perf_counter() - t0
        wav = np.asarray(wavs[0], dtype=np.float32).squeeze()
        filename = f"{args.character}_{args.emotion}_qwen_s{seed}.wav"
        path = args.output_dir / filename
        sf.write(str(path), wav, sample_rate)
        duration = wav.size / sample_rate
        records.append(
            {
                "file": filename,
                "character": args.character,
                "emotion": args.emotion,
                "seed": seed,
                "instruct": instruct,
                "text": text,
                "duration_ms": int(round(duration * 1000)),
                "chars_per_second": round(len(text) / duration, 2),
                "generation_seconds": round(elapsed, 3),
                "sample_rate": sample_rate,
                "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
            }
        )
        print(f"[OK] {filename} {duration:.2f}s {elapsed:.2f}s")

    manifest = args.output_dir / f"{args.character}_{args.emotion}_sources.json"
    manifest.write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[DONE] {manifest}")


if __name__ == "__main__":
    main()