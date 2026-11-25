"""阶段 3C 最后一轮试听包：只针对“菜 / 三”的 ci 感。

输入：
  - stage3c/vc_ab 中已选定的 baseline
  - stage3c/final_try 中 6 条新 VC
  - final_try 中两条擦音收短

输出：
  stage3c/final_audition/audition.html
不覆盖已有 stage3c/audition。
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
FINAL_TRY = STAGE3C / "final_try"
OUT = STAGE3C / "final_audition"
AUDIO = OUT / "audition_audio"
TEXT = "我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。"
CHARS = 27


def hf_p95(path: Path) -> float:
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
    return round(float(np.percentile(ratios, 95)), 3) if ratios else 0.0


def entry(
    sample_id: str,
    title: str,
    path: Path,
    group: str,
    badge: str,
    desc: str,
    pref_value: str,
) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    duration_ms = int(round(samples.shape[0] / sample_rate * 1000))
    return {
        "id": sample_id,
        "title": title,
        "src": path,
        "file": f"{sample_id}.wav",
        "group": group,
        "badge": badge,
        "desc": desc,
        "pref_value": pref_value,
        "duration_ms": duration_ms,
        "cps": round(CHARS / (duration_ms / 1000), 2),
        "hf_p95": hf_p95(path),
    }


def build_entries() -> list[dict]:
    return [
        entry(
            "n0_baseline",
            "当前定稿 · 参考音先去齿",
            VC_AB / "vc_neutral_s42_refdeess" / "vc_neutral_s42_refdeess_raw.wav",
            "neutral",
            "基准",
            "上一轮你选定的中性版本。先听“菜 / 三”作为对比基准。",
            "keep_neutral_refdeess",
        ),
        entry(
            "n1_seed123",
            "换 Qwen seed 123",
            FINAL_TRY / "neutral_s123_refg1" / "neutral_s123_refg1_raw.wav",
            "neutral",
            "换源",
            "参考音处理不变，只换一条 Qwen 中性源。口型与字音可能不同，重点听“菜 / 三”是否改善。",
            "neutral_s123_refg1",
        ),
        entry(
            "n2_refg2",
            "参考音强去齿 -8dB",
            FINAL_TRY / "neutral_s42_refg2" / "neutral_s42_refg2_raw.wav",
            "neutral",
            "更强参考处理",
            "同一 Qwen 源，参考音按更低阈值、最多 -8dB 去齿。判断“菜 / 三”是否更轻，以及音色是否变薄。",
            "neutral_s42_refg2",
        ),
        entry(
            "n3_refg2_out",
            "强参考去齿 + 输出温和去齿",
            FINAL_TRY / "neutral_s42_refg2_outg0" / "neutral_s42_refg2_outg0_deess_g0.wav",
            "neutral",
            "双重处理",
            "参考音强去齿，输出再最多 -3dB。可能更干净，也可能发闷；听处理痕迹。",
            "neutral_s42_refg2_outg0",
        ),
        entry(
            "n4_short80",
            "只收短最长的擦音",
            FINAL_TRY / "neutral_s42_refg1_shorten" / "neutral_s42_refg1_shorten_shorten.wav",
            "neutral",
            "擦音收短",
            "保留擦音开头 55ms，之后 40ms 内压到 -10dB；只处理超过 80ms 的擦音。主要影响“三”。",
            "neutral_refdeess_short80",
        ),
        entry(
            "n5_short40",
            "收短所有明显擦音（更激进）",
            FINAL_TRY / "neutral_refdeess_shorten40" / "neutral_refdeess_shorten40.wav",
            "neutral",
            "更激进",
            "超过 40ms 的擦音都收短到约 45ms，最多压 -8dB；会同时影响“菜”和“三”。听咬字是否被切。",
            "neutral_refdeess_short40",
        ),
        entry(
            "s0_baseline",
            "当前定稿 · 悲伤参考音先去齿",
            VC_AB / "vc_sad_s42_refdeess" / "vc_sad_s42_refdeess_raw.wav",
            "sad",
            "基准",
            "上一轮你选定的悲伤版本。先听“菜 / 三”，再判断是否值得换。",
            "keep_sad_refdeess",
        ),
        entry(
            "s1_seed123",
            "悲伤 · 换 Qwen seed 123",
            FINAL_TRY / "sad_s123_refg1" / "sad_s123_refg1_raw.wav",
            "sad",
            "换源",
            "参考音处理不变，换悲伤源。句子稍长（7.44s），听语气和字音是否更自然。",
            "sad_s123_refg1",
        ),
        entry(
            "s2_refg2",
            "悲伤 · 参考音强去齿 -8dB",
            FINAL_TRY / "sad_s42_refg2" / "sad_s42_refg2_raw.wav",
            "sad",
            "更强参考处理",
            "与 s0 同一条 Qwen 源，只加强参考音去齿。判断悲伤语气是否保留。",
            "sad_s42_refg2",
        ),
    ]


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>阶段 3C 终选 · 菜 / 三 的 ci 感</title>
<style>
  :root { color-scheme: dark; --bg:#101418; --panel:#181f26; --panel2:#1f2831; --line:#2c3a45; --text:#e7edf2; --muted:#93a4b3; --accent:#4cc2ff; --good:#6ee7a8; --warn:#ffb454; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; line-height:1.65; }
  header { position:sticky; top:0; z-index:10; background:rgba(16,20,24,.96); border-bottom:1px solid var(--line); padding:14px 22px; backdrop-filter:blur(8px); }
  header h1 { margin:0; font-size:18px; letter-spacing:.02em; }
  header .sub { color:var(--muted); font-size:13px; margin-top:2px; }
  .progress { margin-left:auto; color:var(--muted); font-size:12px; white-space:nowrap; }
  .wrap { max-width:1100px; margin:0 auto; padding:22px; }
  .notice { background:linear-gradient(135deg,#1c2833,#182028); border:1px solid var(--line); border-left:3px solid var(--accent); padding:14px 16px; border-radius:8px; margin-bottom:18px; }
  .notice strong { color:var(--accent); }
  .notice p { margin:6px 0 0; color:#c9d5df; font-size:14px; }
  .legend { display:flex; gap:14px; flex-wrap:wrap; color:var(--muted); font-size:12px; margin-top:8px; }
  h2 { font-size:16px; margin:24px 0 10px; color:#dbe7ef; border-bottom:1px solid var(--line); padding-bottom:6px; }
  .group-note { color:var(--muted); font-size:13px; margin:-4px 0 12px; }
  .sample { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:10px 0; }
  .sample.played { border-color:#3f6f8a; }
  .sample.picked { border-color:var(--good); box-shadow:0 0 0 1px rgba(110,231,168,.25) inset; }
  .sample.baseline { border-left:3px solid var(--warn); }
  .sample-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .sample-head h3 { margin:0; font-size:15px; }
  .badge { font-size:11px; padding:2px 7px; border-radius:999px; background:#22303b; color:#9fc6dd; border:1px solid #304454; white-space:nowrap; }
  .badge.base { background:#3d2f1c; color:#ffcf8a; border-color:#6b552f; }
  .meta { color:var(--muted); font-size:12px; margin-left:auto; white-space:nowrap; }
  .desc { color:#b9c7d3; font-size:13px; margin:6px 0 10px; }
  audio { width:100%; height:34px; }
  .row { display:flex; align-items:center; gap:10px; margin-top:8px; flex-wrap:wrap; }
  button { background:#24323d; color:#dbe7ef; border:1px solid #35495a; border-radius:6px; padding:5px 10px; cursor:pointer; font-size:12px; }
  button:hover { background:#2c3e4c; }
  button.primary { background:#1d4d66; border-color:#2f789c; }
  .q { background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:12px 0; }
  .q h3 { margin:0 0 4px; font-size:14px; }
  .q .hint { color:var(--muted); font-size:12px; margin-bottom:10px; }
  .scale { display:flex; gap:6px; flex-wrap:wrap; }
  .scale label { display:flex; flex-direction:column; align-items:center; gap:2px; font-size:11px; color:var(--muted); }
  .scale input { accent-color:var(--accent); }
  select, textarea { width:100%; background:#121a21; color:var(--text); border:1px solid #334654; border-radius:6px; padding:8px; font-size:13px; font-family:inherit; }
  textarea { min-height:90px; resize:vertical; }
  .footer { color:var(--muted); font-size:12px; padding:16px 0 40px; }
  details { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin-top:18px; }
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
  <div style="display:flex;align-items:center;gap:14px;max-width:1100px;margin:0 auto;">
    <div>
      <h1>阶段 3C 终选 · “菜 / 三”的 ci 感</h1>
      <div class="sub">同一句“我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。”，只针对最后一句做最后一轮尝试。</div>
    </div>
    <div class="progress" id="progress">已听 0 / __TOTAL__</div>
  </div>
</header>
<div class="wrap">
  <div class="notice">
    <strong>怎么听</strong>
    <p>先播放 <b>n0 / s0</b> 熟悉当前定稿，再逐条对比。核心只有一句：<b>“我把饭菜热了三遍”里的“菜”和“三”，还像不像 ci？</b> 其次判断有没有为了压齿音而发闷、咬字被切、音色变薄。n4 / n5 是擦音收短，副作用最需要留意。</p>
    <div class="legend"><span>n0 / s0 = 当前定稿（基准）</span><span>n1–n3 = 换源或参考音处理</span><span>n4–n5 = 擦音收短</span></div>
  </div>

  __SECTIONS__

  <h2>评分与终选</h2>
  <div class="q">
    <h3>1. “菜 / 三”的 ci 感</h3>
    <div class="hint">1 = 仍明显像 ci，10 = 听起来就是正常的 cai / san。以本轮最好的版本为准。</div>
    <div class="scale" data-key="ci_fix"></div>
  </div>
  <div class="q">
    <h3>2. 整体自然度</h3>
    <div class="hint">1 = 机械、发闷或咬字被切，10 = 接近正常录音。</div>
    <div class="scale" data-key="naturalness"></div>
  </div>
  <div class="q">
    <h3>3. 音色一致性</h3>
    <div class="hint">1 = 不像 narrator_03，10 = 仍是同一个人。</div>
    <div class="scale" data-key="identity"></div>
  </div>
  <div class="q">
    <h3>4. 中性首选</h3>
    <div class="hint">如果都不比 n0 好，选“保持当前定稿”。</div>
    <select id="neutral_pref">
      <option value="">— 请选择 —</option>
      <option value="keep_neutral_refdeess">保持当前定稿（n0 参考音先去齿）</option>
      <option value="neutral_s123_refg1">n1 换 Qwen seed 123</option>
      <option value="neutral_s42_refg2">n2 参考音强去齿 -8dB</option>
      <option value="neutral_s42_refg2_outg0">n3 强参考 + 输出温和去齿</option>
      <option value="neutral_refdeess_short80">n4 只收短最长擦音</option>
      <option value="neutral_refdeess_short40">n5 收短所有明显擦音</option>
      <option value="none">都不行</option>
    </select>
  </div>
  <div class="q">
    <h3>5. 悲伤首选</h3>
    <div class="hint">在保持悲伤语气的前提下选择。</div>
    <select id="sad_pref">
      <option value="">— 请选择 —</option>
      <option value="keep_sad_refdeess">保持当前定稿（s0 参考音先去齿）</option>
      <option value="sad_s123_refg1">s1 换 Qwen seed 123</option>
      <option value="sad_s42_refg2">s2 参考音强去齿 -8dB</option>
      <option value="none">都不行</option>
    </select>
  </div>
  <div class="q">
    <h3>6. 终选结论</h3>
    <div class="hint">决定阶段 3C 是否收尾、进入阶段 3 应用实现。</div>
    <select id="final_decision">
      <option value="">— 请选择 —</option>
      <option value="adopt_new">有新版本更好，替换当前定稿</option>
      <option value="keep_current">都不值得换，按当前定稿收尾</option>
      <option value="need_more">还需要继续调</option>
    </select>
  </div>
  <div class="q">
    <h3>7. 备注</h3>
    <div class="hint">例如“n1 菜正常了但三仍像 ci”“n5 咬字被切”“s1 语气不悲伤”。</div>
    <textarea id="notes" placeholder="写下你的听感..."></textarea>
  </div>

  <div class="row" style="margin:16px 0;">
    <button class="primary" id="copy">复制评分 JSON</button>
    <button id="download">下载评分 JSON</button>
    <button id="reset">清空本页记录</button>
    <span id="save_state" class="meta" style="margin-left:0;"></span>
  </div>

  <details>
    <summary>客观指标（4-10kHz 能量占比 p95，仅供参考，不等于听感）</summary>
    <table>
      <thead><tr><th>样本</th><th>时长</th><th>字/秒</th><th>HF p95</th></tr></thead>
      <tbody>__METRICS__</tbody>
    </table>
    <p class="group-note">HF p95 降低不等于发音变正确；n4 / n5 可能靠削掉擦音让数字下降，但听感未必自然。</p>
  </details>

  <div class="footer">
    阶段 3C 终选 · 数据来源：stage3c/vc_ab + stage3c/final_try。页面只读本地 WAV，无外网依赖。<br>
    生成时间：__GENERATED_AT__
  </div>
</div>
<div class="toast" id="toast">已保存</div>
<script>
const STORAGE_KEY = "nvs_stage3c_final_scores_v1";
const HEARD_KEY = "nvs_stage3c_final_heard_v1";
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
    page: "stage3c_final",
    version: "3C-final",
    saved_at: new Date().toISOString(),
    text: __TEXT_JSON__,
    scores,
    preferences: {
      neutral_pref: document.getElementById("neutral_pref").value || null,
      sad_pref: document.getElementById("sad_pref").value || null
    },
    final_decision: document.getElementById("final_decision").value || null,
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
    document.getElementById("final_decision").value = data.final_decision || "";
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
  link.download = "stage3c_final_result.json";
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
        (
            "neutral",
            "中性句 · 当前基准与 5 个尝试",
            "n0 是上一轮选定的版本。n1 换源、n2–n3 加强参考音处理、n4–n5 直接收短擦音。",
        ),
        (
            "sad",
            "悲伤句 · 当前基准与 2 个尝试",
            "s0 是上一轮选定的版本。s1 换源、s2 加强参考去齿；先确认悲伤语气有没有丢。",
        ),
    ]:
        items = [item for item in entries if item["group"] == group]
        cards = []
        for item in items:
            base = " baseline" if item["id"].endswith("baseline") else ""
            badge = f'<span class="badge base">{item["badge"]}</span>' if item["id"].endswith("baseline") else f'<span class="badge">{item["badge"]}</span>'
            cards.append(
                f"""
  <div class="sample{base}" data-id="{item['id']}" data-group="{group}" data-pref="{item['pref_value']}">
    <div class="sample-head">
      <h3>{item['title']}</h3>
      {badge}
      <span class="meta">{item['duration_ms'] / 1000:.2f}s · {item['cps']:.2f} 字/秒 · HF p95 {item['hf_p95']:.3f}</span>
    </div>
    <div class="desc">{item['desc']}</div>
    <audio controls preload="metadata" src="audition_audio/{item['file']}"></audio>
    <div class="row"><button data-pick>选它</button><span class="meta" style="margin-left:0;">{item['id']}</span></div>
  </div>"""
            )
        blocks.append(
            f'<h2>{title}</h2><div class="group-note">{note}</div>{"".join(cards)}'
        )
    return "\n".join(blocks)


def build_metrics(entries: list[dict]) -> str:
    return "".join(
        f"<tr><td>{item['title']}</td><td>{item['duration_ms'] / 1000:.2f}s</td>"
        f"<td>{item['cps']:.2f}</td><td>{item['hf_p95']:.3f}</td></tr>"
        for item in entries
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    AUDIO.mkdir(parents=True, exist_ok=True)
    entries = build_entries()
    for item in entries:
        shutil.copy2(item["src"], AUDIO / item["file"])
        item.pop("src")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (
        HTML.replace("__SECTIONS__", build_sections(entries))
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
        "purpose": "阶段 3C 最后一轮：菜/三 像 ci 的终选",
        "text": TEXT,
        "char_count": CHARS,
        "entries": entries,
    }
    (OUT / "audition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[DONE] {html_path}")
    for item in entries:
        print(
            f"  {item['id']:<24} {item['duration_ms']}ms "
            f"cps={item['cps']} hf95={item['hf_p95']}"
        )


if __name__ == "__main__":
    main()