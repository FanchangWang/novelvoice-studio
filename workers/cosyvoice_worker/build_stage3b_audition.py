"""阶段 3B 试听包生成器。

把分散在 stage3b 各实验目录的 WAV 复制成一份可交付试听包，并生成自包含的
audition.html（无外网依赖）。音频只做复制，不做响度归一化，避免掩盖问题。

用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/build_stage3b_audition.py

输出（默认）：
  ~/Documents/NovelVoiceStudio/cache/poc_output/stage3b/audition.html
  ~/Documents/NovelVoiceStudio/cache/poc_output/stage3b/audition_audio/
  ~/Documents/NovelVoiceStudio/cache/poc_output/stage3b/audition_manifest.json
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULT_STAGE3B_DIR = (
    Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3b"
)

CORE_TEXT = "我等你很久了。你答应过今天一定回来，可现在天都黑了。"
SAD_TEXT = "我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。"
ANGRY_TEXT = "你答应过的事，为什么总是做不到？你让我怎么相信你？"
NEUTRAL_TEXT = "夜里的风从窗缝里钻进来，屋里很安静。"
STAGE3_REF_TEXT = (
    "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。"
    "远处有人慢慢走过，脚步声很轻。"
)
B08_REF_TEXT = "雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。"


def audio_stats(path: Path) -> dict:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    duration_s = mono.size / sample_rate if sample_rate else 0.0
    if mono.size == 0:
        return {"duration_ms": 0, "tail_200ms_hf_ratio": None, "tail_200ms_rms_dbfs": None}
    tail_size = min(mono.size, int(sample_rate * 0.2))
    tail = mono[-tail_size:]
    overall_rms = float(np.sqrt(np.mean(np.square(mono))))
    tail_rms = float(np.sqrt(np.mean(np.square(tail))))
    spectrum = np.fft.rfft(tail * np.hanning(tail.size))
    power = np.square(np.abs(spectrum))
    freqs = np.fft.rfftfreq(tail.size, d=1.0 / sample_rate)
    total_energy = float(np.sum(power)) or 1e-12
    hf_energy = float(np.sum(power[freqs >= 4000]))
    return {
        "duration_ms": int(round(duration_s * 1000)),
        "tail_200ms_hf_ratio": round(hf_energy / total_energy, 4),
        "tail_200ms_rms_dbfs": round(20 * np.log10(max(tail_rms, 1e-9)), 2),
    }


def sample(
    sample_id: str,
    title: str,
    src: Path,
    text: str,
    tone: str,
    badge: str,
    desc: str,
    *,
    sim_key: str | None = None,
    sim_approx: bool = False,
    track_id: str | None = None,
    show_tail: bool = False,
    note: str = "",
    ref_cps: float | None = None,
) -> dict:
    return {
        "id": sample_id,
        "track_id": track_id or sample_id,
        "title": title,
        "src": src,
        "text": text,
        "tone": tone,
        "badge": badge,
        "desc": desc,
        "sim_key": sim_key,
        "sim_approx": sim_approx,
        "show_tail": show_tail,
        "note": note,
        "ref_cps": ref_cps,
    }


def build_sections(base: Path) -> list[dict]:
    exp = base / "experiments"
    fp32 = base / "fp32_core"
    vc = base / "vc_test" / "clean"
    vc_raw = base / "vc_test"
    pilot = base / "anchor_pilot"
    tail = base / "experiments_tail"
    refs = base / "refs_clean"
    sources = base / "qwen_sources"

    quick = [
        sample(
            "q_narrator_sad_vc", "旁白 · sad · Qwen+VC（压抑）", vc / "vc_narrator_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "Qwen 源生成悲伤韵律，CosyVoice VC 转回 narrator_03 音色。约 3.7 字/秒，不再是旧 sad 的 2.5。",
            sim_key="vc:narrator_sad", track_id="narrator_sad_vc",
        ),
        sample(
            "q_narrator_angry_cold_vc", "旁白 · angry · Qwen+VC（冷怒）", vc / "vc_narrator_angry_cold_qwen_s42.wav",
            ANGRY_TEXT, "vc", "Qwen+VC",
            "冷怒版本。与参考音相似度 0.896，比阶段 3 的 angry 0.740 更稳；听是否够有力度。",
            sim_key="vc:narrator_angry_cold", track_id="narrator_angry_cold_vc",
        ),
        sample(
            "q_young_angry_vc", "年轻女性 · angry · Qwen+VC", vc / "vc_young_female_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "Qwen+VC",
            "阶段 3 同角色 angry 与参考音只有 0.516，几乎像换了人；这条是 0.846。本轮最关键的 A/B。",
            sim_key="vc:young_female_angry", track_id="young_angry_vc",
        ),
        sample(
            "q_old_b08_neutral", "老年男性 · neutral · b08 新参考音", fp32 / "old_male_b08_neutral_clean.wav",
            CORE_TEXT, "candidate", "b08 参考音",
            "新的“清瘦略快”参考音，中性句 5.1 字/秒。先判断音色是否仍是你要的老年男性。",
            sim_key="fp32:old_male_b08_neutral", track_id="old_b08_neutral",
        ),
        sample(
            "q_old_b08_sad_vc", "老年男性 · sad · b08 + Qwen+VC", vc / "vc_old_male_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "b08 音色 + Qwen 悲伤韵律。约 3.80 字/秒，约 3.80 字/秒，比旧 sad 的 2.5 更接近正常说话。",
            sim_key="vc:old_male_sad", track_id="old_b08_sad_vc",
        ),
        sample(
            "q_old03_speed110", "老年男性 · neutral · old03 + 1.1x", exp / "sel_old_male_neutral_speed110.wav",
            CORE_TEXT, "candidate", "旧参考音加速",
            "旧参考音 old03 直接加速 1.1 倍。听它是否只是“变快”，而 b08 是真正更自然。",
            sim_key="stage3:old_male_neutral", sim_approx=True, track_id="old03_neutral_speed110",
        ),
    ]

    sad_narrator = [
        sample(
            "s_narrator_stage3", "阶段 3 基线 · 旧 sad 指令", exp / "sel_narrator_sad.wav",
            CORE_TEXT, "baseline", "基线",
            "10.48 秒 / 2.48 字每秒。旧指令明确要求“语速较慢”，模型照做了。",
            sim_key="stage3:narrator_sad",
        ),
        sample(
            "s_narrator_cosy_c", "CosyVoice · 克制 sad 指令", pilot / "sad_instruct_c_clean.wav",
            SAD_TEXT, "candidate", "新指令",
            "8.80 秒 / 3.64 字每秒。指令改为“语速正常，不要每个字都拖慢”。",
            sim_key="pilot:sad_instruct_c",
        ),
        sample(
            "s_narrator_cosy_speed110", "CosyVoice · 克制 sad + 1.1x", pilot / "sad_instruct_c_speed110_clean.wav",
            SAD_TEXT, "candidate", "新指令 + 加速",
            "7.86 秒 / 4.07 字每秒。同一指令再做 1.1 倍语速，听是否自然。",
            sim_key="pilot:sad_speed110",
        ),
        sample(
            "s_narrator_zero_anchor", "CosyVoice · 中性零样本锚点", pilot / "sad_zero_anchor_clean.wav",
            SAD_TEXT, "candidate", "零样本",
            "6.28 秒 / 5.10 字每秒。不加悲伤指令，只给中性锚点；听是否还有悲伤。",
            sim_key="pilot:sad_zero_anchor",
        ),
        sample(
            "s_narrator_vc", "Qwen+VC · sad（压抑）", vc / "vc_narrator_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "8.39 秒 / 3.81 字每秒。Qwen 源负责情感，VC 负责音色。",
            sim_key="vc:narrator_sad", track_id="narrator_sad_vc",
        ),
        sample(
            "s_narrator_vc_natural", "Qwen+VC · sad（自然）", vc / "vc_narrator_sad_natural_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "7.39 秒 / 4.33 字每秒。更克制、更接近日常说话的悲伤。",
            sim_key="vc:narrator_sad_natural",
        ),
        sample(
            "s_narrator_vc_tired", "Qwen+VC · sad（疲惫）", vc / "vc_narrator_sad_tired_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "7.22 秒 / 4.43 字每秒。疲惫感；原始尾音有气声，可在“尾音 A/B”对照处理效果。",
            sim_key="vc:narrator_sad_tired",
        ),
    ]

    sad_young = [
        sample(
            "s_young_stage3", "年轻女性 · 阶段 3 sad 基线", exp / "sel_young_female_sad.wav",
            CORE_TEXT, "baseline", "基线",
            "10.48 秒 / 2.48 字每秒。与旁白一样，被旧 sad 指令拖慢。",
            sim_key="stage3:young_female_sad",
        ),
        sample(
            "s_young_cosy_c", "年轻女性 · CosyVoice 克制 sad", exp / "emo_young_female_sad_c.wav",
            CORE_TEXT, "candidate", "新指令",
            "5.24 秒 / 4.96 字每秒。语速恢复正常，音色相似度 0.887。",
            sim_key="exp:young_female_sad_c",
        ),
        sample(
            "s_young_vc", "年轻女性 · Qwen+VC sad", vc / "vc_young_female_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "7.77 秒 / 4.12 字每秒。本轮身份相似度最高的一条（0.933）。",
            sim_key="vc:young_female_sad", track_id="young_sad_vc",
        ),
    ]

    sad_old = [
        sample(
            "s_old_stage3", "老年男性 · 阶段 3 sad 基线（old03）", exp / "sel_old_male_sad.wav",
            CORE_TEXT, "baseline", "基线",
            "10.48 秒 / 2.48 字每秒。old03 参考音本身只有 2.46 字每秒，模型进一步拖慢。",
            sim_key="stage3:old_male_sad",
        ),
        sample(
            "s_old_b08_cosy", "老年男性 · b08 + 克制 sad", exp / "old_b08_sad.wav",
            CORE_TEXT, "candidate", "新参考音",
            "9.16 秒 / 2.84 字每秒。比基线快一点，但仍然偏慢。",
            sim_key="exp:old_b08_sad",
        ),
        sample(
            "s_old_b08_vc", "老年男性 · b08 + Qwen+VC sad", vc / "vc_old_male_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "8.52 秒 / 3.80 字每秒。目前老年 sad 里语速最接近正常的一条。",
            sim_key="vc:old_male_sad", track_id="old_b08_sad_vc",
        ),
    ]

    angry_narrator = [
        sample(
            "a_narrator_stage3", "旁白 · 阶段 3 angry 基线", exp / "sel_narrator_angry.wav",
            CORE_TEXT, "baseline", "基线",
            "6.32 秒 / 4.11 字每秒；与参考音相似度 0.740。",
            sim_key="stage3:narrator_angry",
        ),
        sample(
            "a_narrator_cosy_b", "旁白 · CosyVoice 克制不满", pilot / "angry_instruct_b_clean.wav",
            ANGRY_TEXT, "candidate", "新指令",
            "5.15 秒 / 4.85 字每秒；相似度 0.894。压低声音，不喊叫。",
            sim_key="pilot:angry_instruct_b",
        ),
        sample(
            "a_narrator_vc", "旁白 · Qwen+VC angry（外放）", vc / "vc_narrator_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "Qwen+VC",
            "3.44 秒 / 7.27 字每秒。更外放、更快；听是否快到失真。",
            sim_key="vc:narrator_angry", track_id="narrator_angry_vc",
        ),
        sample(
            "a_narrator_cold_vc", "旁白 · Qwen+VC angry（冷怒）", vc / "vc_narrator_angry_cold_qwen_s42.wav",
            ANGRY_TEXT, "vc", "Qwen+VC",
            "4.31 秒 / 5.80 字每秒；相似度 0.896。冷怒、克制，身份更稳。",
            sim_key="vc:narrator_angry_cold", track_id="narrator_angry_cold_vc",
        ),
    ]

    angry_young = [
        sample(
            "a_young_stage3", "年轻女性 · 阶段 3 angry 基线", exp / "sel_young_female_angry.wav",
            CORE_TEXT, "warning", "身份崩坏",
            "6.32 秒；与参考音相似度只有 0.516。这是你打 1/10 的最可能原因。",
            sim_key="stage3:young_female_angry",
        ),
        sample(
            "a_young_cosy_c", "年轻女性 · CosyVoice 新 angry 指令", exp / "emo_young_female_angry_c.wav",
            CORE_TEXT, "candidate", "新指令",
            "5.20 秒；相似度 0.666。比基线好，但仍不够稳。",
            sim_key="exp:young_female_angry_c",
        ),
        sample(
            "a_young_vc", "年轻女性 · Qwen+VC angry", vc / "vc_young_female_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "Qwen+VC",
            "4.01 秒 / 6.23 字每秒；相似度 0.846。目前最稳的年轻女性 angry。",
            sim_key="vc:young_female_angry", track_id="young_angry_vc",
        ),
    ]

    angry_old = [
        sample(
            "a_old_b08", "老年男性 · b08 + 阶段 3 angry 指令", fp32 / "old_male_b08_angry_clean.wav",
            CORE_TEXT, "candidate", "b08 参考音",
            "6.06 秒 / 4.29 字每秒；相似度 0.844。",
            sim_key="fp32:old_male_b08_angry",
        ),
        sample(
            "a_old_b08_vc", "老年男性 · b08 + Qwen+VC angry", vc / "vc_old_male_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "Qwen+VC",
            "5.64 秒 / 4.55 字每秒；相似度 0.901。",
            sim_key="vc:old_male_angry", track_id="old_angry_vc",
        ),
    ]

    consistency_narrator = [
        sample(
            "c_narrator_neutral", "旁白 · neutral（核心句）", fp32 / "narrator_neutral_clean.wav",
            CORE_TEXT, "candidate", "neutral",
            "5.04 秒 / 5.16 字每秒；与 narrator_03 参考音 0.887。",
            sim_key="fp32:narrator_neutral",
        ),
        sample(
            "c_narrator_angry", "旁白 · angry（Qwen+VC）", vc / "vc_narrator_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "angry",
            "与 neutral 的相似度 0.852（阶段 3 为 0.790）；与参考音 0.832。",
            sim_key="vc:narrator_angry", track_id="narrator_angry_vc",
        ),
        sample(
            "c_narrator_sad", "旁白 · sad（Qwen+VC）", vc / "vc_narrator_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "sad",
            "与 neutral 的相似度 0.862（阶段 3 为 0.811）；与参考音 0.916。",
            sim_key="vc:narrator_sad", track_id="narrator_sad_vc",
        ),
    ]

    consistency_young = [
        sample(
            "c_young_neutral", "年轻女性 · neutral（核心句）", fp32 / "young_female_neutral_clean.wav",
            CORE_TEXT, "candidate", "neutral",
            "5.11 秒 / 5.09 字每秒；与参考音 0.741。neutral 本身不算特别像参考音。",
            sim_key="fp32:young_female_neutral",
        ),
        sample(
            "c_young_angry", "年轻女性 · angry（Qwen+VC）", vc / "vc_young_female_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "angry",
            "与参考音 0.846（阶段 3 仅 0.516）；与 neutral 0.735。",
            sim_key="vc:young_female_angry", track_id="young_angry_vc",
        ),
        sample(
            "c_young_sad", "年轻女性 · sad（Qwen+VC）", vc / "vc_young_female_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "sad",
            "与参考音 0.933；与 neutral 0.781。",
            sim_key="vc:young_female_sad", track_id="young_sad_vc",
        ),
    ]

    consistency_old = [
        sample(
            "c_old_neutral", "老年男性 · neutral（b08 核心句）", fp32 / "old_male_b08_neutral_clean.wav",
            CORE_TEXT, "candidate", "neutral",
            "5.12 秒 / 5.08 字每秒；与 b08 参考音 0.838。",
            sim_key="fp32:old_male_b08_neutral", track_id="old_b08_neutral",
        ),
        sample(
            "c_old_angry", "老年男性 · angry（b08 + Qwen+VC）", vc / "vc_old_male_angry_qwen_s42.wav",
            ANGRY_TEXT, "vc", "angry",
            "与参考音 0.901；与 neutral 0.854。",
            sim_key="vc:old_male_angry", track_id="old_angry_vc",
        ),
        sample(
            "c_old_sad", "老年男性 · sad（b08 + Qwen+VC）", vc / "vc_old_male_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "sad",
            "与参考音 0.919；与 neutral 0.854。",
            sim_key="vc:old_male_sad", track_id="old_b08_sad_vc",
        ),
    ]

    tail_samples = [
        sample(
            "t_narrator_raw", "旁白 neutral · 原样", exp / "sel_narrator_neutral.wav",
            CORE_TEXT, "baseline", "原始",
            "未处理。句尾保留模型原始气声/噪声。",
            sim_key="stage3:narrator_neutral", show_tail=True,
        ),
        sample(
            "t_narrator_trim", "旁白 neutral · 硬切", tail / "trim" / "sel_narrator_neutral.wav",
            CORE_TEXT, "processed", "硬切",
            "检测最后一个周期性人声帧，保留 40ms，再 35ms 淡出截断。",
            sim_key="stage3:narrator_neutral", sim_approx=True, show_tail=True,
            note="移除约 270ms 尾部。",
        ),
        sample(
            "t_narrator_atten", "旁白 neutral · 衰减", tail / "attenuate" / "sel_narrator_neutral.wav",
            CORE_TEXT, "processed", "衰减",
            "不硬切，对最后一个人声帧之后的 220ms 做 -12dB 衰减。",
            sim_key="stage3:narrator_neutral", sim_approx=True, show_tail=True,
            note="衰减段约 220ms。",
        ),
        sample(
            "t_old_raw", "老年男性 neutral · 原样", exp / "old_b08_neutral.wav",
            CORE_TEXT, "baseline", "原始",
            "b08 参考音 + 中性指令的原始输出。",
            sim_key="exp:old_b08_neutral", show_tail=True,
        ),
        sample(
            "t_old_trim", "老年男性 neutral · 硬切", tail / "trim" / "old_b08_neutral.wav",
            CORE_TEXT, "processed", "硬切",
            "同一音频的硬切版本。",
            sim_key="exp:old_b08_neutral", sim_approx=True, show_tail=True,
            note="移除约 280ms 尾部。",
        ),
        sample(
            "t_old_atten", "老年男性 neutral · 衰减", tail / "attenuate" / "old_b08_neutral.wav",
            CORE_TEXT, "processed", "衰减",
            "同一音频的 -12dB 尾部衰减版本。",
            sim_key="exp:old_b08_neutral", sim_approx=True, show_tail=True,
            note="衰减段约 220ms。",
        ),
        sample(
            "t_vc_raw", "旁白 sad 疲惫 · 原样（气声明显）", vc_raw / "vc_narrator_sad_tired_qwen_s42.wav",
            SAD_TEXT, "warning", "原始",
            "这条原始尾音有较明显的气声，是本轮尾音处理的主要测试样本。",
            sim_key="vc:narrator_sad_tired", show_tail=True,
        ),
        sample(
            "t_vc_trim", "旁白 sad 疲惫 · 硬切", tail / "trim" / "vc_narrator_sad_tired_qwen_s42.wav",
            SAD_TEXT, "processed", "硬切",
            "同一音频的硬切版本。",
            sim_key="vc:narrator_sad_tired", sim_approx=True, show_tail=True,
            note="移除约 540ms 尾部。",
        ),
        sample(
            "t_vc_atten", "旁白 sad 疲惫 · 衰减", tail / "attenuate" / "vc_narrator_sad_tired_qwen_s42.wav",
            SAD_TEXT, "processed", "衰减",
            "同一音频的 -12dB 尾部衰减版本。",
            sim_key="vc:narrator_sad_tired", sim_approx=True, show_tail=True,
            note="衰减段约 220ms。",
        ),
    ]

    old_pace = [
        sample(
            "o_old03_ref", "参考音 · old_male_03（你选中的旧参考）", refs / "old_male_03.wav",
            "", "ref", "参考音",
            "19.14 秒 / 47 字，约 2.46 字每秒。参考音本身就慢，零样本会照抄这个节奏。",
            ref_cps=round(len(STAGE3_REF_TEXT) / 19.144, 2),
        ),
        sample(
            "o_b08_ref", "参考音 · new_old_male_b08（新参考）", refs / "new_old_male_b08.wav",
            "", "ref", "参考音",
            "7.93 秒 / 32 字，约 4.04 字每秒。正常语速，句尾 150ms 静音。",
            ref_cps=round(len(B08_REF_TEXT) / 7.93, 2),
        ),
        sample(
            "o_old03_neutral", "old03 · neutral（阶段 3）", exp / "sel_old_male_neutral.wav",
            CORE_TEXT, "baseline", "基线",
            "4.72 秒 / 5.51 字每秒。中性句其实不慢；问题集中在 sad 指令。",
            sim_key="stage3:old_male_neutral",
        ),
        sample(
            "o_old03_speed110", "old03 · neutral + 1.1x", exp / "sel_old_male_neutral_speed110.wav",
            CORE_TEXT, "candidate", "加速",
            "4.28 秒 / 6.07 字每秒。只加速，不换参考音。",
            sim_key="stage3:old_male_neutral", sim_approx=True, track_id="old03_neutral_speed110",
        ),
        sample(
            "o_b08_neutral", "b08 · neutral", fp32 / "old_male_b08_neutral_clean.wav",
            CORE_TEXT, "candidate", "新参考音",
            "5.12 秒 / 5.08 字每秒。换参考音后的中性句。",
            sim_key="fp32:old_male_b08_neutral", track_id="old_b08_neutral",
        ),
        sample(
            "o_old03_sad", "old03 · sad（阶段 3）", exp / "sel_old_male_sad.wav",
            CORE_TEXT, "warning", "旧 sad",
            "10.48 秒 / 2.48 字每秒。慢在 sad 上最明显。",
            sim_key="stage3:old_male_sad",
        ),
        sample(
            "o_b08_sad", "b08 · sad + 克制指令", exp / "old_b08_sad.wav",
            CORE_TEXT, "candidate", "新参考音",
            "9.16 秒 / 2.84 字每秒。仍然偏慢，说明只换参考音不够。",
            sim_key="exp:old_b08_sad",
        ),
        sample(
            "o_b08_vc_sad", "b08 · sad + Qwen+VC", vc / "vc_old_male_sad_qwen_s42.wav",
            SAD_TEXT, "vc", "Qwen+VC",
            "8.52 秒 / 3.80 字每秒。目前最接近正常语速的老年 sad。",
            sim_key="vc:old_male_sad", track_id="old_b08_sad_vc",
        ),
    ]

    references = [
        sample(
            "r_narrator", "旁白参考音 · narrator_03", refs / "narrator_03.wav",
            "", "ref", "参考音",
            "11.52 秒 / 47 字，约 4.08 字每秒。阶段 3 你选中的旁白。",
            ref_cps=round(len(STAGE3_REF_TEXT) / 11.52, 2),
        ),
        sample(
            "r_young", "年轻女性参考音 · young_female_01", refs / "young_female_01.wav",
            "", "ref", "参考音",
            "12.40 秒 / 47 字，约 3.79 字每秒。阶段 3 你选中的年轻女性。",
            ref_cps=round(len(STAGE3_REF_TEXT) / 12.4, 2),
        ),
        sample(
            "r_old03", "老年参考音 · old_male_03", refs / "old_male_03.wav",
            "", "ref", "参考音",
            "19.14 秒 / 47 字，约 2.46 字每秒。慢是参考音自带的。",
            ref_cps=round(len(STAGE3_REF_TEXT) / 19.144, 2),
        ),
        sample(
            "r_b08", "老年参考音 · new_old_male_b08", refs / "new_old_male_b08.wav",
            "", "ref", "参考音",
            "7.93 秒 / 32 字，约 4.04 字每秒。新的正常语速参考。",
            ref_cps=round(len(B08_REF_TEXT) / 7.93, 2),
        ),
    ]

    source_samples = [
        sample(
            "src_narrator_sad", "Qwen 源 · 旁白 sad（压抑）", sources / "narrator_sad_qwen_s42.wav",
            SAD_TEXT, "source", "Qwen 源音",
            "只负责情感韵律，不是最终角色音色。VC 之后才变成 narrator_03。",
        ),
        sample(
            "src_narrator_sad_natural", "Qwen 源 · 旁白 sad（自然）", sources / "narrator_sad_natural_qwen_s42.wav",
            SAD_TEXT, "source", "Qwen 源音",
            "更克制的悲伤源。",
        ),
        sample(
            "src_narrator_angry", "Qwen 源 · 旁白 angry（外放）", sources / "narrator_angry_qwen_s42.wav",
            ANGRY_TEXT, "source", "Qwen 源音",
            "外放愤怒源。",
        ),
        sample(
            "src_narrator_angry_cold", "Qwen 源 · 旁白 angry（冷怒）", sources / "narrator_angry_cold_qwen_s42.wav",
            ANGRY_TEXT, "source", "Qwen 源音",
            "冷怒源。",
        ),
        sample(
            "src_young_sad", "Qwen 源 · 年轻女性 sad", sources / "young_female_sad_qwen_s42.wav",
            SAD_TEXT, "source", "Qwen 源音",
            "VC 前是另一个音色；VC 后才是 young_female_01。",
        ),
        sample(
            "src_old_sad", "Qwen 源 · 老年男性 sad", sources / "old_male_sad_qwen_s42.wav",
            SAD_TEXT, "source", "Qwen 源音",
            "VC 前是另一个老年音色；VC 后转成 b08。",
        ),
    ]

    return [
        {
            "id": "quick",
            "title": "先听这 6 条",
            "desc": "如果只有 3 分钟，先听这组。覆盖本轮四个修复点：sad 语速、angry 身份、老年语速、尾音。",
            "groups": [{"title": "快速判断", "samples": quick}],
        },
        {
            "id": "sad",
            "title": "悲伤 A/B",
            "desc": "重点不是“有没有放慢”，而是“像不像真实难过”。旧 sad 指令把语速压到 2.5 字/秒；新方案在 3.6–5.1 字/秒之间。",
            "groups": [
                {"title": "旁白（narrator_03）", "samples": sad_narrator},
                {"title": "年轻女性（young_female_01）", "samples": sad_young},
                {"title": "老年男性（old03 / b08）", "samples": sad_old},
            ],
        },
        {
            "id": "angry",
            "title": "愤怒 A/B",
            "desc": "重点：愤怒时是否还是同一个人。阶段 3 的年轻女性 angry 与参考音相似度只有 0.516，是本轮最严重的身份崩坏。",
            "groups": [
                {"title": "旁白（narrator_03）", "samples": angry_narrator},
                {"title": "年轻女性（young_female_01）", "samples": angry_young},
                {"title": "老年男性（b08）", "samples": angry_old},
            ],
        },
        {
            "id": "consistency",
            "title": "跨情绪一致性",
            "desc": "同一角色三种情绪。neutral 是阶段 3B 核心句，angry/sad 是新句；文本不同，只判断音色是否像同一人。",
            "groups": [
                {"title": "旁白", "samples": consistency_narrator},
                {"title": "年轻女性", "samples": consistency_young},
                {"title": "老年男性（b08）", "samples": consistency_old},
            ],
        },
        {
            "id": "tail",
            "title": "句尾气声 A/B",
            "desc": "同一音频的三种处理：原样 / 硬切 + 35ms 淡出 / 末段 -12dB 衰减。重点：还有没有 qi/si/ci 气声，硬切是否突兀。",
            "groups": [
                {"title": "旁白 neutral", "samples": tail_samples[:3]},
                {"title": "老年男性 neutral（b08）", "samples": tail_samples[3:6]},
                {"title": "旁白 sad 疲惫（气声明显）", "samples": tail_samples[6:]},
            ],
        },
        {
            "id": "old_pace",
            "title": "老年男性语速",
            "desc": "先听两条参考音，再听生成。old03 参考音本身 2.46 字/秒，b08 是 4.04 字/秒；sad 又被旧指令进一步拖慢。",
            "groups": [{"title": "参考音与生成对照", "samples": old_pace}],
        },
        {
            "id": "references",
            "title": "参考音",
            "desc": "零样本音色的上限由参考音决定。参考音的语速、尾音和情绪会被模型继承。",
            "groups": [{"title": "四条参考音", "samples": references}],
        },
        {
            "id": "sources",
            "title": "Qwen 源音（诊断用）",
            "desc": "这些不是最终角色音色。把它们与 VC 结果对比，可以判断“情感是 Qwen 给的，音色是 VC 转的”这条链路是否成立。",
            "groups": [{"title": "非最终音色", "samples": source_samples}],
            "collapsed": True,
        },
    ]


def fmt_seconds(ms: int) -> str:
    return f"{ms / 1000:.2f}s"


def card_html(item: dict, page_base: Path) -> str:
    stats = audio_stats(page_base / "audition_audio" / item["audio"])
    duration = stats["duration_ms"]
    cps = None
    if item["text"]:
        cps = round(len(item["text"]) / (duration / 1000), 2) if duration else None
    elif item.get("ref_cps"):
        cps = item["ref_cps"]
    sim = item.get("sim")
    meta = [f"<span>{fmt_seconds(duration)}</span>"]
    if cps:
        meta.append(f"<span>{cps:.2f} 字/秒</span>")
    if sim is not None:
        prefix = "音色≈" if item["sim_approx"] else "音色"
        meta.append(
            f'<span class="metric" title="与目标参考音的说话人嵌入余弦相似度；辅助指标，不替代试听">'
            f"{prefix} {sim:.3f}</span>"
        )
    if item["show_tail"] and stats["tail_200ms_hf_ratio"] is not None:
        meta.append(
            f'<span class="metric" title="最后 200ms 的 4kHz 以上能量占比；仅作辅助，静音段的数值不稳定">'
            f'尾HF {stats["tail_200ms_hf_ratio"]:.4f}</span>'
        )
        meta.append(
            f'<span class="metric" title="最后 200ms 的 RMS 电平">尾RMS {stats["tail_200ms_rms_dbfs"]:.1f}dB</span>'
        )
    note_html = f'<p class="note">{html.escape(item["note"])}</p>' if item["note"] else ""
    return f"""
    <article class="sample tone-{item['tone']}" data-track="{html.escape(item['track_id'])}">
      <div class="sample-head">
        <div>
          <h4>{html.escape(item['title'])}</h4>
          <span class="badge badge-{item['tone']}">{html.escape(item['badge'])}</span>
        </div>
        <button class="heard" type="button" aria-pressed="false">标记已听</button>
      </div>
      <div class="meta">{''.join(meta)}</div>
      <audio controls preload="none" src="audition_audio/{html.escape(item['audio'])}"></audio>
      <p class="desc">{html.escape(item['desc'])}</p>
      {note_html}
    </article>"""


def group_html(group: dict, page_base: Path) -> str:
    cards = "".join(card_html(item, page_base) for item in group["samples"])
    return f"""
    <div class="group">
      <div class="group-head">
        <h3>{html.escape(group['title'])}</h3>
        <button class="play-group" type="button" aria-pressed="false">顺序连播</button>
      </div>
      <div class="cards">{cards}</div>
    </div>"""


def section_html(section: dict, page_base: Path, index: int) -> str:
    groups = "".join(group_html(group, page_base) for group in section["groups"])
    if section.get("collapsed"):
        return f"""
    <details class="panel" id="{section['id']}">
      <summary>{index:02d} · {html.escape(section['title'])}（点击展开）</summary>
      <div class="details-body">
        <p class="section-desc">{html.escape(section['desc'])}</p>
        {groups}
      </div>
    </details>"""
    return f"""
    <section class="panel" id="{section['id']}">
      <div class="section-head">
        <div>
          <span class="section-no">{index:02d}</span>
          <h2>{html.escape(section['title'])}</h2>
        </div>
      </div>
      <p class="section-desc">{html.escape(section['desc'])}</p>
      {groups}
    </section>"""


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>NovelVoice Studio · 阶段 3B 聚焦试听</title>
<style>
  :root {
    --bg: #080e1a;
    --panel: #0f172a;
    --panel-2: #111c33;
    --line: #24324d;
    --text: #e6edf7;
    --muted: #93a4bd;
    --accent: #38bdf8;
    --green: #34d399;
    --amber: #fbbf24;
    --red: #fb7185;
    --purple: #c084fc;
    --teal: #2dd4bf;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scroll-padding-top: 82px; }
  body {
    margin: 0;
    background: radial-gradient(1200px 600px at 20% -10%, #16294a 0%, var(--bg) 55%);
    color: var(--text);
    font: 15px/1.65 "Segoe UI", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif;
  }
  a { color: var(--accent); }
  .top {
    position: sticky; top: 0; z-index: 20;
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 14px 28px;
    background: rgba(8, 14, 26, 0.88);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--line);
  }
  .top h1 { margin: 0; font-size: 18px; letter-spacing: 0.02em; }
  .top p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
  .top-right { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  .progress { color: var(--muted); font-size: 13px; white-space: nowrap; }
  .progress strong { color: var(--green); font-size: 16px; }
  .jump {
    padding: 7px 12px; border: 1px solid var(--line); border-radius: 8px;
    color: var(--text); text-decoration: none; font-size: 13px; background: var(--panel);
  }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 24px 28px 100px; }
  .hero {
    display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px;
    margin-bottom: 18px;
  }
  .hero-main, .hero-side {
    background: linear-gradient(180deg, rgba(22, 36, 62, 0.94), rgba(15, 23, 42, 0.94));
    border: 1px solid var(--line); border-radius: 14px; padding: 20px 22px;
  }
  .hero h2 { margin: 0 0 8px; font-size: 22px; }
  .hero p { margin: 6px 0; color: var(--muted); }
  .pill {
    display: inline-block; padding: 3px 9px; border-radius: 999px;
    border: 1px solid var(--line); color: var(--muted); font-size: 12px; margin-right: 6px;
  }
  .pill.bad { color: var(--red); border-color: rgba(251, 113, 133, 0.35); background: rgba(251, 113, 133, 0.08); }
  .pill.good { color: var(--green); border-color: rgba(52, 211, 153, 0.35); background: rgba(52, 211, 153, 0.08); }
  .pill.warn { color: var(--amber); border-color: rgba(251, 191, 36, 0.35); background: rgba(251, 191, 36, 0.08); }
  .hero-side h3 { margin: 0 0 8px; font-size: 15px; }
  .hero-side ul { margin: 0; padding-left: 18px; color: var(--muted); font-size: 13px; }
  .hero-side li { margin: 5px 0; }
  .callout {
    margin: 14px 0 20px; padding: 14px 18px; border-radius: 12px;
    border: 1px solid rgba(56, 189, 248, 0.28);
    background: rgba(56, 189, 248, 0.06); color: #cbe8f8; font-size: 13px;
  }
  .callout strong { color: #fff; }
  .panel {
    background: rgba(15, 23, 42, 0.82);
    border: 1px solid var(--line); border-radius: 16px;
    padding: 22px 24px; margin: 22px 0;
  }
  .section-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .section-head > div { display: flex; align-items: baseline; gap: 10px; }
  .section-no {
    font: 700 12px/1 "Consolas", monospace; color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 6px; padding: 5px 7px;
  }
  .panel h2 { margin: 0; font-size: 20px; }
  .section-desc { color: var(--muted); margin: 8px 0 16px; max-width: 980px; font-size: 13px; }
  .group { margin: 18px 0 22px; }
  .group-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
  .group-head h3 { margin: 0; font-size: 15px; color: #cbd5e1; font-weight: 600; }
  .play-group {
    border: 1px solid var(--line); background: var(--panel-2); color: var(--text);
    border-radius: 8px; padding: 6px 12px; font-size: 12px; cursor: pointer;
  }
  .play-group:hover { border-color: var(--accent); }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 12px; }
  .sample {
    background: var(--panel-2); border: 1px solid var(--line); border-left: 3px solid var(--line);
    border-radius: 12px; padding: 14px; min-width: 0;
  }
  .sample-head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
  .sample-head h4 { margin: 0 0 5px; font-size: 14px; line-height: 1.45; }
  .badge {
    display: inline-block; font-size: 10px; letter-spacing: 0.04em;
    padding: 2px 7px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted);
  }
  .tone-baseline { border-left-color: #64748b; }
  .tone-candidate { border-left-color: var(--accent); }
  .tone-vc { border-left-color: var(--purple); }
  .tone-warning { border-left-color: var(--red); }
  .tone-ref { border-left-color: var(--green); }
  .tone-source { border-left-color: var(--amber); }
  .tone-processed { border-left-color: var(--teal); }
  .badge-baseline { color: #cbd5e1; }
  .badge-candidate { color: var(--accent); border-color: rgba(56, 189, 248, 0.4); }
  .badge-vc { color: var(--purple); border-color: rgba(192, 132, 252, 0.4); }
  .badge-warning { color: var(--red); border-color: rgba(251, 113, 133, 0.4); }
  .badge-ref { color: var(--green); border-color: rgba(52, 211, 153, 0.4); }
  .badge-source { color: var(--amber); border-color: rgba(251, 191, 36, 0.4); }
  .badge-processed { color: var(--teal); border-color: rgba(45, 212, 191, 0.4); }
  .meta { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 10px; }
  .meta span {
    font: 12px/1 "Consolas", "Segoe UI", monospace; color: #b8c7dd;
    background: rgba(148, 163, 184, 0.09); padding: 4px 6px; border-radius: 5px;
  }
  .meta .metric { color: #86d7f7; }
  audio { width: 100%; height: 36px; margin: 2px 0 8px; }
  .desc { margin: 6px 0 0; color: #b6c4d8; font-size: 12.5px; }
  .note { margin: 6px 0 0; color: var(--amber); font-size: 12px; }
  .heard {
    flex: 0 0 auto; border: 1px solid var(--line); background: transparent; color: var(--muted);
    border-radius: 7px; padding: 4px 8px; font-size: 11px; cursor: pointer; white-space: nowrap;
  }
  .heard[aria-pressed="true"] { color: var(--green); border-color: rgba(52, 211, 153, 0.45); background: rgba(52, 211, 153, 0.08); }
  details.panel { padding: 0; }
  details.panel > summary {
    list-style: none; cursor: pointer; padding: 18px 24px; font-weight: 600; font-size: 16px;
  }
  details.panel > summary::-webkit-details-marker { display: none; }
  details.panel > summary::after { content: "+"; float: right; color: var(--muted); font-size: 20px; line-height: 1; }
  details.panel[open] > summary::after { content: "−"; }
  details.panel .details-body { padding: 0 24px 22px; }
  .table-wrap { overflow-x: auto; margin: 10px 0 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; min-width: 620px; }
  th, td { border-bottom: 1px solid var(--line); padding: 8px 10px; text-align: left; }
  th { color: var(--muted); font-weight: 600; }
  td.num { font-family: "Consolas", monospace; }
  .num.bad { color: var(--red); }
  .num.good { color: var(--green); }
  .num.mid { color: var(--amber); }
  .score-box {
    background: linear-gradient(180deg, rgba(22, 36, 62, 0.9), rgba(15, 23, 42, 0.95));
    border: 1px solid rgba(56, 189, 248, 0.28); border-radius: 16px; padding: 22px 24px; margin-top: 26px;
  }
  .score-box h2 { margin: 0 0 6px; font-size: 20px; }
  .score-box > p { color: var(--muted); margin: 0 0 16px; font-size: 13px; }
  .q { padding: 14px 0; border-top: 1px solid var(--line); }
  .q-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .q-head strong { font-size: 14px; }
  .q-head .hint { color: var(--muted); font-size: 12px; }
  .scale { display: flex; gap: 6px; margin: 10px 0 4px; flex-wrap: wrap; }
  .scale label { cursor: pointer; }
  .scale input { position: absolute; opacity: 0; pointer-events: none; }
  .scale span {
    display: inline-flex; align-items: center; justify-content: center;
    width: 34px; height: 30px; border-radius: 7px; border: 1px solid var(--line);
    background: #0b1324; color: #b8c7dd; font: 12px "Consolas", monospace;
  }
  .scale label:hover span { border-color: var(--accent); }
  .scale input:checked + span { background: var(--accent); color: #04121f; border-color: var(--accent); font-weight: 700; }
  .scale-ends { display: flex; justify-content: space-between; color: #7f91aa; font-size: 11px; max-width: 420px; }
  .choice-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
  .choice { cursor: pointer; }
  .choice input { margin-right: 6px; accent-color: var(--accent); }
  .choice span { color: #c7d3e4; font-size: 12.5px; }
  .prefs { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 16px; }
  .pref { background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
  .pref h4 { margin: 0 0 4px; font-size: 13px; }
  .pref .hint { color: var(--muted); font-size: 11.5px; }
  textarea {
    width: 100%; min-height: 110px; margin-top: 10px; resize: vertical;
    background: #0b1324; color: var(--text); border: 1px solid var(--line);
    border-radius: 9px; padding: 10px 12px; font: 13px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif;
  }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
  .btn {
    border: 1px solid var(--line); background: var(--panel-2); color: var(--text);
    border-radius: 9px; padding: 9px 14px; cursor: pointer; font-size: 13px;
  }
  .btn.primary { background: var(--accent); color: #04121f; border-color: var(--accent); font-weight: 700; }
  .btn.danger { color: var(--red); border-color: rgba(251, 113, 133, 0.35); }
  .save-status { color: var(--green); font-size: 12px; }
  .loop { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 12px; }
  .loop input { accent-color: var(--accent); }
  .footer { color: #6f819a; font-size: 11.5px; margin-top: 26px; text-align: center; }
  @media (max-width: 900px) {
    .hero { grid-template-columns: 1fr; }
    .top { position: static; flex-wrap: wrap; }
    .wrap { padding: 16px 14px 80px; }
    .cards { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<header class="top">
  <div>
    <h1>NovelVoice Studio · 阶段 3B 聚焦试听</h1>
    <p>gate 尚未通过 · 针对跨情绪 / sad 表现力 / 尾音 / 老年语速重做</p>
  </div>
  <div class="top-right">
    <label class="loop"><input type="checkbox" id="loopToggle" /> 单曲循环</label>
    <span class="progress"><strong id="heardCount">0</strong>/<span id="heardTotal">0</span> 已听</span>
    <a class="jump" href="#scoring">去评分</a>
  </div>
</header>

<main class="wrap">
  <div class="hero">
    <div class="hero-main">
      <h2>先别急着打分：这一轮只验证四个问题</h2>
      <p><span class="pill bad">跨情绪 1/10</span><span class="pill bad">sad 1/10</span><span class="pill warn">尾音备注</span><span class="pill warn">老年拖慢</span></p>
      <p>上一轮失败的核心不是“音色不够多”，而是 <strong>同一角色换情绪后不像同一个人</strong>，以及 <strong>sad 靠放慢来表演</strong>。本轮所有样本都围绕这两个问题，加上尾音和老年语速。</p>
      <p>建议按顺序听：先听“先听这 6 条”，再听 sad、angry、跨情绪；最后用尾音 A/B 和老年语速做裁决。</p>
    </div>
    <div class="hero-side">
      <h3>本轮新增了什么</h3>
      <ul>
        <li>sad 指令改为“语速正常，不要逐字拖慢”，并加 1.1x 对照。</li>
        <li>Qwen 源生成情感韵律 + CosyVoice VC 转回角色音色。</li>
        <li>句尾硬切 / -12dB 衰减两版，直接 A/B。</li>
        <li>老年男性换 b08 正常语速参考音；先听参考音本身。</li>
      </ul>
    </div>
  </div>

  <div class="callout">
    <strong>使用说明：</strong>所有音频只做复制，未做响度归一化；请用同一音量、耳机试听。数字（字/秒、音色相似度、尾HF）只是辅助，不能替代你的听感。Qwen+VC 是实验路径，生成速度约 3–5 倍实时，不适合当前实时预览；如果听感明显更好，后续可以改为预生成。不同章节的文本不同，跨情绪段落只比较“是否像同一人”，不比较字/秒。
  </div>

  <div class="callout">
    <strong>关键发现：</strong>old_male_03 参考音本身是 19.14 秒 / 47 字，约 <strong>2.46 字/秒</strong>；b08 参考音是 7.93 秒 / 32 字，约 <strong>4.04 字/秒</strong>。零样本会继承参考音的节奏，所以“老年声音故意拖慢”至少有相当一部分来自参考音，而不是模型凭空表演。但 sad 指令仍会把 b08 拖到 2.84 字/秒，说明指令也需要改。
  </div>

  <section class="panel">
    <div class="section-head"><div><span class="section-no">00</span><h2>关键数字</h2></div></div>
    <p class="section-desc">说话人嵌入余弦相似度，只用于定位问题，不用于排名。重点看“与参考音”是否在换情绪后塌掉。</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>角色 / 情绪</th><th>阶段 3 / fp32 对照 · 与参考音</th><th>Qwen+VC · 与参考音</th><th>阶段 3 / fp32 · 与本人 neutral</th><th>Qwen+VC · 与本人 neutral</th></tr></thead>
        <tbody>
          <tr><td>旁白 angry</td><td class="num mid">0.740</td><td class="num good">0.832 / 0.896（冷怒）</td><td class="num">0.790</td><td class="num good">0.852</td></tr>
          <tr><td>年轻女性 angry</td><td class="num bad">0.516</td><td class="num good">0.846</td><td class="num">0.766</td><td class="num mid">0.735</td></tr>
          <tr><td>老年男性 angry（b08）</td><td class="num mid">0.844</td><td class="num good">0.901</td><td class="num">0.847</td><td class="num good">0.854</td></tr>
          <tr><td>旁白 sad</td><td class="num mid">0.860</td><td class="num good">0.916</td><td class="num mid">0.811</td><td class="num good">0.862</td></tr>
          <tr><td>年轻女性 sad</td><td class="num mid">0.802</td><td class="num good">0.933</td><td class="num mid">0.762</td><td class="num good">0.781</td></tr>
          <tr><td>老年男性 sad（b08）</td><td class="num mid">0.874</td><td class="num good">0.919</td><td class="num mid">0.863</td><td class="num good">0.854</td></tr>
        </tbody>
      </table>
    </div>
    <p class="section-desc">读法：年轻女性 angry 的 0.510（fp32）→ 0.846 是本轮最大的单点修复；旁白 angry 也从 0.740 提升到 0.832/0.896。表中旁白/年轻女性使用 fp32 同条件对照，阶段 3 fp16 的年轻女性 angry 为 0.516；b08 是阶段 3B 新参考音，没有阶段 3 值。相似度不是越高越好，最终以“像不像同一个人”的听感为准。</p>
  </section>

  __SECTIONS__

  <section class="score-box" id="scoring">
    <h2>阶段 3B 评分</h2>
    <p>1 = 差，10 = 好；只有“句尾气声/杂音”和“Qwen+VC 变声痕迹”是反向描述，已在题目标注。页面不会自动判定 gate，结论由你选择。</p>
    <div id="questions"></div>
    <div class="prefs" id="prefs"></div>
    <div class="q" style="margin-top:16px">
      <div class="q-head"><strong>gate 结论</strong><span class="hint">由你决定；本页不自动判定</span></div>
      <div class="choice-row" id="gateChoices">
        <label class="choice"><input type="radio" name="gate" value="pass" /> <span>通过，可以进入阶段 3 应用实现</span></label>
        <label class="choice"><input type="radio" name="gate" value="fail" /> <span>不通过，需要继续修音质</span></label>
        <label class="choice"><input type="radio" name="gate" value="another" /> <span>再做一轮聚焦实验</span></label>
      </div>
    </div>
    <div class="q">
      <div class="q-head"><strong>备注</strong><span class="hint">哪条最像 / 哪条最假 / 是否还有 qi si ci 气声</span></div>
      <textarea id="notes" placeholder="例如：sad 自然版最像真人；年轻女性 angry 的 VC 版仍有变声痕迹；b08 neutral 可以，sad 还是慢……"></textarea>
    </div>
    <div class="actions">
      <button class="btn primary" id="copyBtn" type="button">复制评分 JSON</button>
      <button class="btn" id="downloadBtn" type="button">下载评分 JSON</button>
      <button class="btn danger" id="clearBtn" type="button">清空本页评分</button>
      <span class="save-status" id="saveStatus"></span>
    </div>
    <p class="footer">页面生成时间：__GENERATED_AT__ · 条目：__SAMPLE_COUNT__ · 音频目录：audition_audio/ · 本地文件，无遥测</p>
  </section>
</main>

<script>
const QUESTIONS = __QUESTIONS_JSON__;
const PREFS = __PREFS_JSON__;
const PAGE_META = __PAGE_META_JSON__;
const SCORE_KEY = "nvs_stage3b_scores_v1";
const HEARD_KEY = "nvs_stage3b_heard_v1";
const LOOP_KEY = "nvs_stage3b_loop_v1";

function safeGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function safeSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) {}
}
function safeRemove(key) {
  try { localStorage.removeItem(key); } catch (e) {}
}

const questionsEl = document.getElementById("questions");
QUESTIONS.forEach((q, index) => {
  const wrap = document.createElement("div");
  wrap.className = "q";
  const head = document.createElement("div");
  head.className = "q-head";
  head.innerHTML = "<strong>" + (index + 1) + ". " + q.label + "</strong><span class='hint'>" + q.hint + "</span>";
  wrap.appendChild(head);
  const scale = document.createElement("div");
  scale.className = "scale";
  for (let value = 1; value <= 10; value++) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = q.id;
    input.value = String(value);
    const span = document.createElement("span");
    span.textContent = String(value);
    label.appendChild(input);
    label.appendChild(span);
    scale.appendChild(label);
  }
  wrap.appendChild(scale);
  const ends = document.createElement("div");
  ends.className = "scale-ends";
  ends.innerHTML = "<span>" + q.low + "</span><span>" + q.high + "</span>";
  wrap.appendChild(ends);
  questionsEl.appendChild(wrap);
});

const prefsEl = document.getElementById("prefs");
PREFS.forEach((pref) => {
  const box = document.createElement("div");
  box.className = "pref";
  const title = document.createElement("h4");
  title.textContent = pref.label;
  box.appendChild(title);
  const hint = document.createElement("div");
  hint.className = "hint";
  hint.textContent = pref.hint;
  box.appendChild(hint);
  const row = document.createElement("div");
  row.className = "choice-row";
  pref.options.forEach((opt) => {
    const label = document.createElement("label");
    label.className = "choice";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = pref.id;
    input.value = opt.value;
    const span = document.createElement("span");
    span.textContent = opt.label;
    label.appendChild(input);
    label.appendChild(span);
    row.appendChild(label);
  });
  box.appendChild(row);
  prefsEl.appendChild(box);
});

function collectScores() {
  const scores = {};
  QUESTIONS.forEach((q) => {
    const checked = document.querySelector("input[name='" + q.id + "']:checked");
    scores[q.id] = checked ? Number(checked.value) : null;
  });
  const preferences = {};
  PREFS.forEach((p) => {
    const checked = document.querySelector("input[name='" + p.id + "']:checked");
    preferences[p.id] = checked ? checked.value : null;
  });
  const gate = document.querySelector("input[name='gate']:checked");
  return {
    scores,
    preferences,
    gate_conclusion: gate ? gate.value : null,
    notes: document.getElementById("notes").value.trim(),
  };
}

function applyScores(data) {
  if (!data) return;
  Object.entries(data.scores || {}).forEach(([key, value]) => {
    if (value === null) return;
    const input = document.querySelector("input[name='" + key + "'][value='" + value + "']");
    if (input) input.checked = true;
  });
  Object.entries(data.preferences || {}).forEach(([key, value]) => {
    if (!value) return;
    const input = document.querySelector("input[name='" + key + "'][value='" + value + "']");
    if (input) input.checked = true;
  });
  if (data.gate_conclusion) {
    const input = document.querySelector("input[name='gate'][value='" + data.gate_conclusion + "']");
    if (input) input.checked = true;
  }
  if (data.notes) document.getElementById("notes").value = data.notes;
}

const rawSaved = safeGet(SCORE_KEY);
if (rawSaved) {
  try { applyScores(JSON.parse(rawSaved)); } catch (e) {}
}

const saveStatus = document.getElementById("saveStatus");
let saveTimer = null;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const data = collectScores();
    data.saved_at = new Date().toISOString();
    safeSet(SCORE_KEY, JSON.stringify(data));
    saveStatus.textContent = "已保存到本机";
    setTimeout(() => { saveStatus.textContent = ""; }, 1400);
  }, 250);
}

document.addEventListener("change", (event) => {
  if (event.target.matches("input[type=radio], textarea")) scheduleSave();
});
document.getElementById("notes").addEventListener("input", scheduleSave);

const heard = new Set();
try {
  const stored = JSON.parse(safeGet(HEARD_KEY) || "[]");
  if (Array.isArray(stored)) stored.forEach((id) => heard.add(id));
} catch (e) {}

const cards = Array.from(document.querySelectorAll(".sample"));
const validTracks = new Set(cards.map((card) => card.dataset.track));
heard.forEach((id) => { if (!validTracks.has(id)) heard.delete(id); });
const totalUnique = validTracks.size;
document.getElementById("heardTotal").textContent = String(totalUnique);

function updateHeard() {
  cards.forEach((card) => {
    const active = heard.has(card.dataset.track);
    const button = card.querySelector(".heard");
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.textContent = active ? "已听" : "标记已听";
  });
  document.getElementById("heardCount").textContent = String(heard.size);
  safeSet(HEARD_KEY, JSON.stringify(Array.from(heard)));
}
updateHeard();

cards.forEach((card) => {
  const button = card.querySelector(".heard");
  button.addEventListener("click", () => {
    const id = card.dataset.track;
    if (heard.has(id)) heard.delete(id); else heard.add(id);
    updateHeard();
  });
});

let groupToken = 0;
let groupPlaybackAudio = null;
function resetGroupButtons() {
  document.querySelectorAll(".play-group").forEach((button) => {
    button.dataset.playing = "0";
    button.textContent = "顺序连播";
  });
}
function stopPlayback() {
  groupToken += 1;
  groupPlaybackAudio = null;
  document.querySelectorAll("audio").forEach((audio) => audio.pause());
  resetGroupButtons();
}

const loopToggle = document.getElementById("loopToggle");
let loopEnabled = safeGet(LOOP_KEY) === "1";
loopToggle.checked = loopEnabled;
function applyLoop() {
  document.querySelectorAll("audio").forEach((audio) => {
    audio.loop = loopEnabled && audio !== groupPlaybackAudio;
  });
}
applyLoop();
loopToggle.addEventListener("change", () => {
  loopEnabled = loopToggle.checked;
  safeSet(LOOP_KEY, loopEnabled ? "1" : "0");
  applyLoop();
});

const allAudios = Array.from(document.querySelectorAll("audio"));
allAudios.forEach((audio) => {
  audio.addEventListener("play", () => {
    allAudios.forEach((other) => {
      if (other !== audio) other.pause();
    });
    if (audio === groupPlaybackAudio) {
      applyLoop();
      return;
    }
    groupToken += 1;
    groupPlaybackAudio = null;
    resetGroupButtons();
    applyLoop();
  });
  audio.addEventListener("ended", () => {
    const card = audio.closest(".sample");
    if (card) {
      heard.add(card.dataset.track);
      updateHeard();
    }
  });
});

document.querySelectorAll(".play-group").forEach((button) => {
  button.addEventListener("click", () => {
    const group = button.closest(".group");
    if (!group) return;
    if (button.dataset.playing === "1") {
      stopPlayback();
      return;
    }
    stopPlayback();
    const audios = Array.from(group.querySelectorAll("audio"));
    if (!audios.length) return;
    const token = groupToken;
    button.dataset.playing = "1";
    button.textContent = "停止连播";
    let index = 0;
    const step = () => {
      if (token !== groupToken) return;
      if (index >= audios.length) {
        groupPlaybackAudio = null;
        applyLoop();
        resetGroupButtons();
        return;
      }
      const audio = audios[index++];
      groupPlaybackAudio = audio;
      audio.loop = false;
      audio.play().catch(() => {
        groupPlaybackAudio = null;
        applyLoop();
        resetGroupButtons();
      });
      audio.onended = () => {
        if (token !== groupToken) return;
        const card = audio.closest(".sample");
        if (card) {
          heard.add(card.dataset.track);
          updateHeard();
        }
        step();
      };
    };
    step();
  });
});

function buildPayload() {
  const data = collectScores();
  return {
    page: "stage3b",
    version: "3B",
    saved_at: new Date().toISOString(),
    gate_conclusion: data.gate_conclusion,
    scores: data.scores,
    preferences: data.preferences,
    notes: data.notes,
    heard: Array.from(heard),
    page_generated_at: PAGE_META.generated_at,
    sample_count: PAGE_META.sample_count,
    metric_source: PAGE_META.metric_source,
  };
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  document.body.removeChild(area);
  return Promise.resolve();
}

const copyBtn = document.getElementById("copyBtn");
copyBtn.addEventListener("click", () => {
  copyText(JSON.stringify(buildPayload(), null, 2)).then(() => {
    copyBtn.textContent = "已复制";
    setTimeout(() => { copyBtn.textContent = "复制评分 JSON"; }, 1600);
  }).catch(() => {
    copyBtn.textContent = "复制失败，请用下载";
    setTimeout(() => { copyBtn.textContent = "复制评分 JSON"; }, 2200);
  });
});

const downloadBtn = document.getElementById("downloadBtn");
downloadBtn.addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(buildPayload(), null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "stage3b_listening_scores.json";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(link.href);
});

document.getElementById("clearBtn").addEventListener("click", () => {
  if (!confirm("清空本页评分与已听标记？此操作不可撤销。")) return;
  safeRemove(SCORE_KEY);
  safeRemove(HEARD_KEY);
  heard.clear();
  document.querySelectorAll("input[type=radio]").forEach((input) => { input.checked = false; });
  document.getElementById("notes").value = "";
  updateHeard();
  saveStatus.textContent = "已清空";
  setTimeout(() => { saveStatus.textContent = ""; }, 1600);
});
</script>
</body>
</html>
"""


