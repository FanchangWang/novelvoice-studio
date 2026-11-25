"""阶段 3 音质 spike：CosyVoice3 情绪、参考音、种子、模式与后处理实验。

实验定义在 stage3_cases.json，避免把大量文本硬编码进脚本。
输出到数据目录 poc_output/stage3/experiments，不覆盖阶段 0 样本。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3_ab.py --group all
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3_ab.py --group core,eval
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
DEFAULT_MODEL_DIR = DEFAULT_CACHE / "Fun-CosyVoice3-0.5B-2512"
DEFAULT_STAGE3_DIR = DEFAULT_CACHE.parent / "poc_output" / "stage3"
DEFAULT_SOURCE_DIR = Path(__file__).resolve().parent / "CosyVoice"
DEFAULT_CASES = Path(__file__).resolve().parent / "stage3_cases.json"

SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return samples.mean(axis=1), sample_rate


def silence_edges(samples: np.ndarray, sample_rate: int, threshold_db: float = -50.0) -> tuple[int, int]:
    if samples.size == 0:
        return 0, 0
    threshold = 10 ** (threshold_db / 20.0)
    non_silent = np.flatnonzero(np.abs(samples) > threshold)
    if non_silent.size == 0:
        return int(samples.size / sample_rate * 1000), 0
    return (
        int(non_silent[0] / sample_rate * 1000),
        int((samples.size - 1 - non_silent[-1]) / sample_rate * 1000),
    )


def tail_metrics(samples: np.ndarray, sample_rate: int) -> dict:
    duration_ms = int(round(samples.size / sample_rate * 1000))
    if samples.size == 0:
        return {
            "tail_200ms_rms_dbfs": None,
            "tail_200ms_hf_ratio": None,
            "tail_100ms_to_overall_rms": None,
        }
    tail_size = min(samples.size, int(sample_rate * 0.2))
    tail = samples[-tail_size:]
    overall_rms = float(np.sqrt(np.mean(np.square(samples))))
    tail_rms = float(np.sqrt(np.mean(np.square(tail))))
    tail_100 = samples[-min(samples.size, int(sample_rate * 0.1)):]
    tail_100_rms = float(np.sqrt(np.mean(np.square(tail_100))))
    spectrum = np.fft.rfft(tail * np.hanning(tail.size))
    power = np.square(np.abs(spectrum))
    freqs = np.fft.rfftfreq(tail.size, d=1.0 / sample_rate)
    total_energy = float(np.sum(power)) or 1e-12
    hf_energy = float(np.sum(power[freqs >= 4000]))
    return {
        "tail_200ms_rms_dbfs": round(20 * np.log10(max(tail_rms, 1e-9)), 2),
        "tail_200ms_hf_ratio": round(hf_energy / total_energy, 4),
        "tail_100ms_to_overall_rms": round(tail_100_rms / max(overall_rms, 1e-9), 4),
        "duration_ms": duration_ms,
    }


def audio_stats(path: Path) -> dict:
    mono, sample_rate = read_audio(path)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
    lead_ms, tail_ms = silence_edges(mono, sample_rate)
    clipping = float(np.mean(np.abs(mono) >= 0.999)) if mono.size else 0.0
    return {
        "sample_rate": sample_rate,
        "duration_ms": int(round(mono.size / sample_rate * 1000)),
        "peak": round(peak, 4),
        "rms_dbfs": round(20 * np.log10(max(rms, 1e-9)), 2),
        "dc_offset": round(float(np.mean(mono)), 6),
        "clipping_ratio": round(clipping, 6),
        "leading_silence_ms": lead_ms,
        "trailing_silence_ms": tail_ms,
        **tail_metrics(mono, sample_rate),
    }


def trim_and_process(
    samples: np.ndarray,
    sample_rate: int,
    target_rms_dbfs: float = -20.0,
    peak_limit: float = 0.95,
) -> tuple[np.ndarray, dict]:
    threshold = 10 ** (-50.0 / 20.0)
    non_silent = np.flatnonzero(np.abs(samples) > threshold)
    if non_silent.size == 0:
        raise ValueError("音频全为静音，无法后处理")
    lead_keep = int(sample_rate * 0.05)
    tail_keep = int(sample_rate * 0.15)
    start = max(0, int(non_silent[0]) - lead_keep)
    end = min(samples.size, int(non_silent[-1]) + tail_keep + 1)
    out = samples[start:end].copy()

    fade_in = min(int(sample_rate * 0.01), out.size)
    fade_out = min(int(sample_rate * 0.04), out.size)
    if fade_in:
        out[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)
    if fade_out:
        out[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, endpoint=False)

    target_rms = 10 ** (target_rms_dbfs / 20.0)
    rms = float(np.sqrt(np.mean(np.square(out))))
    gain = target_rms / max(rms, 1e-9)
    peak_after = float(np.max(np.abs(out))) * gain
    if peak_after > peak_limit:
        gain = peak_limit / max(float(np.max(np.abs(out))), 1e-9)
    out *= gain
    return out, {
        "trim_start_sample": start,
        "trim_end_sample": end,
        "removed_lead_ms": int(start / sample_rate * 1000),
        "removed_tail_ms": int((samples.size - end) / sample_rate * 1000),
        "fade_in_ms": round(fade_in / sample_rate * 1000, 2),
        "fade_out_ms": round(fade_out / sample_rate * 1000, 2),
        "target_rms_dbfs": target_rms_dbfs,
        "peak_limit": peak_limit,
        "applied_gain": round(float(gain), 4),
    }


def build_instruct_text(instruction: str) -> str:
    body = instruction.strip()
    if not body:
        return "You are a helpful assistant.<|endofprompt|>"
    return f"You are a helpful assistant. {body}<|endofprompt|>"


def build_prompt_text(prompt_body: str) -> str:
    body = prompt_body.strip()
    return f"You are a helpful assistant.<|endofprompt|>{body}"


def generate_chunks(
    model,
    case: dict,
    reference_text: str,
    set_seed,
) -> tuple[list[np.ndarray], list[int], list[float]]:
    seed = int(case.get("seed", 42))
    mode = case.get("mode", "instruct2")
    text = case["text"]
    chunking = case.get("chunking", "native")
    texts = [text]
    if chunking == "per_sentence":
        texts = [item.strip() for item in SENTENCE_SPLIT.split(text) if item.strip()]

    chunks: list[np.ndarray] = []
    seeds: list[int] = []
    elapsed: list[float] = []
    for index, sentence in enumerate(texts):
        sentence_seed = seed if chunking == "native" else seed + index
        seeds.append(sentence_seed)
        set_seed(sentence_seed)
        started = time.perf_counter()
        if mode == "zero_shot":
            prompt_text = build_prompt_text(case.get("prompt_text", reference_text))
            outputs = model.inference_zero_shot(
                sentence,
                prompt_text,
                case["_ref_path"],
                stream=False,
                text_frontend=True,
            )
        else:
            outputs = model.inference_instruct2(
                sentence,
                build_instruct_text(case.get("instruction", "")),
                case["_ref_path"],
                stream=False,
                text_frontend=True,
            )
        sentence_chunks = [item["tts_speech"] for item in outputs]
        if not sentence_chunks:
            raise RuntimeError(f"{case['id']}: 推理未返回音频")
        for item in sentence_chunks:
            chunks.append(item.squeeze(0).float().cpu().numpy())
        elapsed.append(time.perf_counter() - started)
    return chunks, seeds, elapsed


def merge_records(path: Path, new_records: list[dict]) -> list[dict]:
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = data.get("records", [])
        except (json.JSONDecodeError, OSError):
            existing = []
    by_id = {item["id"]: item for item in existing}
    for item in new_records:
        by_id[item["id"]] = item
    return list(by_id.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CosyVoice3 阶段 3 音质实验")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--stage3-dir", type=Path, default=DEFAULT_STAGE3_DIR)
    parser.add_argument(
        "--group",
        default="all",
        help="all 或逗号分隔的 core,ab,eval",
    )
    parser.add_argument("--only", default="", help="只跑指定 case id，逗号分隔")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的 wav")
    return parser.parse_args()


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

    experiment_dir = args.stage3_dir / "experiments"
    clean_dir = args.stage3_dir / "experiments_clean"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = experiment_dir / "records.json"

    import sys

    sys.path.insert(0, str(DEFAULT_SOURCE_DIR))
    sys.path.insert(0, str(DEFAULT_SOURCE_DIR / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import AutoModel
    from cosyvoice.utils.common import set_all_random_seed

    started = time.perf_counter()
    model = AutoModel(model_dir=str(args.model_dir))
    load_seconds = time.perf_counter() - started
    print(f"[INFO] 模型加载耗时 {load_seconds:.2f}s")
    print(f"[INFO] 文本前端: {model.frontend.text_frontend or '无'}")

    records: list[dict] = []
    outputs_by_id: dict[str, Path] = {}

    def write_manifest() -> None:
        # 每个 case 后落盘，避免中途失败丢失已完成记录。
        merged_records = merge_records(manifest_path, records)
        manifest = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "model_dir": str(args.model_dir),
            "text_frontend": getattr(model.frontend, "text_frontend", ""),
            "cases_file": str(args.cases),
            "records": sorted(merged_records, key=lambda item: item["id"]),
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

        if case.get("postprocess"):
            source_id = case["source_case"]
            source_path = outputs_by_id.get(source_id) or experiment_dir / f"{source_id}.wav"
            if not source_path.exists():
                raise RuntimeError(f"{case_id}: 源 case 不存在: {source_path}")
            samples, sample_rate = read_audio(source_path)
            processed, params = trim_and_process(samples, sample_rate)
            sf.write(str(output_path), processed, sample_rate, subtype="PCM_16")
            record = {
                "id": case_id,
                "group": case.get("group", ""),
                "source_case": source_id,
                "text": case.get("text", ""),
                "notes": case.get("notes", ""),
                "postprocess": params,
                "reference_wav": str(source_path),
                "sha256": sha256_file(output_path),
                **audio_stats(output_path),
            }
            records.append(record)
            outputs_by_id[case_id] = output_path
            write_manifest()
            print(f"[OK] {case_id} <- {source_id}, {record['duration_ms']}ms")
            continue

        character = characters.get(case["character"])
        if character is None:
            raise RuntimeError(f"{case_id}: 未知角色 {case['character']}")
        ref_path = (args.stage3_dir / character["ref_file"]).resolve()
        if not ref_path.exists():
            raise RuntimeError(f"{case_id}: 参考音不存在 {ref_path}")

        enriched = dict(case)
        enriched["_ref_path"] = str(ref_path)
        chunks, seeds, elapsed = generate_chunks(
            model, enriched, character["reference_text"], set_all_random_seed
        )
        merged = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        sf.write(str(output_path), merged, model.sample_rate, subtype="PCM_16")
        raw_stats = audio_stats(output_path)

        clean_path = clean_dir / f"{case_id}.wav"
        clean_params = None
        clean_stats = None
        failure = None
        try:
            # 空参考文本的 zero_shot 可能输出极短静音；保留原始样本并记录失败，
            # 不能把失败样本误当成有效音频进入评估集。
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
            "mode": case.get("mode", "instruct2"),
            "chunking": case.get("chunking", "native"),
            "text": case["text"],
            "instruction": case.get("instruction", ""),
            "prompt_text": case.get("prompt_text", ""),
            "notes": case.get("notes", ""),
            "seed": int(case.get("seed", 42)),
            "per_chunk_seeds": seeds,
            "reference_wav": str(ref_path),
            "reference_text": character["reference_text"],
            "text_frontend": model.frontend.text_frontend,
            "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            "model_dir": str(args.model_dir),
            "load_seconds": round(load_seconds, 3),
            "generation_seconds": [round(item, 3) for item in elapsed],
            "chunk_count": len(chunks),
            "rtf": round(sum(elapsed) / max(merged.size / model.sample_rate, 1e-9), 3),
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
                f"[OK] {case_id} {record['duration_ms']}ms chunks={record['chunk_count']} "
                f"peak={record['peak']} rms={record['rms_dbfs']}dBFS "
                f"tail_hf={record['tail_200ms_hf_ratio']} rtf={record['rtf']}"
            )

    merged_records = merge_records(manifest_path, records)
    manifest = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "model_dir": str(args.model_dir),
        "text_frontend": getattr(model.frontend, "text_frontend", ""),
        "cases_file": str(args.cases),
        "records": sorted(merged_records, key=lambda item: item["id"]),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] 本次生成 {len(records)} 个样本，记录: {manifest_path}")
    print("[HUMAN GATE] 请人工试听并填写阶段 3 音质评分。")


if __name__ == "__main__":
    main()