"""阶段 3B 试听包辅助指标：说话人嵌入余弦相似度。

只服务于 build_stage3b_audition.py，不替代人工试听。计算：
  - 每条候选与目标参考音的相似度；
  - 同一角色跨情绪（neutral vs angry/sad）的相似度；
  - 阶段 3 基线与 Qwen 源 + CosyVoice VC 的对照。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_audition_metrics.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DEFAULT_CACHE = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "modelscope"
DEFAULT_MODEL_DIR = DEFAULT_CACHE / "Fun-CosyVoice3-0.5B-2512"
DEFAULT_STAGE3B_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3b"
SOURCE_DIR = Path(__file__).resolve().parents[2] / "cosyvoice_worker" / "CosyVoice"


def cosine(a: np.ndarray, b: np.ndarray) -> float | None:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return None
    value = float(np.dot(a, b) / denominator)
    return round(value, 4) if math.isfinite(value) else None


def candidate_files(base: Path) -> dict[str, Path]:
    refs = base / "refs_clean"
    fp32 = base / "fp32_core"
    vc = base / "vc_test" / "clean"
    pilot = base / "anchor_pilot"
    stage3 = base / "experiments"
    return {
        # 参考音
        "ref:narrator_03": refs / "narrator_03.wav",
        "ref:young_female_01": refs / "young_female_01.wav",
        "ref:old_male_03": refs / "old_male_03.wav",
        "ref:old_male_b08": refs / "new_old_male_b08.wav",
        # 阶段 3 原始选择（旧指令，用于基线）
        "stage3:narrator_neutral": stage3 / "sel_narrator_neutral.wav",
        "stage3:narrator_angry": stage3 / "sel_narrator_angry.wav",
        "stage3:narrator_sad": stage3 / "sel_narrator_sad.wav",
        "stage3:young_female_neutral": stage3 / "sel_young_female_neutral.wav",
        "stage3:young_female_angry": stage3 / "sel_young_female_angry.wav",
        "stage3:young_female_sad": stage3 / "sel_young_female_sad.wav",
        "stage3:old_male_neutral": stage3 / "sel_old_male_neutral.wav",
        "stage3:old_male_angry": stage3 / "sel_old_male_angry.wav",
        "stage3:old_male_sad": stage3 / "sel_old_male_sad.wav",
        # fp32 重跑，排除半精度影响
        "fp32:narrator_neutral": fp32 / "narrator_neutral_clean.wav",
        "fp32:narrator_angry": fp32 / "narrator_angry_clean.wav",
        "fp32:narrator_sad": fp32 / "narrator_sad_clean.wav",
        "fp32:young_female_neutral": fp32 / "young_female_neutral_clean.wav",
        "fp32:young_female_angry": fp32 / "young_female_angry_clean.wav",
        "fp32:young_female_sad": fp32 / "young_female_sad_clean.wav",
        "fp32:old_male_b08_neutral": fp32 / "old_male_b08_neutral_clean.wav",
        "fp32:old_male_b08_angry": fp32 / "old_male_b08_angry_clean.wav",
        "fp32:old_male_b08_sad": fp32 / "old_male_b08_sad_clean.wav",
        # Qwen 情感源 + CosyVoice VC
        "vc:narrator_sad": vc / "vc_narrator_sad_qwen_s42.wav",
        "vc:narrator_sad_natural": vc / "vc_narrator_sad_natural_qwen_s42.wav",
        "vc:narrator_sad_tired": vc / "vc_narrator_sad_tired_qwen_s42.wav",
        "vc:narrator_angry": vc / "vc_narrator_angry_qwen_s42.wav",
        "vc:narrator_angry_cold": vc / "vc_narrator_angry_cold_qwen_s42.wav",
        "vc:young_female_sad": vc / "vc_young_female_sad_qwen_s42.wav",
        "vc:young_female_angry": vc / "vc_young_female_angry_qwen_s42.wav",
        "vc:old_male_sad": vc / "vc_old_male_sad_qwen_s42.wav",
        "vc:old_male_angry": vc / "vc_old_male_angry_qwen_s42.wav",
        # 新指令 / 零样本锚点 pilot
        "pilot:neutral_instruct": pilot / "neutral_instruct_clean.wav",
        "pilot:sad_instruct_c": pilot / "sad_instruct_c_clean.wav",
        "pilot:sad_speed110": pilot / "sad_instruct_c_speed110_clean.wav",
        "pilot:sad_zero_anchor": pilot / "sad_zero_anchor_clean.wav",
        "pilot:angry_instruct_b": pilot / "angry_instruct_b_clean.wav",
        # 试听包中还需要直接展示的新指令 / 尾音原文件
        "exp:young_female_sad_c": stage3 / "emo_young_female_sad_c.wav",
        "exp:young_female_angry_c": stage3 / "emo_young_female_angry_c.wav",
        "exp:old_b08_neutral": stage3 / "old_b08_neutral.wav",
        "exp:old_b08_sad": stage3 / "old_b08_sad.wav",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 3B 试听包说话人相似度")
    parser.add_argument("--stage3b-dir", type=Path, default=DEFAULT_STAGE3B_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    args = parser.parse_args()

    base = args.stage3b_dir.resolve()
    files = candidate_files(base)
    missing = [name for name, path in files.items() if not path.exists()]
    if missing:
        raise SystemExit(f"缺少音频: {', '.join(missing)}")
    if not SOURCE_DIR.exists():
        raise SystemExit(f"CosyVoice 源码不存在: {SOURCE_DIR}")

    sys.path.insert(0, str(SOURCE_DIR))
    sys.path.insert(0, str(SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel

    model = AutoModel(model_dir=str(args.model_dir))

    embeddings: dict[str, np.ndarray] = {}
    for name, path in files.items():
        value = model.frontend._extract_spk_embedding(str(path))
        array = value.detach().cpu().numpy().reshape(-1).astype(np.float64)
        norm = np.linalg.norm(array)
        embeddings[name] = array / norm if norm > 1e-12 else array
        print(f"[OK] {name}")

    def sim(left: str, right: str) -> float | None:
        return cosine(embeddings[left], embeddings[right])

    to_ref = {}
    for name in files:
        if name.startswith("ref:"):
            continue
        if name.startswith("pilot:") or "narrator" in name:
            ref = "ref:narrator_03"
        elif "young" in name:
            ref = "ref:young_female_01"
        elif name.startswith("stage3:"):
            ref = "ref:old_male_03"
        else:
            ref = "ref:old_male_b08"
        to_ref[name] = sim(name, ref)

    cross_emotion = {
        "narrator": {
            "neutral": "fp32:narrator_neutral",
            "stage3_vs_neutral": {
                "angry": sim("fp32:narrator_neutral", "fp32:narrator_angry"),
                "sad": sim("fp32:narrator_neutral", "fp32:narrator_sad"),
            },
            "qwen_vc_vs_neutral": {
                "angry": sim("fp32:narrator_neutral", "vc:narrator_angry"),
                "sad": sim("fp32:narrator_neutral", "vc:narrator_sad"),
            },
        },
        "young_female": {
            "neutral": "fp32:young_female_neutral",
            "stage3_vs_neutral": {
                "angry": sim("fp32:young_female_neutral", "fp32:young_female_angry"),
                "sad": sim("fp32:young_female_neutral", "fp32:young_female_sad"),
            },
            "qwen_vc_vs_neutral": {
                "angry": sim("fp32:young_female_neutral", "vc:young_female_angry"),
                "sad": sim("fp32:young_female_neutral", "vc:young_female_sad"),
            },
        },
        "old_male_03": {
            "neutral": "stage3:old_male_neutral",
            "stage3_vs_neutral": {
                "angry": sim("stage3:old_male_neutral", "stage3:old_male_angry"),
                "sad": sim("stage3:old_male_neutral", "stage3:old_male_sad"),
            },
            "qwen_vc_vs_neutral": {"angry": None, "sad": None},
        },
        "old_male_b08": {
            "neutral": "fp32:old_male_b08_neutral",
            "stage3_vs_neutral": {
                "angry": sim("fp32:old_male_b08_neutral", "fp32:old_male_b08_angry"),
                "sad": sim("fp32:old_male_b08_neutral", "fp32:old_male_b08_sad"),
            },
            "qwen_vc_vs_neutral": {
                "angry": sim("fp32:old_male_b08_neutral", "vc:old_male_angry"),
                "sad": sim("fp32:old_male_b08_neutral", "vc:old_male_sad"),
            },
        },
    }

    output = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "说话人嵌入余弦相似度仅为辅助指标，不替代人工试听；Qwen+VC 为实验路径。",
        "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "to_reference": to_ref,
        "cross_emotion": cross_emotion,
    }
    path = base / "audition_metrics.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"[DONE] {path}")


if __name__ == "__main__":
    main()