QUESTIONS = [
    {
        "id": "consistency",
        "label": "跨情绪一致性",
        "hint": "同一角色 neutral / angry / sad 是否像同一个人",
        "low": "不像同一人",
        "high": "完全同一人",
    },
    {
        "id": "sad",
        "label": "sad 表现力",
        "hint": "是否像真实难过，而不是单纯放慢、压低",
        "low": "只是变慢",
        "high": "真实难过",
    },
    {
        "id": "tail",
        "label": "句尾气声 / 杂音",
        "hint": "1 = 很多气声，10 = 干净（注意方向）",
        "low": "很多气声",
        "high": "干净",
    },
    {
        "id": "old_pace",
        "label": "老年男性语速",
        "hint": "是否自然，不刻意拖慢",
        "low": "故意拖慢",
        "high": "自然",
    },
    {
        "id": "pronunciation",
        "label": "中文发音与杂音",
        "hint": "多音字、吞字、金属音、爆音",
        "low": "差",
        "high": "好",
    },
    {
        "id": "vc_artifacts",
        "label": "Qwen+VC 变声痕迹",
        "hint": "是否机械、发闷、失真；没听出来给高分",
        "low": "明显",
        "high": "听不出",
    },
    {
        "id": "timbre_diff",
        "label": "三音色差异",
        "hint": "旁白 / 年轻女性 / 老年男性是否清楚区分",
        "low": "分不清",
        "high": "清楚",
    },
    {
        "id": "overall",
        "label": "整体可用性",
        "hint": "是否愿意让这种质量进入应用",
        "low": "不可用",
        "high": "可用",
    },
]

