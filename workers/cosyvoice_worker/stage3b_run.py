"""阶段 3B：使用用户选定候选重跑核心情绪，并测试缓存、fp16、克制指令。

与阶段 3 的区别：
  - 参考音改为用户选择的 narrator_03 / young_female_01 / old_male_03；
  - 新增自然语速老年男性 b07/b08；
  - 支持 `add_zero_shot_spk` 缓存参考音特征，避免每条样本重复提取；
  - 支持 fp16 开关，记录缓存构建时间与纯生成 RTF；
  - 输出到 stage3b，不覆盖阶段 3。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3b_run.py --group selected_core
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3b_run.py --group all --fp16
"""

from __future__ import annotations

import argparse
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
    build_instruct_text,
    build_prompt_text,
    read_audio,
    sha256_file,
    trim_and_process,
)

DEFAULT_STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)
DEFAULT_CASES = Path(__file__).resolve().parent / "stage3b_cases.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CosyVoice3 阶段 3B 聚焦实验")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--stage3b-dir", type=Path, default=DEFAULT_STAGE3B_DIR)
    parser.add_argument("--group", default="all", help="all 或逗号分隔的 selected_core,old_new,emotion_v2")
    parser.add_argument("--only", default="", help="只跑指定 case id，逗号分隔")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的 wav")
    parser.add_argument("--fp16", action="store_true", help="使用 fp16 autocast（可能提升 RTF，需复听音质）")
    parser.add_argument("--no-cache", action="store_true", help="禁用 add_zero_shot_spk 缓存，作为对照")
    return parser.parse_args()


def build_cache_id(ref_path: Path, prompt_text: str) -> str:
    digest = hashlib.sha1()
    digest.update(str(ref_path).encode("utf-8"))
    digest.update(b"\0")
    digest.update(prompt_text.encode("utf-8"))
    return "b_" + digest.hexdigest()[:16]


