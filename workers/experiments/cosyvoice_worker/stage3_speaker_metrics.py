"""阶段 3 辅助客观指标：说话人嵌入余弦相似度。

注意：这只是辅助指标，不能替代人工试听。它用于观察：
  - 同一角色 neutral/angry/sad 的嵌入是否仍接近；
  - 生成音频与参考音的嵌入距离；
  - 不同角色之间是否保持区分度。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3_speaker_metrics.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DEFAULT_CACHE = (
    Path.home()
    / "Documents"
    / "NovelVoiceStudio"
    / "cache"
    / "modelscope"
)
DEFAULT_MODEL_DIR = DEFAULT_CACHE / "Fun-CosyVoice3-0.5B-2512"
DEFAULT_STAGE3_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3"
SOURCE_DIR = Path(__file__).resolve().parents[2] / "cosyvoice_worker" / "CosyVoice"


def cosine(a: np.ndarray, b: np.ndarray) -> float | None:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return None
    value = float(np.dot(a, b) / denominator)
    if not math.isfinite(value):
        return None
    return round(value, 4)


def load_records(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("records", [])


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 3 说话人嵌入相似度")
    parser.add_argument("--stage3-dir", type=Path, default=DEFAULT_STAGE3_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    args = parser.parse_args()

    base = args.stage3_dir.resolve()
    records = load_records(base / "experiments" / "records.json")
    if not records:
        raise SystemExit("没有实验记录")
    if not SOURCE_DIR.exists():
        raise SystemExit(f"CosyVoice 源码不存在: {SOURCE_DIR}")

    sys.path.insert(0, str(SOURCE_DIR))
    sys.path.insert(0, str(SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel

    model = AutoModel(model_dir=str(args.model_dir))
    print(f"[INFO] 文本前端: {model.frontend.text_frontend}")

    def embedding(path: Path) -> np.ndarray:
        with np.errstate(all="ignore"):
            value = model.frontend._extract_spk_embedding(str(path))
        array = value.detach().cpu().numpy().reshape(-1).astype(np.float64)
        norm = np.linalg.norm(array)
        return array / norm if norm > 1e-12 else array

    embeddings: dict[str, np.ndarray] = {}
    for record in records:
        clean_file = record.get("clean_file")
        paths = []
        if clean_file:
            paths.append(base / clean_file)
        paths.append(base / "experiments" / f"{record['id']}.wav")
        path = next((item for item in paths if item.exists()), None)
        if path is None:
            continue
        try:
            embeddings[record["id"]] = embedding(path)
        except Exception as exc:  # noqa: BLE001 - 记录后继续，避免单个坏样本中断
            print(f"[WARN] {record['id']} 嵌入失败: {exc}")

    core = [item for item in records if item.get("group") == "core" and item["id"] in embeddings]
    by_character: dict[str, dict[str, str]] = {}
    for item in core:
        by_character.setdefault(item["character"], {})[item["emotion"]] = item["id"]

    core_similarity = {}
    for character, emotions in by_character.items():
        pairs = {}
        names = sorted(emotions)
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                pairs[f"{left}-{right}"] = cosine(
                    embeddings[emotions[left]], embeddings[emotions[right]]
                )
        core_similarity[character] = pairs

    reference_similarity = {}
    for item in records:
        case_id = item["id"]
        if case_id not in embeddings:
            continue
        ref_path = item.get("reference_wav")
        if not ref_path:
            continue
        key = f"ref::{Path(ref_path).resolve()}"
        if key not in embeddings:
            path = Path(ref_path)
            if not path.exists():
                continue
            try:
                embeddings[key] = embedding(path)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] 参考音嵌入失败 {path}: {exc}")
                continue
        reference_similarity[case_id] = cosine(embeddings[case_id], embeddings[key])

    cross_character = {}
    character_keys = sorted(by_character)
    for i, left in enumerate(character_keys):
        for right in character_keys[i + 1:]:
            values = []
            for left_id in by_character[left].values():
                for right_id in by_character[right].values():
                    if left_id in embeddings and right_id in embeddings:
                        values.append(cosine(embeddings[left_id], embeddings[right_id]))
            cross_character[f"{left}-{right}"] = round(
                sum(values) / len(values), 4
            ) if values else None

    output = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "说话人嵌入余弦相似度仅为辅助指标，不替代人工试听。",
        "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "core_cross_emotion_similarity": core_similarity,
        "cross_character_similarity": cross_character,
        "reference_similarity": reference_similarity,
    }
    output_path = base / "experiments" / "speaker_metrics.json"
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"[DONE] {output_path}")


if __name__ == "__main__":
    main()