PREFS = [
    {
        "id": "sad_pref",
        "label": "sad 最像真人",
        "hint": "可选“都不行”",
        "options": [
            {"value": "cosy_c", "label": "CosyVoice 克制"},
            {"value": "cosy_speed110", "label": "克制 + 1.1x"},
            {"value": "qwen_vc", "label": "Qwen+VC 压抑"},
            {"value": "qwen_natural", "label": "Qwen+VC 自然"},
            {"value": "none", "label": "都不行"},
        ],
    },
    {
        "id": "angry_pref",
        "label": "angry 最像同一人",
        "hint": "重点听年轻女性",
        "options": [
            {"value": "cosy", "label": "CosyVoice 新指令"},
            {"value": "qwen_vc", "label": "Qwen+VC 外放"},
            {"value": "qwen_cold", "label": "Qwen+VC 冷怒"},
            {"value": "none", "label": "都不行"},
        ],
    },
    {
        "id": "old_pref",
        "label": "老年男性首选",
        "hint": "音色 + 语速一起判断",
        "options": [
            {"value": "old03", "label": "old03 原样"},
            {"value": "old03_speed110", "label": "old03 + 1.1x"},
            {"value": "b08", "label": "b08 新参考音"},
            {"value": "b08_vc", "label": "b08 + Qwen+VC"},
            {"value": "none", "label": "都不行"},
        ],
    },
    {
        "id": "tail_pref",
        "label": "尾音处理",
        "hint": "硬切可能突兀；衰减可能仍有气声",
        "options": [
            {"value": "trim", "label": "硬切更好"},
            {"value": "attenuate", "label": "衰减更好"},
            {"value": "raw", "label": "原样更好"},
            {"value": "none", "label": "都有问题"},
        ],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成阶段 3B 试听包")
    parser.add_argument("--stage3b-dir", type=Path, default=DEFAULT_STAGE3B_DIR)
    args = parser.parse_args()

    base = args.stage3b_dir.resolve()
    sections = build_sections(base)
    audio_dir = base / "audition_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = base / "audition_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    to_ref = metrics.get("to_reference", {})

    counter = 0
    manifest_sections = []
    for section in sections:
        manifest_groups = []
        for group in section["groups"]:
            manifest_samples = []
            for item in group["samples"]:
                if not item["src"].exists():
                    raise SystemExit(f"缺少音频: {item['src']}")
                counter += 1
                filename = f"{counter:02d}_{item['id']}.wav"
                shutil.copy2(item["src"], audio_dir / filename)
                item["audio"] = filename
                item["sim"] = to_ref.get(item["sim_key"]) if item["sim_key"] else None
                stats = audio_stats(audio_dir / filename)
                item["duration_ms"] = stats["duration_ms"]
                item["cps"] = (
                    round(len(item["text"]) / (stats["duration_ms"] / 1000), 2)
                    if item["text"] and stats["duration_ms"]
                    else item.get("ref_cps")
                )
                item["tail_200ms_hf_ratio"] = stats["tail_200ms_hf_ratio"]
                item["tail_200ms_rms_dbfs"] = stats["tail_200ms_rms_dbfs"]
                manifest_samples.append(
                    {
                        "id": item["id"],
                        "track_id": item["track_id"],
                        "title": item["title"],
                        "source": str(item["src"]),
                        "audio": filename,
                        "tone": item["tone"],
                        "badge": item["badge"],
                        "duration_ms": item["duration_ms"],
                        "cps": item["cps"],
                        "similarity_to_reference": item["sim"],
                        "similarity_approximate": item["sim_approx"],
                        "tail_200ms_hf_ratio": item["tail_200ms_hf_ratio"],
                        "tail_200ms_rms_dbfs": item["tail_200ms_rms_dbfs"],
                        "text": item["text"],
                        "desc": item["desc"],
                        "note": item["note"],
                    }
                )
            manifest_groups.append({"title": group["title"], "samples": manifest_samples})
        manifest_sections.append(
            {
                "id": section["id"],
                "title": section["title"],
                "desc": section["desc"],
                "groups": manifest_groups,
            }
        )

    unique_tracks = {item["track_id"] for section in sections for group in section["groups"] for item in group["samples"]}
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3B 聚焦试听：跨情绪一致性、sad 表现力、句尾气声、老年语速",
        "status": "gate 尚未通过；本包等待人工复听",
        "source_stage3b": str(base),
        "audio_dir": str(audio_dir),
        "sample_count": counter,
        "unique_track_count": len(unique_tracks),
        "metric_source": str(metrics_path) if metrics_path.exists() else None,
        "sections": manifest_sections,
    }
    manifest_path = base / "audition_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    page_meta = {
        "generated_at": generated_at,
        "sample_count": counter,
        "metric_source": "audition_metrics.json（CosyVoice Campplus 说话人嵌入）",
    }
    sections_html = "".join(
        section_html(section, base, index)
        for index, section in enumerate(sections, start=1)
    )

    page = (
        HTML_TEMPLATE.replace("__SECTIONS__", sections_html)
        .replace("__QUESTIONS_JSON__", json.dumps(QUESTIONS, ensure_ascii=False))
        .replace("__PREFS_JSON__", json.dumps(PREFS, ensure_ascii=False))
        .replace("__PAGE_META_JSON__", json.dumps(page_meta, ensure_ascii=False))
        .replace("__GENERATED_AT__", generated_at)
        .replace("__SAMPLE_COUNT__", str(counter))
    )
    output = base / "audition.html"
    output.write_text(page, encoding="utf-8")

    print(f"[DONE] {counter} 条音频 -> {audio_dir}")
    print(f"[DONE] manifest -> {manifest_path}")
    print(f"[DONE] 页面 -> {output}")
    print("[HUMAN GATE] 请按“先听这 6 条”顺序试听，再评分；本页不自动判定通过。")


if __name__ == "__main__":
    main()