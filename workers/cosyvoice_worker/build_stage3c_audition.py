"""阶段 3C 试听包：句中气声/齿音 A/B。

只复制已生成的 WAV，不做额外处理；生成单文件 HTML 与 manifest。
用法（仓库根目录）：
  workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/build_stage3c_audition.py
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

STAGE3C = Path.home() / "Documents" / "NovelVoiceStudio" / "cache" / "poc_output" / "stage3c"
VC_AB = STAGE3C / "vc_ab"
OUT = STAGE3C / "audition"
AUDIO = OUT / "audition_audio"
TEXT = "我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。"
CHARS = 27


def spectral_hf_p95(path: Path) -> float:
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1).astype(np.float64)
    n_fft = 1024
    hop = 256
    window = np.hanning(n_fft)
    count = max(1, 1 + (mono.size - n_fft) // hop)
    ratios = []
    for index in range(count):
        seg = mono[index * hop : index * hop + n_fft]
        if seg.size < n_fft:
            seg = np.pad(seg, (0, n_fft - seg.size))
        spec = np.abs(np.fft.rfft(seg * window)) ** 2
        total = float(np.sum(spec)) + 1e-18
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
        band = (freqs >= 4000) & (freqs < 10000)
        rms = float(np.sqrt(np.mean(np.square(seg))))
        if rms > 1e-4:
            ratios.append(float(np.sum(spec[band]) / total))
    if not ratios:
        return 0.0
    return round(float(np.percentile(ratios, 95)), 3)


def entry(
    sample_id: str,
    title: str,
    rel: str,
    group: str,
    badge: str,
    desc: str,
    *,
    pref_value: str = "",
    track_id: str = "",
) -> dict:
    src = VC_AB / rel
    if not src.exists():
        raise FileNotFoundError(src)
    samples, sample_rate = sf.read(str(src), dtype="float32", always_2d=True)
    duration_ms = int(round(samples.shape[0] / sample_rate * 1000))
    return {
        "id": sample_id,
        "track_id": track_id or sample_id,
        "title": title,
        "src": src,
        "file": f"{sample_id}.wav",
        "group": group,
        "badge": badge,
        "desc": desc,
        "pref_value": pref_value,
        "duration_ms": duration_ms,
        "cps": round(CHARS / (duration_ms / 1000), 2),
        "hf_p95": spectral_hf_p95(src),
    }


def build_entries() -> list[dict]:
    return [
        entry(
            "n1_vc_neutral_raw",
            "中性 · Qwen+VC 原始",
            "vc_neutral_s42/vc_neutral_s42_raw.wav",
            "neutral",
            "当前链路",
            "不做任何后处理。重点听“久 / 今 / 三”和 qi/zi/ci/si/shi/in 类字是否有刮玻璃感。",
            pref_value="vc_neutral_s42_raw",
            track_id="vc_neutral_s42",
        ),
        entry(
            "n2_vc_neutral_refdeess",
            "中性 · 参考音先去齿音",
            "vc_neutral_s42_refdeess/vc_neutral_s42_refdeess_raw.wav",
            "neutral",
            "参考音处理",
            "先把 narrator_03 参考音的高频齿音压一点，再做 VC。输出端不再处理。",
            pref_value="vc_neutral_s42_refdeess_raw",
            track_id="vc_neutral_s42_refdeess",
        ),
        entry(
            "n3_vc_neutral_deess_g1",
            "中性 · 输出动态去齿音 -5dB",
            "vc_neutral_s42/vc_neutral_s42_deess_g1.wav",
            "neutral",
            "输出端处理",
            "只在 4-10kHz 占比异常高的帧上衰减，最多 -5dB；正常元音不受影响。",
            pref_value="vc_neutral_s42_deess_g1",
            track_id="vc_neutral_s42_deess_g1",
        ),
        entry(
            "n4_vc_neutral_deess_extreme",
            "中性 · 只削最刺的帧 -8dB",
            "vc_neutral_s42/vc_neutral_s42_deess_extreme.wav",
            "neutral",
            "定向处理",
            "阈值提高到 0.40，只处理最像“刮玻璃”的少数帧，最多 -8dB。听是否还有漏网的，以及有没有变闷。",
            pref_value="vc_neutral_s42_deess_extreme",
            track_id="vc_neutral_s42_deess_extreme",
        ),
        entry(
            "n5_vc_neutral_refdeess_shelf",
            "中性 · 参考去齿 + 高频 -2dB",
            "vc_neutral_s42_refdeess/vc_neutral_s42_refdeess_shelf4k_2db.wav",
            "neutral",
            "组合处理",
            "参考音先去齿，输出再整体把 4kHz 以上压 2dB。对比 n2，判断是否过闷。",
            pref_value="vc_neutral_s42_refdeess_shelf4k_2db",
            track_id="vc_neutral_s42_refdeess_shelf4k_2db",
        ),
        entry(
            "n6_instruct_neutral",
            "中性 · CosyVoice instruct2 原生",
            "instruct_neutral/instruct_neutral_raw.wav",
            "neutral",
            "原生对照",
            "不经 Qwen 源。语速更快（约 5.9 字/秒），齿音指标最低，但音色相似度略低。",
            pref_value="instruct_neutral_raw",
            track_id="instruct_neutral",
        ),
        entry(
            "n7_zero_neutral",
            "中性 · CosyVoice zero-shot",
            "zero_neutral/zero_neutral_raw.wav",
            "neutral",
            "原生对照",
            "直接用参考音做零样本。语速偏慢（约 3.4 字/秒），听自然度和齿音。",
            pref_value="zero_neutral_raw",
            track_id="zero_neutral",
        ),
        entry(
            "s1_vc_sad_raw",
            "悲伤 · Qwen+VC 原始",
            "vc_sad_s42/vc_sad_s42_raw.wav",
            "sad",
            "当前链路",
            "用户已选的 sad 方向。先确认毛刺是否主要集中在“久 / 今 / 三”。",
            pref_value="vc_sad_s42_raw",
            track_id="vc_sad_s42",
        ),
        entry(
            "s2_vc_sad_deess_g0",
            "悲伤 · 输出温和去齿音 -3dB",
            "vc_sad_s42/vc_sad_s42_deess_g0.wav",
            "sad",
            "轻处理",
            "最大只减 3dB，尽量只削刺耳峰值，保留悲伤语气。",
            pref_value="vc_sad_s42_deess_g0",
            track_id="vc_sad_s42_deess_g0",
        ),
        entry(
            "s3_vc_sad_refdeess",
            "悲伤 · 参考音先去齿音",
            "vc_sad_s42_refdeess/vc_sad_s42_refdeess_raw.wav",
            "sad",
            "参考音处理",
            "与 s1 同一条 Qwen 源，只换去齿音后的参考音。听音色是否仍然像 narrator_03。",
            pref_value="vc_sad_s42_refdeess_raw",
            track_id="vc_sad_s42_refdeess",
        ),
        entry(
            "s4_vc_sad_refdeess_shelf",
            "悲伤 · 参考去齿 + 高频 -2dB",
            "vc_sad_s42_refdeess/vc_sad_s42_refdeess_shelf4k_2db.wav",
            "sad",
            "组合处理",
            "参考去齿后再整体压 2dB 高频。判断悲伤语气是否被压闷。",
            pref_value="vc_sad_s42_refdeess_shelf4k_2db",
            track_id="vc_sad_s42_refdeess_shelf4k_2db",
        ),
        entry(
            "s5_vc_sad_deess_breath",
            "悲伤 · 2.5-8kHz 气声抑制",
            "vc_sad_s42/vc_sad_s42_deess_breath.wav",
            "sad",
            "气声处理",
            "不只削 4-10kHz 齿音，还把 2.5-8kHz 的气声压最多 5dB。听是否更干净，或开始发闷。",
            pref_value="vc_sad_s42_deess_breath",
            track_id="vc_sad_s42_deess_breath",
        ),
    ]


HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>阶段 3C · 句中气声/齿音 A/B</title>
<style>
  :root { color-scheme: dark; --bg:#101418; --panel:#181f26; --panel2:#1f2831; --line:#2c3a45; --text:#e7edf2; --muted:#93a4b3; --accent:#4cc2ff; --warn:#ffb454; --good:#6ee7a8; --bad:#ff7b7b; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; line-height:1.6; }
  header { position:sticky; top:0; z-index:10; background:rgba(16,20,24,.96); border-bottom:1px solid var(--line); padding:14px 22px; backdrop-filter:blur(8px); }
  header h1 { margin:0; font-size:18px; letter-spacing:.02em; }
  header .sub { color:var(--muted); font-size:13px; margin-top:2px; }
  .progress { margin-left:auto; color:var(--muted); font-size:12px; }
  .wrap { max-width:1180px; margin:0 auto; padding:22px; }
  .notice { background:linear-gradient(135deg,#1c2833,#182028); border:1px solid var(--line); border-left:3px solid var(--accent); padding:14px 16px; border-radius:8px; margin-bottom:18px; }
  .notice strong { color:var(--accent); }
  .notice p { margin:6px 0 0; color:#c9d5df; font-size:14px; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; color:var(--muted); font-size:12px; margin-top:8px; }
  h2 { font-size:16px; margin:24px 0 10px; color:#dbe7ef; border-bottom:1px solid var(--line); padding-bottom:6px; }
  .group-note { color:var(--muted); font-size:13px; margin:-4px 0 12px; }
  .sample { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:10px 0; }
  .sample.played { border-color:#3f6f8a; }
  .sample.picked { border-color:var(--good); box-shadow:0 0 0 1px rgba(110,231,168,.25) inset; }
  .sample-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .sample-head h3 { margin:0; font-size:15px; }
  .badge { font-size:11px; padding:2px 7px; border-radius:999px; background:#22303b; color:#9fc6dd; border:1px solid #304454; white-space:nowrap; }
  .meta { color:var(--muted); font-size:12px; margin-left:auto; white-space:nowrap; }
  .desc { color:#b9c7d3; font-size:13px; margin:6px 0 10px; }
  audio { width:100%; height:34px; }
  .row { display:flex; align-items:center; gap:10px; margin-top:8px; flex-wrap:wrap; }
  button { background:#24323d; color:#dbe7ef; border:1px solid #35495a; border-radius:6px; padding:5px 10px; cursor:pointer; font-size:12px; }
  button:hover { background:#2c3e4c; }
  button.primary { background:#1d4d66; border-color:#2f789c; }
  button.good { background:#1c3d2c; border-color:#2f6e4d; }
  .q { background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:12px 0; }
  .q h3 { margin:0 0 4px; font-size:14px; }
  .q .hint { color:var(--muted); font-size:12px; margin-bottom:10px; }
  .scale { display:flex; gap:6px; flex-wrap:wrap; }
  .scale label { display:flex; flex-direction:column; align-items:center; gap:2px; font-size:11px; color:var(--muted); }
  .scale input { accent-color:var(--accent); }
  select, textarea { width:100%; background:#121a21; color:var(--text); border:1px solid #334654; border-radius:6px; padding:8px; font-size:13px; font-family:inherit; }
  textarea { min-height:90px; resize:vertical; }
  .checks { display:flex; gap:8px 16px; flex-wrap:wrap; font-size:13px; color:#c6d3dd; }
  .checks label { display:flex; gap:5px; align-items:center; }
  .footer { color:var(--muted); font-size:12px; padding:16px 0 40px; }
  .details { margin:18px 0; }
  details { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 14px; }
  summary { cursor:pointer; color:#cfe2ee; }
  table { width:100%; border-collapse:collapse; font-size:12px; margin-top:8px; }
  th, td { text-align:left; padding:5px 6px; border-bottom:1px solid #25333e; }
  th { color:var(--muted); font-weight:500; }
  .toast { position:fixed; right:18px; bottom:18px; background:#1f4d35; color:#d9ffe8; border:1px solid #2f7d52; padding:8px 12px; border-radius:6px; font-size:13px; opacity:0; transform:translateY(8px); transition:.2s; pointer-events:none; }
  .toast.show { opacity:1; transform:none; }
  @media (max-width:720px) { .meta { margin-left:0; width:100%; } header { padding:12px 14px; } .wrap { padding:14px; } }
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:14px;max-width:1180px;margin:0 auto;">
    <div>
      <h1>阶段 3C · 句中气声 / 齿音 A/B</h1>
      <div class="sub">同一句“我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。”，只改生成链路与高频处理。</div>
    </div>
    <div class="progress" id="progress">已听 0 / __TOTAL__</div>
  </div>
</header>
<div class="wrap">
  <div class="notice">
    <strong>听音重点</strong>
    <p>请重点听“<b>久</b>”“<b>今</b>”“<b>三</b>”，以及 qi / zi / ci / si / shi / in 类字。判断标准不是“高频多不多”，而是是否还有<b>刮玻璃、漏气、嘶嘶</b>的毛刺；同时注意有没有被削得发闷、咬字发虚。</p>
    <div class="legend"><span>n1 = 当前 Qwen+VC 原始</span><span>n2 / s3 = 只处理参考音</span><span>n3 / s2 = 输出动态去齿音</span><span>n5 / s4 = 组合处理</span><span>n6 / n7 = CosyVoice 原生对照</span></div>
  </div>

  __SECTIONS__

  <h2>评分与选择</h2>
  <div class="q">
    <h3>1. 句中毛刺 / 刮玻璃感</h3>
    <div class="hint">1 = 非常明显，10 = 基本干净。请以本轮最好的版本为准。</div>
    <div class="scale" data-key="sibilance"></div>
  </div>
  <div class="q">
    <h3>2. 整体自然度</h3>
    <div class="hint">1 = 机械、发闷或明显处理痕迹，10 = 接近正常录音。</div>
    <div class="scale" data-key="naturalness"></div>
  </div>
  <div class="q">
    <h3>3. 音色一致性</h3>
    <div class="hint">1 = 不像 narrator_03，10 = 仍是同一个人。</div>
    <div class="scale" data-key="identity"></div>
  </div>
  <div class="q">
    <h3>4. 中性句首选</h3>
    <div class="hint">选一个你愿意作为旁白中性句默认的版本。</div>
    <select id="neutral_pref">
      <option value="">— 请选择 —</option>
      <option value="vc_neutral_s42_raw">n1 Qwen+VC 原始</option>
      <option value="vc_neutral_s42_refdeess_raw">n2 参考音先去齿</option>
      <option value="vc_neutral_s42_deess_g1">n3 输出动态去齿 -5dB</option>
      <option value="vc_neutral_s42_deess_extreme">n4 只削最刺的帧 -8dB</option>
      <option value="vc_neutral_s42_refdeess_shelf4k_2db">n5 参考去齿 + 高频 -2dB</option>
      <option value="instruct_neutral_raw">n6 CosyVoice instruct2</option>
      <option value="zero_neutral_raw">n7 CosyVoice zero-shot</option>
      <option value="none">都不行</option>
    </select>
  </div>
  <div class="q">
    <h3>5. 悲伤句首选</h3>
    <div class="hint">在保持悲伤语气的前提下，哪个毛刺最少。</div>
    <select id="sad_pref">
      <option value="">— 请选择 —</option>
      <option value="vc_sad_s42_raw">s1 Qwen+VC 原始</option>
      <option value="vc_sad_s42_deess_g0">s2 温和去齿 -3dB</option>
      <option value="vc_sad_s42_refdeess_raw">s3 参考音先去齿</option>
      <option value="vc_sad_s42_refdeess_shelf4k_2db">s4 参考去齿 + 高频 -2dB</option>
      <option value="vc_sad_s42_deess_breath">s5 2.5-8kHz 气声抑制</option>
      <option value="none">都不行</option>
    </select>
  </div>
  <div class="q">
    <h3>6. 哪些字仍然有问题</h3>
    <div class="hint">可多选。用于下一轮只针对这些音节做实验。</div>
    <div class="checks" id="problem_chars">
      <label><input type="checkbox" value="久"> 久</label>
      <label><input type="checkbox" value="今"> 今</label>
      <label><input type="checkbox" value="三"> 三</label>
      <label><input type="checkbox" value="七"> 七</label>
      <label><input type="checkbox" value="自"> 自</label>
      <label><input type="checkbox" value="次"> 次</label>
      <label><input type="checkbox" value="四"> 四</label>
      <label><input type="checkbox" value="声"> 声</label>
      <label><input type="checkbox" value="阴"> 阴</label>
      <label><input type="checkbox" value="其他"> 其他</label>
    </div>
  </div>
  <div class="q">
    <h3>7. 备注</h3>
    <div class="hint">例如“n3 干净但发闷”“s3 音色变了”“某个字还是刺”。</div>
    <textarea id="notes" placeholder="写下你的听感..."></textarea>
  </div>

  <div class="row" style="margin:16px 0;">
    <button class="primary" id="copy">复制评分 JSON</button>
    <button id="download">下载评分 JSON</button>
    <button id="reset">清空本页记录</button>
    <span id="save_state" class="meta" style="margin-left:0;"></span>
  </div>

  <div class="details">
    <details>
      <summary>客观指标（4-10kHz 能量占比 p95，仅供参考，不等于听感）</summary>
      <table>
        <thead><tr><th>样本</th><th>时长</th><th>字/秒</th><th>HF p95</th></tr></thead>
        <tbody>__METRICS__</tbody>
      </table>
      <p class="group-note">HF p95 越低通常越不刺耳，但过低也会发闷；请以试听为准。n6 / n7 不是同一链路，不能只按数字比较。</p>
    </details>
  </div>

  <div class="footer">
    阶段 3C · 数据来源：stage3c/qwen_sources + stage3c/vc_ab。页面只读本地 WAV，无外网依赖。<br>
    生成时间：__GENERATED_AT__
  </div>
</div>
<div class="toast" id="toast">已保存</div>
<script>
const STORAGE_KEY = "nvs_stage3c_scores_v1";
const HEARD_KEY = "nvs_stage3c_heard_v1";
const samples = __SAMPLES__;
const cards = Array.from(document.querySelectorAll(".sample"));
const heard = new Set(JSON.parse(localStorage.getItem(HEARD_KEY) || "[]"));
const validIds = new Set(cards.map((card) => card.dataset.id));
for (const id of Array.from(heard)) if (!validIds.has(id)) heard.delete(id);

function saveHeard() {
  localStorage.setItem(HEARD_KEY, JSON.stringify(Array.from(heard)));
  document.getElementById("progress").textContent = `已听 ${heard.size} / ${validIds.size}`;
}
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 1800);
}
function markPlayed(card) {
  card.classList.add("played");
  heard.add(card.dataset.id);
  saveHeard();
}
cards.forEach((card) => {
  const audio = card.querySelector("audio");
  audio.addEventListener("play", () => markPlayed(card));
  const pick = card.querySelector("[data-pick]");
  if (pick) {
    pick.addEventListener("click", () => {
      const group = card.dataset.group;
      const select = document.getElementById(group === "neutral" ? "neutral_pref" : "sad_pref");
      select.value = card.dataset.pref;
      select.dispatchEvent(new Event("change"));
      document.querySelectorAll(".sample.picked").forEach((el) => el.classList.remove("picked"));
      card.classList.add("picked");
      toast("已选为" + (group === "neutral" ? "中性" : "悲伤") + "首选");
    });
  }
});
const scales = document.querySelectorAll(".scale");
scales.forEach((scale) => {
  const key = scale.dataset.key;
  for (let i = 1; i <= 10; i += 1) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = key;
    input.value = String(i);
    label.appendChild(input);
    label.appendChild(document.createTextNode(String(i)));
    scale.appendChild(label);
  }
});
function collect() {
  const scores = {};
  scales.forEach((scale) => {
    const checked = scale.querySelector("input:checked");
    if (checked) scores[scale.dataset.key] = Number(checked.value);
  });
  return {
    page: "stage3c",
    version: "3C",
    saved_at: new Date().toISOString(),
    text: __TEXT_JSON__,
    scores,
    preferences: {
      neutral_pref: document.getElementById("neutral_pref").value || null,
      sad_pref: document.getElementById("sad_pref").value || null
    },
    problem_chars: Array.from(document.querySelectorAll("#problem_chars input:checked")).map((el) => el.value),
    notes: document.getElementById("notes").value.trim(),
    heard: Array.from(heard),
    sample_count: samples.length,
    page_generated_at: __GENERATED_JSON__
  };
}
function restore() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;
  try {
    const data = JSON.parse(raw);
    Object.entries(data.scores || {}).forEach(([key, value]) => {
      const input = document.querySelector(`input[name="${key}"][value="${value}"]`);
      if (input) input.checked = true;
    });
    if (data.preferences) {
      document.getElementById("neutral_pref").value = data.preferences.neutral_pref || "";
      document.getElementById("sad_pref").value = data.preferences.sad_pref || "";
    }
    (data.problem_chars || []).forEach((value) => {
      const input = document.querySelector(`#problem_chars input[value="${value}"]`);
      if (input) input.checked = true;
    });
    document.getElementById("notes").value = data.notes || "";
    document.getElementById("save_state").textContent = "已恢复上次记录";
  } catch (error) { console.warn(error); }
}
function persist() {
  const data = collect();
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  document.getElementById("save_state").textContent = "已自动保存 " + new Date().toLocaleTimeString();
  return data;
}
document.querySelectorAll("input, select, textarea").forEach((el) => {
  el.addEventListener("change", persist);
  el.addEventListener("input", () => {
    if (el.tagName === "TEXTAREA") persist();
  });
});
document.getElementById("copy").addEventListener("click", async () => {
  const data = persist();
  try {
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast("评分 JSON 已复制");
  } catch (error) {
    toast("复制失败，请用下载按钮");
  }
});
document.getElementById("download").addEventListener("click", () => {
  const data = persist();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "stage3c_listening_result.json";
  link.click();
  URL.revokeObjectURL(url);
});
document.getElementById("reset").addEventListener("click", () => {
  if (!confirm("清空本页的评分、首选、备注和已听记录？")) return;
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(HEARD_KEY);
  location.reload();
});
saveHeard();
restore();
</script>
</body>
</html>
"""


