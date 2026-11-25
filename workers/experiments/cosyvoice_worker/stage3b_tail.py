"""阶段 3B：句尾气声/杂音抑制与客观对比。

问题：CosyVoice3 在分句生成时，每句结尾可能带一段漏气/噪声。整段尾部静音
指标看不出来，因为气声紧贴最后一个音节。

做法：对每个推理 chunk（即每个分句）单独检测“最后一个有周期性的人声帧”，
保留 40ms 自然衰减，再用 30ms 淡出截掉后面的气声。只处理句尾，不改句中。
另外输出一版“衰减而非硬切”的变体，方便人工 A/B。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_tail.py
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3b_tail.py --file xxx.wav
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULT_STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)


def _frames(samples: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if samples.size < frame:
        return samples[None, :]
    count = 1 + (samples.size - frame) // hop
    return np.stack([samples[i * hop : i * hop + frame] for i in range(count)])


def frame_voicing(samples: np.ndarray, sample_rate: int, frame_ms: int = 30, hop_ms: int = 10):
    """返回每帧的 RMS、归一化自相关峰值（周期性）与帧起点。"""
    frame = int(sample_rate * frame_ms / 1000)
    hop = int(sample_rate * hop_ms / 1000)
    frames = _frames(samples.astype(np.float64), frame, hop)
    frames = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    # 归一化自相关：只搜索 60–400 Hz 的基频范围。
    min_lag = max(1, int(sample_rate / 400))
    max_lag = min(frame - 2, int(sample_rate / 60))
    periodicity = np.zeros(len(frames), dtype=np.float64)
    for index, item in enumerate(frames):
        energy = float(np.dot(item, item))
        if energy <= 1e-12:
            continue
        corr = np.correlate(item, item, mode="full")[frame - 1 :]
        corr /= energy
        periodicity[index] = float(np.max(corr[min_lag : max_lag + 1]))
    starts = np.arange(len(frames)) * hop
    return rms, periodicity, starts


def find_last_voiced(samples: np.ndarray, sample_rate: int, keep_ms: float = 40.0) -> int:
    """返回应保留的样本数；找不到可靠人声时返回原长度。"""
    if samples.size < sample_rate * 0.15:
        return samples.size
    rms, periodicity, starts = frame_voicing(samples, sample_rate)
    if rms.size == 0:
        return samples.size
    peak = float(np.max(rms))
    if peak <= 1e-6:
        return samples.size
    # 人声帧：足够响 + 有明显周期性。阈值偏保守，宁可少切。
    voiced = (rms > max(peak * 0.015, 1e-4)) & (periodicity > 0.32)
    indices = np.flatnonzero(voiced)
    if indices.size == 0:
        return samples.size
    last = int(indices[-1])
    frame = int(sample_rate * 0.03)
    end = starts[last] + frame + int(sample_rate * keep_ms / 1000)
    # 最多切掉 0.7s，避免把正常停顿误判为气声。
    end = max(end, samples.size - int(sample_rate * 0.7))
    return int(min(end, samples.size))


def trim_tail(samples: np.ndarray, sample_rate: int, keep_ms: float = 40.0, fade_ms: float = 35.0):
    end = find_last_voiced(samples, sample_rate, keep_ms=keep_ms)
    if end >= samples.size:
        return samples.copy(), samples.size, 0
    trimmed = samples[:end].copy()
    fade = min(int(sample_rate * fade_ms / 1000), trimmed.size)
    if fade > 1:
        ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32)
        trimmed[-fade:] *= ramp
    return trimmed, end, samples.size - end


def attenuate_tail(
    samples: np.ndarray,
    sample_rate: int,
    keep_ms: float = 40.0,
    tail_ms: float = 220.0,
    gain_db: float = -12.0,
):
    """不硬切：对最后一个强人声帧之后的尾部做淡入淡出的衰减。"""
    end = find_last_voiced(samples, sample_rate, keep_ms=keep_ms)
    if end >= samples.size:
        return samples.copy(), 0
    out = samples.copy()
    start = end
    stop = min(samples.size, end + int(sample_rate * tail_ms / 1000))
    if stop <= start:
        return out, 0
    gain = 10 ** (gain_db / 20.0)
    # 从原增益平滑过渡到目标增益，再淡出到 0，避免爆音。
    length = stop - start
    ramp = np.linspace(1.0, gain, max(2, length // 2), dtype=np.float32)
    out[start : start + ramp.size] *= ramp
    rest_start = start + ramp.size
    if rest_start < stop:
        out[rest_start:stop] *= gain
    fade = min(int(sample_rate * 0.03), stop - start)
    if fade > 1:
        out[stop - fade : stop] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out, stop - start


def process_file(path: Path, out_dir: Path, trim_dir: Path, atten_dir: Path) -> dict:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    trimmed, kept, removed = trim_tail(mono, sample_rate)
    attenuated, atten_len = attenuate_tail(mono, sample_rate)
    out_dir.mkdir(parents=True, exist_ok=True)
    trim_dir.mkdir(parents=True, exist_ok=True)
    atten_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(trim_dir / path.name), trimmed, sample_rate, subtype="PCM_16")
    sf.write(str(atten_dir / path.name), attenuated, sample_rate, subtype="PCM_16")
    return {
        "file": path.name,
        "sample_rate": sample_rate,
        "original_ms": int(round(mono.size / sample_rate * 1000)),
        "trimmed_ms": int(round(trimmed.size / sample_rate * 1000)),
        "removed_ms": int(round(removed / sample_rate * 1000)),
        "attenuated_tail_ms": int(round(atten_len / sample_rate * 1000)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 3B 句尾气声抑制")
    parser.add_argument("--stage3b-dir", type=Path, default=DEFAULT_STAGE3B_DIR)
    parser.add_argument("--file", type=Path, default=None, help="只处理单个文件")
    parser.add_argument("--glob", default="*.wav", help="批量匹配模式")
    args = parser.parse_args()

    source_dir = args.stage3b_dir / "experiments"
    out_dir = args.stage3b_dir / "experiments_tail"
    if args.file:
        files = [args.file]
    else:
        files = sorted(source_dir.glob(args.glob))
    records = [process_file(path, out_dir, out_dir / "trim", out_dir / "attenuate") for path in files]
    manifest = {
        "purpose": "阶段 3B 句尾气声抑制：trim 硬切 + attenuate 衰减",
        "keep_ms": 40,
        "fade_ms": 35,
        "attenuate_gain_db": -12,
        "attenuate_tail_ms": 220,
        "records": records,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tail_metrics.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for item in records:
        print(
            f"[OK] {item['file']:<32} {item['original_ms']}ms -> trim {item['trimmed_ms']}ms "
            f"(-{item['removed_ms']}ms), atten {item['attenuated_tail_ms']}ms"
        )


if __name__ == "__main__":
    main()