def main() -> None:
    args = parse_args()
    if not args.model_dir.exists():
        raise SystemExit(f"模型目录不存在: {args.model_dir}")
    if not args.cases.exists():
        raise SystemExit(f"实验定义不存在: {args.cases}")
    if not DEFAULT_SOURCE_DIR.exists():
        raise SystemExit(f"CosyVoice 源码不存在: {DEFAULT_SOURCE_DIR}")

    spec = json.loads(args.cases.read_text(encoding="utf-8"))
    characters = spec["characters"]
    groups = {item.strip() for item in args.group.split(",") if item.strip()}
    only = {item.strip() for item in args.only.split(",") if item.strip()}
    selected = []
    for case in spec["cases"]:
        if only and case["id"] not in only:
            continue
        if not only and "all" not in groups and case.get("group") not in groups:
            continue
        selected.append(case)
    if not selected:
        raise SystemExit("没有匹配的实验 case")

    experiment_dir = args.stage3b_dir / "experiments"
    clean_dir = args.stage3b_dir / "experiments_clean"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "records.json"

    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    started = time.perf_counter()
    model = AutoModel(model_dir=str(args.model_dir), fp16=args.fp16)
    load_seconds = time.perf_counter() - started
    print(f"[INFO] 模型加载耗时 {load_seconds:.2f}s; fp16={args.fp16}; cache={not args.no_cache}")
    print(f"[INFO] 文本前端: {model.frontend.text_frontend or '无'}")

    records: list[dict] = []
    outputs_by_id: dict[str, Path] = {}
    cache_ids: dict[str, str] = {}
    cache_seconds_total: dict[str, float] = {}

    def write_manifest() -> None:
        existing: list[dict] = []
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8")).get("records", [])
            except (json.JSONDecodeError, OSError):
                existing = []
        by_id = {item["id"]: item for item in existing}
        for item in records:
            by_id[item["id"]] = item
        manifest = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "model_dir": str(args.model_dir),
            "text_frontend": getattr(model.frontend, "text_frontend", ""),
            "fp16": args.fp16,
            "cache_enabled": not args.no_cache,
            "cases_file": str(args.cases),
            "records": sorted(by_id.values(), key=lambda item: item["id"]),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for case in selected:
        case_id = case["id"]
        output_path = experiment_dir / f"{case_id}.wav"
        if output_path.exists() and not args.overwrite:
            print(f"[SKIP] {case_id} 已存在（使用 --overwrite 重跑）")
            outputs_by_id[case_id] = output_path
            continue

        character = characters.get(case["character"])
        if character is None:
            raise RuntimeError(f"{case_id}: 未知角色 {case['character']}")
        ref_path = (args.stage3b_dir / character["ref_file"]).resolve()
        if not ref_path.exists():
            raise RuntimeError(f"{case_id}: 参考音不存在 {ref_path}")

        mode = case.get("mode", "instruct2")
        seed = int(case.get("seed", 42))
        speed = float(case.get("speed", 1.0))
        instruction = case.get("instruction", "")
        prompt_text = (
            build_instruct_text(instruction)
            if mode == "instruct2"
            else build_prompt_text(case.get("prompt_text", character["reference_text"]))
        )
        cache_id = ""
        cache_hit = False
        cache_seconds = 0.0
        if not args.no_cache:
            cache_id = build_cache_id(ref_path, prompt_text)
            if cache_id not in cache_ids:
                cache_started = time.perf_counter()
                model.add_zero_shot_spk(prompt_text, str(ref_path), cache_id)
                cache_seconds = time.perf_counter() - cache_started
                cache_ids[cache_id] = cache_id
                cache_seconds_total[cache_id] = cache_seconds
                print(f"[CACHE] {cache_id} 构建 {cache_seconds:.3f}s")
            else:
                cache_hit = True

        set_all_random_seed(seed)
        t0 = time.perf_counter()
        if mode == "zero_shot":
            outputs = model.inference_zero_shot(
                case["text"],
                prompt_text,
                "" if cache_id else str(ref_path),
                zero_shot_spk_id=cache_id,
                stream=False,
                speed=speed,
                text_frontend=True,
            )
        else:
            outputs = model.inference_instruct2(
                case["text"],
                prompt_text,
                "" if cache_id else str(ref_path),
                zero_shot_spk_id=cache_id,
                stream=False,
                speed=speed,
                text_frontend=True,
            )
        chunks = [item["tts_speech"].squeeze(0).float().cpu().numpy() for item in outputs]
        elapsed = time.perf_counter() - t0
        if not chunks:
            raise RuntimeError(f"{case_id}: 推理未返回音频")
        merged = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        sf.write(str(output_path), merged, model.sample_rate, subtype="PCM_16")
        raw_stats = audio_stats(output_path)
        duration_s = merged.size / model.sample_rate

        clean_path = clean_dir / f"{case_id}.wav"
        clean_params = None
        clean_stats = None
        failure = None
        try:
            if raw_stats["duration_ms"] < 200 or raw_stats["peak"] <= 1e-4:
                raise ValueError("输出过短或全为静音")
            cleaned, clean_params = trim_and_process(merged, model.sample_rate)
            sf.write(str(clean_path), cleaned, model.sample_rate, subtype="PCM_16")
            clean_stats = audio_stats(clean_path)
        except ValueError as exc:
            failure = str(exc)
            print(f"[WARN] {case_id} 未生成 clean 版本: {failure}")

        record = {
            "id": case_id,
            "group": case.get("group", ""),
            "character": case["character"],
            "character_label": character["label"],
            "emotion": case.get("emotion", "neutral"),
            "mode": mode,
            "text": case["text"],
            "instruction": instruction,
            "speed": speed,
            "notes": case.get("notes", ""),
            "seed": seed,
            "reference_wav": str(ref_path),
            "reference_text": character["reference_text"],
            "text_frontend": model.frontend.text_frontend,
            "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "model_dir": str(args.model_dir),
            "fp16": args.fp16,
            "cache_enabled": not args.no_cache,
            "cache_id": cache_id,
            "cache_hit": cache_hit,
            "cache_seconds": round(cache_seconds, 3),
            "load_seconds": round(load_seconds, 3),
            "generation_seconds": round(elapsed, 3),
            "generation_rtf": round(elapsed / max(duration_s, 1e-9), 3),
            "rtf_with_cache_build": round((elapsed + cache_seconds) / max(duration_s, 1e-9), 3),
            "sha256": sha256_file(output_path),
            "status": "failed" if failure else "ok",
            "failure_reason": failure,
            "clean_file": f"experiments_clean/{case_id}.wav" if clean_stats else None,
            "clean_params": clean_params,
            "clean_stats": clean_stats,
            **raw_stats,
        }
        records.append(record)
        outputs_by_id[case_id] = output_path
        write_manifest()
        if failure:
            print(f"[FAIL] {case_id} {raw_stats['duration_ms']}ms: {failure}")
        else:
            print(
                f"[OK] {case_id} {record['duration_ms']}ms "
                f"gen={record['generation_seconds']}s rtf={record['generation_rtf']} "
                f"cache_hit={cache_hit} peak={record['peak']} "
                f"tail_hf={record['tail_200ms_hf_ratio']}"
            )

    write_manifest()
    print(f"[DONE] 本次生成 {len(records)} 个样本，记录: {manifest_path}")
    print("[HUMAN GATE] 请人工试听并评分；RTF 需看同缓存键的重复生成。")


if __name__ == "__main__":
    main()