def build_sections(entries: list[dict]) -> str:
    blocks = []
    for group, title, note in [
        ("neutral", "中性句：先解决“久 / 今”一类毛刺", "同一 Qwen 中性源只改参考音或输出端处理；n6 / n7 是不同链路，用来判断是否必须依赖 Qwen。"),
        ("sad", "悲伤句：保持语气，只削毛刺", "s1 是当前方向。s2-s5 是四种抑制思路，重点判断是否保留了悲伤的“紧”和“压”。"),
    ]:
        items = [item for item in entries if item["group"] == group]
        cards = []
        for item in items:
            cards.append(
                f"""
  <div class="sample" data-id="{item['id']}" data-group="{group}" data-pref="{item['pref_value']}" data-track="{item['track_id']}">
    <div class="sample-head">
      <h3>{item['title']}</h3>
      <span class="badge">{item['badge']}</span>
      <span class="meta">{item['duration_ms'] / 1000:.2f}s · {item['cps']:.2f} 字/秒</span>
    </div>
    <div class="desc">{item['desc']}</div>
    <audio controls preload="metadata" src="audition_audio/{item['file']}"></audio>
    <div class="row"><button data-pick>选它</button><span class="meta" style="margin-left:0;">{item['id']}</span></div>
  </div>"""
            )
        blocks.append(
            f"<h2>{title}</h2><div class=\"group-note\">{note}</div>{''.join(cards)}"
        )
    return "\n".join(blocks)


