"""阶段 3C：句中齿音/气声动态抑制实验。

不改变音素和时长，只在 4-10kHz 高频能量异常突出的帧上做平滑衰减。
提供三档强度与两档低通，用于人工 A/B，不直接作为默认后处理。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3c_deess.py \
      --input <wav> --output-dir <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt, stft, istft


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float64), sample_rate


def smooth_gain(
    gain_db: np.ndarray,
    sample_rate: int,
    hop: int,
    attack_ms: float = 3.0,
    release_ms: float = 70.0,
) -> np.ndarray:
    """对逐帧增益做快攻慢放平滑，避免抽吸感。"""
    attack = max(1, int(attack_ms / (hop / sample_rate * 1000)))
    release = max(1, int(release_ms / (hop / sample_rate * 1000)))
    out = np.empty_like(gain_db)
    current = gain_db[0]
    for index, target in enumerate(gain_db):
        # gain_db 是负值；更负 = 更强衰减。
        coefficient = 1.0 - np.exp(-1.0 / (attack if target < current else release))
        current += (target - current) * coefficient
        out[index] = current
    return out


def deess(
    samples: np.ndarray,
    sample_rate: int,
    band_low: float = 4000.0,
    band_high: float = 10000.0,
    threshold_ratio: float = 0.22,
    full_ratio: float = 0.62,
    max_reduction_db: float = 6.0,
    n_fft: int = 1024,
    hop: int = 256,
) -> tuple[np.ndarray, dict]:
    """在 STFT 域对高频带做动态衰减。

    threshold_ratio: 高频能量占比超过该值开始衰减。
    full_ratio: 达到该占比时衰减到 max_reduction_db。
    """
    if samples.size < n_fft:
        return samples.copy(), {"frames": 0, "reduced_frames": 0, "mean_reduction_db": 0.0}
    freqs, _, spectrum = stft(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=n_fft,
        noverlap=n_fft - hop,
        boundary="zeros",
        padded=True,
    )
    power = np.square(np.abs(spectrum))
    total = np.sum(power, axis=0) + 1e-18
    band_mask = (freqs >= band_low) & (freqs < band_high)
    band_power = np.sum(power[band_mask], axis=0)
    ratio = band_power / total
    # 只在有声音的帧上动作，避免把静音底噪当成齿音。
    frame_rms = np.sqrt(np.mean(np.square(np.abs(spectrum)), axis=0))
    active = frame_rms > max(frame_rms.max() * 0.01, 1e-7)

    amount = np.clip((ratio - threshold_ratio) / max(full_ratio - threshold_ratio, 1e-6), 0, 1)
    amount[~active] = 0.0
    gain_db = -max_reduction_db * amount
    gain_db = smooth_gain(gain_db, sample_rate, hop)
    gain = 10 ** (gain_db / 20.0)

    # 4kHz 以下完全不碰；带内应用增益；带边缘做 500Hz 余弦过渡。
    transition = 500.0
    weight = np.zeros_like(freqs)
    weight[band_mask] = 1.0
    left = (freqs >= band_low - transition) & (freqs < band_low)
    right = (freqs >= band_high) & (freqs < band_high + transition)
    if left.any():
        weight[left] = 0.5 * (1 - np.cos(np.pi * (freqs[left] - (band_low - transition)) / transition))
    if right.any():
        weight[right] = 0.5 * (1 + np.cos(np.pi * (freqs[right] - band_high) / transition))
    frame_gain = 1.0 + (gain[None, :] - 1.0) * weight[:, None]
    processed = istft(
        spectrum * frame_gain,
        fs=sample_rate,
        window="hann",
        nperseg=n_fft,
        noverlap=n_fft - hop,
        boundary=True,
    )[1]
    processed = processed[: samples.size]
    if processed.size < samples.size:
        processed = np.pad(processed, (0, samples.size - processed.size))
    reduced = gain_db < -0.5
    params = {
        "band_low_hz": band_low,
        "band_high_hz": band_high,
        "threshold_ratio": threshold_ratio,
        "full_ratio": full_ratio,
        "max_reduction_db": max_reduction_db,
        "frames": int(len(gain_db)),
        "reduced_frames": int(np.sum(reduced)),
        "reduced_ratio": round(float(np.mean(reduced)), 4),
        "mean_reduction_db": round(float(np.mean(gain_db[reduced])) if reduced.any() else 0.0, 2),
        "min_reduction_db": round(float(gain_db.min()), 2),
        "ratio_before_p95": round(float(np.percentile(ratio[active], 95)), 4) if active.any() else None,
    }
    return processed.astype(np.float32), params


def sibilant_shorten(
    samples: np.ndarray,
    sample_rate: int,
    threshold_ratio: float = 0.35,
    band_low: float = 3500.0,
    band_high: float = 10000.0,
    keep_ms: float = 55.0,
    fade_ms: float = 40.0,
    reduction_db: float = -10.0,
    min_burst_ms: float = 80.0,
    n_fft: int = 1024,
    hop: int = 256,
) -> tuple[np.ndarray, dict]:
    """把过长的高频擦音尾巴平滑收短，而不是整段压低。

    只在连续高频帧超过 min_burst_ms 时动作：保留前 keep_ms，随后 fade_ms
    内衰减到 reduction_db，直到该擦音结束。目的是减轻“菜 / 三”被读成
    “ci”时那种拖长的嘶声；如果听起来咬字被切掉，应放弃这一处理。
    """
    if samples.size < n_fft:
        return samples.copy(), {"bursts": 0, "shortened": 0}
    freqs, _, spectrum = stft(
        samples, fs=sample_rate, window="hann", nperseg=n_fft,
        noverlap=n_fft - hop, boundary="zeros", padded=True,
    )
    power = np.square(np.abs(spectrum))
    total = np.sum(power, axis=0) + 1e-18
    band_mask = (freqs >= band_low) & (freqs < band_high)
    ratio = np.sum(power[band_mask], axis=0) / total
    frame_rms = np.sqrt(np.mean(np.square(np.abs(spectrum)), axis=0))
    active = frame_rms > max(frame_rms.max() * 0.01, 1e-7)
    hot = (ratio >= threshold_ratio) & active

    frame_ms = hop / sample_rate * 1000.0
    bursts = []
    index = 0
    while index < len(hot):
        if not hot[index]:
            index += 1
            continue
        start = index
        while index + 1 < len(hot) and hot[index + 1]:
            index += 1
        end = index
        if (end - start + 1) * frame_ms >= min_burst_ms:
            bursts.append((start, end))
        index += 1

    gain_db = np.zeros(len(hot), dtype=np.float64)
    keep_frames = max(1, int(round(keep_ms / frame_ms)))
    fade_frames = max(1, int(round(fade_ms / frame_ms)))
    for start, end in bursts:
        fade_start = min(start + keep_frames, end + 1)
        fade_end = min(fade_start + fade_frames, end + 1)
        if fade_start > start:
            gain_db[start:fade_start] = 0.0
        if fade_end > fade_start:
            gain_db[fade_start:fade_end] = np.linspace(0.0, reduction_db, fade_end - fade_start)
        if end + 1 > fade_end:
            gain_db[fade_end : end + 1] = reduction_db
    gain_db = smooth_gain(gain_db, sample_rate, hop, attack_ms=4.0, release_ms=30.0)
    gain = 10 ** (gain_db / 20.0)

    weight = np.zeros_like(freqs)
    weight[(freqs >= band_low) & (freqs < band_high)] = 1.0
    transition = 400.0
    left = (freqs >= band_low - transition) & (freqs < band_low)
    right = (freqs >= band_high) & (freqs < band_high + transition)
    if left.any():
        weight[left] = 0.5 * (1 - np.cos(np.pi * (freqs[left] - (band_low - transition)) / transition))
    if right.any():
        weight[right] = 0.5 * (1 + np.cos(np.pi * (freqs[right] - band_high) / transition))
    frame_gain = 1.0 + (gain[None, :] - 1.0) * weight[:, None]
    processed = istft(
        spectrum * frame_gain, fs=sample_rate, window="hann",
        nperseg=n_fft, noverlap=n_fft - hop, boundary=True,
    )[1][: samples.size]
    if processed.size < samples.size:
        processed = np.pad(processed, (0, samples.size - processed.size))
    return processed.astype(np.float32), {
        "bursts": len(bursts),
        "shortened": len(bursts),
        "hot_frames": int(np.sum(hot)),
        "reduction_db": reduction_db,
        "keep_ms": keep_ms,
        "fade_ms": fade_ms,
    }


def lowpass(samples: np.ndarray, sample_rate: int, cutoff: float) -> np.ndarray:
    nyquist = sample_rate / 2.0
    sos = butter(8, min(cutoff / nyquist, 0.99), btype="lowpass", output="sos")
    return sosfiltfilt(sos, samples).astype(np.float32)


def high_shelf(samples: np.ndarray, sample_rate: int, cutoff: float, gain_db: float) -> np.ndarray:
    """一阶高频搁架，用于整体亮度对齐；正增益不实现，避免提升。"""
    if gain_db >= 0:
        return samples.copy()
    nyquist = sample_rate / 2.0
    sos = butter(2, min(cutoff / nyquist, 0.99), btype="highpass", output="sos")
    high = sosfiltfilt(sos, samples)
    gain = 10 ** (gain_db / 20.0)
    return (samples - high + high * gain).astype(np.float32)


def spectral_stats(path_or_samples, sample_rate: int | None = None) -> dict:
    if isinstance(path_or_samples, Path):
        samples, sample_rate = read_mono(path_or_samples)
    else:
        samples = np.asarray(path_or_samples, dtype=np.float64)
    assert sample_rate is not None
    n_fft = 1024
    hop = 256
    freqs, _, spectrum = stft(
        samples,
        fs=sample_rate,
        window="hann",
        nperseg=n_fft,
        noverlap=n_fft - hop,
        boundary="zeros",
        padded=True,
    )
    power = np.square(np.abs(spectrum))
    total = np.sum(power, axis=0) + 1e-18
    band = (freqs >= 4000) & (freqs < 10000)
    ratio = np.sum(power[band], axis=0) / total
    rms = np.sqrt(np.mean(np.square(np.abs(spectrum)), axis=0))
    active = rms > max(rms.max() * 0.01, 1e-7)
    ltas = power[:, active].sum(axis=1)
    ltas /= ltas.sum()
    bands = [(0, 500), (500, 1000), (1000, 2000), (2000, 4000), (4000, 6000), (6000, 8000), (8000, 10000), (10000, 12000)]
    balance = {}
    for low, high in bands:
        mask = (freqs >= low) & (freqs < high)
        balance[f"{low}-{high}"] = round(float(10 * np.log10(ltas[mask].sum() + 1e-15)), 2)
    return {
        "duration_ms": int(round(samples.size / sample_rate * 1000)),
        "high_ratio_median": round(float(np.median(ratio[active])), 4),
        "high_ratio_p95": round(float(np.percentile(ratio[active], 95)), 4),
        "high_ratio_max": round(float(np.max(ratio[active])), 4),
        "ltas_db": balance,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="阶段 3C 齿音/气声动态抑制")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()

    samples, sample_rate = read_mono(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = args.prefix or args.input.stem
    variants = {
        "raw": samples.astype(np.float32),
        "deess_g0": deess(
            samples, sample_rate,
            threshold_ratio=0.30, full_ratio=0.72, max_reduction_db=3.0,
        )[0],
        "deess_g1": deess(samples, sample_rate, max_reduction_db=5.0, threshold_ratio=0.24)[0],
        "deess_g2": deess(samples, sample_rate, max_reduction_db=10.0, threshold_ratio=0.20)[0],
        "deess_extreme": deess(
            samples, sample_rate,
            threshold_ratio=0.40, full_ratio=0.85, max_reduction_db=8.0,
        )[0],
        "deess_mid": deess(
            samples, sample_rate,
            band_low=2500, band_high=6500,
            threshold_ratio=0.16, full_ratio=0.55, max_reduction_db=5.0,
        )[0],
        "deess_breath": deess(
            samples, sample_rate,
            band_low=2500, band_high=8000,
            threshold_ratio=0.22, full_ratio=0.70, max_reduction_db=5.0,
        )[0],
        "lp10k": lowpass(samples, sample_rate, 10000),
        "lp8k": lowpass(samples, sample_rate, 8000),
        "shelf4k_2db": high_shelf(samples, sample_rate, 4000, -2.0),
        "shelf4k_3db": high_shelf(samples, sample_rate, 4000, -3.0),
    }
    records = []
    for name, audio in variants.items():
        path = args.output_dir / f"{base}_{name}.wav"
        sf.write(str(path), audio, sample_rate, subtype="PCM_16")
        record = {
            "variant": name,
            "file": str(path),
            "stats": spectral_stats(audio, sample_rate),
        }
        records.append(record)
        print(f"[OK] {path.name} HF p95={record['stats']['high_ratio_p95']}")
    manifest = {
        "input": str(args.input),
        "sample_rate": sample_rate,
        "variants": records,
    }
    (args.output_dir / f"{base}_deess.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {args.output_dir / f'{base}_deess.json'}")


if __name__ == "__main__":
    main()