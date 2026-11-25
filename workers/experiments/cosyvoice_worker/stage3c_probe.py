"""阶段 3C 探针：定位句中高频毛刺来自哪条链路。

对同一段文本的三条链路做逐帧频谱对照：
  1. Qwen VoiceDesign 情感源
  2. CosyVoice3 VC（把源音色换成固定角色）
  3. CosyVoice3 instruct2 原生生成

输出 PNG（波形 + 频谱 + 4-10kHz 能量占比 + 频谱平坦度）与 metrics.json。
不生成新音频，只做只读诊断。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/experiments/cosyvoice_worker/stage3c_probe.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf

STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)
OUT_DIR = STAGE3B_DIR.parent / "stage3c" / "probe"

SAD_TEXT = "我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。"

CASES = [
    {
        "id": "qwen_source_s42",
        "label": "Qwen source (s42)",
        "path": STAGE3B_DIR / "qwen_sources" / "narrator_sad_qwen_s42.wav",
        "text": SAD_TEXT,
    },
    {
        "id": "qwen_source_s123",
        "label": "Qwen source (s123)",
        "path": STAGE3B_DIR / "qwen_sources" / "narrator_sad_qwen_s123.wav",
        "text": SAD_TEXT,
    },
    {
        "id": "cosy_vc_s42",
        "label": "CosyVoice VC (s42)",
        "path": STAGE3B_DIR / "vc_test" / "vc_narrator_sad_qwen_s42.wav",
        "text": SAD_TEXT,
    },
    {
        "id": "cosy_vc_s123",
        "label": "CosyVoice VC (s123)",
        "path": STAGE3B_DIR / "vc_test" / "vc_narrator_sad_qwen_s123.wav",
        "text": SAD_TEXT,
    },
    {
        "id": "cosy_instruct_c",
        "label": "CosyVoice instruct2 (c)",
        "path": STAGE3B_DIR / "anchor_pilot" / "sad_instruct_c_clean.wav",
        "text": SAD_TEXT,
    },
    {
        "id": "reference_narrator_03",
        "label": "Narrator 03 reference",
        "path": STAGE3B_DIR / "refs_clean" / "narrator_03.wav",
        "text": "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。远处有人慢慢走过，脚步声很轻。",
    },
]


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float64), sample_rate


def frames(samples: np.ndarray, frame: int, hop: int) -> np.ndarray:
    if samples.size < frame:
        pad = frame - samples.size
        return np.pad(samples, (0, pad))[None, :]
    count = 1 + (samples.size - frame) // hop
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    return samples[index]


def frame_features(samples: np.ndarray, sample_rate: int) -> dict:
    frame = int(sample_rate * 0.03)
    hop = int(sample_rate * 0.01)
    items = frames(samples, frame, hop)
    window = np.hanning(frame)
    spectrum = np.fft.rfft(items * window, axis=1)
    power = np.square(np.abs(spectrum))
    freqs = np.fft.rfftfreq(frame, d=1.0 / sample_rate)
    total = np.sum(power, axis=1) + 1e-15
    rms = np.sqrt(np.mean(np.square(items), axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))

    band_low = (freqs >= 80) & (freqs < 1000)
    band_mid = (freqs >= 1000) & (freqs < 4000)
    band_high = (freqs >= 4000) & (freqs < 10000)
    band_top = (freqs >= 8000) & (freqs < 12000)

    def ratio(mask: np.ndarray) -> np.ndarray:
        return np.sum(power[:, mask], axis=1) / total

    high_ratio = ratio(band_high)
    top_ratio = ratio(band_top)
    # 4-10kHz 的频谱平坦度：噪声型高频接近 1，乐音型高频接近 0。
    high_power = power[:, band_high] + 1e-18
    flatness = np.exp(np.mean(np.log(high_power), axis=1)) / (
        np.mean(high_power, axis=1) + 1e-18
    )
    # 高频峰值因子：局部最强高频分量 / 高频中位数。
    peak_factor = np.max(high_power, axis=1) / (np.median(high_power, axis=1) + 1e-18)
    zcr = np.mean(np.abs(np.diff(np.signbit(items), axis=1)), axis=1)

    active = db > max(db.max() - 40.0, -55.0)
    score = high_ratio * np.clip(flatness, 0, 1) * np.maximum(db - (db.max() - 40), 0)
    score[~active] = 0
    top = np.argsort(score)[-20:][::-1]
    peaks = []
    for index in top:
        peaks.append(
            {
                "time_ms": int(index * hop / sample_rate * 1000),
                "rms_dbfs": round(float(db[index]), 2),
                "high_ratio": round(float(high_ratio[index]), 4),
                "top_ratio": round(float(top_ratio[index]), 4),
                "flatness": round(float(flatness[index]), 4),
                "peak_factor": round(float(peak_factor[index]), 1),
                "zcr": round(float(zcr[index]), 4),
                "score": round(float(score[index]), 3),
            }
        )
    return {
        "sample_rate": sample_rate,
        "duration_ms": int(round(samples.size / sample_rate * 1000)),
        "frames": len(items),
        "hop_ms": round(hop / sample_rate * 1000, 3),
        "rms_dbfs": round(float(20 * np.log10(max(np.sqrt(np.mean(samples**2)), 1e-9))), 2),
        "high_ratio_median": round(float(np.median(high_ratio[active])), 4),
        "high_ratio_p95": round(float(np.percentile(high_ratio[active], 95)), 4),
        "flatness_median": round(float(np.median(flatness[active])), 4),
        "flatness_p95": round(float(np.percentile(flatness[active], 95)), 4),
        "zcr_median": round(float(np.median(zcr[active])), 4),
        "peaks": peaks,
        "_series": {
            "db": db,
            "high_ratio": high_ratio,
            "top_ratio": top_ratio,
            "flatness": flatness,
            "score": score,
            "hop": hop,
            "spectrum": power,
            "freqs": freqs,
        },
    }


def plot_case(case: dict, features: dict, out_dir: Path) -> Path:
    series = features.pop("_series")
    samples, sample_rate = read_mono(case["path"])
    duration = samples.size / sample_rate
    time_axis = np.arange(samples.size) / sample_rate
    frame_time = np.arange(len(series["db"])) * series["hop"] / sample_rate

    fig, axes = plt.subplots(
        4,
        1,
        figsize=(16, 11),
        sharex=False,
        gridspec_kw={"height_ratios": [1.0, 1.8, 0.9, 0.9]},
    )
    axes[0].plot(time_axis, samples, linewidth=0.3, color="#1f77b4")
    axes[0].set_ylabel("waveform")
    axes[0].set_xlim(0, duration)
    axes[0].grid(alpha=0.2)

    power = series["spectrum"]
    freqs = series["freqs"]
    db_power = 10 * np.log10(power.T + 1e-12)
    axes[1].imshow(
        db_power,
        origin="lower",
        aspect="auto",
        extent=[0, duration, 0, freqs[-1] / 1000],
        cmap="magma",
        vmin=np.percentile(db_power, 30),
        vmax=np.percentile(db_power, 99.8),
    )
    axes[1].set_ylabel("frequency (kHz)")
    axes[1].set_ylim(0, 12)

    axes[2].plot(frame_time, series["high_ratio"], linewidth=0.8, color="#d62728")
    axes[2].set_ylabel("4-10k ratio")
    axes[2].set_xlim(0, duration)
    axes[2].grid(alpha=0.2)

    axes[3].plot(frame_time, series["flatness"], linewidth=0.8, color="#2ca02c")
    axes[3].set_ylabel("HF flatness")
    axes[3].set_xlabel("seconds")
    axes[3].set_xlim(0, duration)
    axes[3].grid(alpha=0.2)

    for peak in features["peaks"][:5]:
        axes[0].axvline(peak["time_ms"] / 1000, color="#ff7f0e", alpha=0.5, linewidth=0.8)
        axes[2].axvline(peak["time_ms"] / 1000, color="#ff7f0e", alpha=0.5, linewidth=0.8)

    fig.suptitle(f"{case['label']}  |  {case['text']}")
    fig.tight_layout()
    path = out_dir / f"{case['id']}.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for case in CASES:
        if not case["path"].exists():
            print(f"[SKIP] {case['id']} 不存在: {case['path']}")
            continue
        samples, _ = read_mono(case["path"])
        features = frame_features(samples, 24000)
        plot_path = plot_case(case, features, OUT_DIR)
        record = {
            "id": case["id"],
            "label": case["label"],
            "path": str(case["path"]),
            "text": case["text"],
            "plot": str(plot_path),
            **features,
        }
        records.append(record)
        print(
            f"[OK] {case['id']}: {record['duration_ms']}ms "
            f"HF median={record['high_ratio_median']} p95={record['high_ratio_p95']} "
            f"flat median={record['flatness_median']} p95={record['flatness_p95']}"
        )
        print(
            "     top peaks: "
            + ", ".join(
                f"{item['time_ms']}ms(hf={item['high_ratio']},flat={item['flatness']})"
                for item in record["peaks"][:6]
            )
        )
    manifest = {
        "purpose": "阶段 3C 句中高频毛刺链路定位（只读诊断）",
        "sad_text": SAD_TEXT,
        "records": records,
    }
    (OUT_DIR / "metrics.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {OUT_DIR / 'metrics.json'}")


if __name__ == "__main__":
    main()