def build_metrics(entries: list[dict]) -> str:
    rows = []
    for item in entries:
        rows.append(
            f"<tr><td>{item['title']}</td><td>{item['duration_ms'] / 1000:.2f}s</td>"
            f"<td>{item['cps']:.2f}</td><td>{item['hf_p95']:.3f}</td></tr>"
        )
    return "".join(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)
    entries = build_entries()
    for item in entries:
        target = AUDIO / item["file"]
        shutil.copy2(item["src"], target)
        item.pop("src")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        HTML_TEMPLATE.replace("__SECTIONS__", build_sections(entries))
        .replace("__METRICS__", build_metrics(entries))
        .replace("__TOTAL__", str(len(entries)))
        .replace("__SAMPLES__", json.dumps(entries, ensure_ascii=False))
        .replace("__TEXT_JSON__", json.dumps(TEXT, ensure_ascii=False))
        .replace("__GENERATED_JSON__", json.dumps(generated_at, ensure_ascii=False))
        .replace("__GENERATED_AT__", generated_at)
    )
    html_path = OUT / "audition.html"
    html_path.write_text(html, encoding="utf-8")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "阶段 3C 句中气声/齿音 A/B 试听包",
        "text": TEXT,
        "char_count": CHARS,
        "entries": entries,
    }
    (OUT / "audition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {html_path}")
    print(f"[DONE] {AUDIO}，{len(entries)} 个音频")
    for item in entries:
        print(f"  {item['id']:<28} {item['duration_ms']}ms cps={item['cps']} hf95={item['hf_p95']}")


if __name__ == "__main__":
    main()