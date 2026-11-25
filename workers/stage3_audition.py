"""把阶段 3 参考音与实验结果汇总成一个本地试听页。

只读取 stage3 目录中的 candidates.json / records.json 和 wav，不修改音频。
用法：
  uv run python workers/stage3_audition.py
  uv run python workers/stage3_audition.py --stage3-dir <数据目录>/cache/poc_output/stage3
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path

DEFAULT_STAGE3_DIR = (
    Path.home()
    / "Documents"
    / "NovelVoiceStudio"
    / "cache"
    / "poc_output"
    / "stage3"
)


def load_json(path: Path, key: str = "records") -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get(key, [])


def load_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return esc(value)


def audio_tag(path: Path, base: Path) -> str:
    if not path.exists():
        return '<span class="missing">缺少音频</span>'
    rel = path.relative_to(base).as_posix()
    return f'<audio controls preload="none" src="{esc(rel)}"></audio>'


def stats_for(record: dict) -> dict:
    return record.get("clean_stats") or record


def clean_path(base: Path, case_id: str) -> Path:
    return base / "experiments_clean" / f"{case_id}.wav"


def stats_line(record: dict) -> str:
    dc = record.get("dc_offset")
    dc_text = (
        f" · DC {float(dc):.4f}"
        if isinstance(dc, (int, float)) and abs(dc) >= 0.001
        else ""
    )
    return (
        f"{fmt(record.get('duration_ms'))} ms · peak {fmt(record.get('peak'), 3)} · "
        f"RMS {fmt(record.get('rms_dbfs'), 1)} dBFS{dc_text} · "
        f"尾部静音 {fmt(record.get('trailing_silence_ms'))} ms · "
        f"尾 200ms 高频占比 {fmt(record.get('tail_200ms_hf_ratio'), 4)}"
    )


def render_reference(records: list[dict], clean_records: list[dict], base: Path) -> str:
    if not records:
        return '<p class="empty">尚未生成参考音候选。</p>'
    clean_by_source = {
        item.get("source_record", {}).get("file"): item
        for item in clean_records
        if item.get("source_record", {}).get("file")
    }
    by_character: dict[str, list[dict]] = {}
    for record in records:
        by_character.setdefault(record["character_name"], []).append(record)
    cards = []
    for name, items in by_character.items():
        rows = []
        for item in items:
            path = base / "voices" / item["file"]
            clean = clean_by_source.get(item["file"])
            clean_block = ""
            if clean:
                clean_path = base / "refs_clean" / clean["file"]
                clean_block = (
                    f'<div class="sub-label">clean 参考音（用于 TTS，-23 dBFS）</div>'
                    f'{audio_tag(clean_path, base)}'
                    f'<p class="meta">{stats_line(clean)}</p>'
                )
            rows.append(
                f"""
                <div class="sample">
                  <div class="sample-head"><strong>{esc(item['file'])}</strong>
                    <span class="tag">{esc(item['variant'])}</span>
                    <span class="tag">seed {esc(item.get('seed'))}</span>
                  </div>
                  <div class="sub-label">原始候选</div>
                  {audio_tag(path, base)}
                  <p class="meta">{stats_line(item)}</p>
                  {clean_block}
                  <p class="text">{esc(item.get('instruct'))}</p>
                </div>
                """
            )
        cards.append(
            f'<section class="card"><h3>{esc(name)}</h3>{"".join(rows)}</section>'
        )
    return "".join(cards)


def render_core(records: list[dict], base: Path) -> str:
    core = [item for item in records if item.get("group") == "core"]
    if not core:
        return '<p class="empty">尚未生成核心情绪样本。</p>'
    by_character: dict[str, list[dict]] = {}
    for item in core:
        by_character.setdefault(item.get("character_label", item.get("character", "")), []).append(item)
    cards = []
    for name, items in by_character.items():
        rows = []
        for item in sorted(items, key=lambda x: x.get("emotion", "")):
            path = clean_path(base, item["id"])
            if not path.exists():
                path = base / "experiments" / f"{item['id']}.wav"
            rows.append(
                f"""
                <div class="sample">
                  <div class="sample-head"><strong>{esc(item['id'])}</strong>
                    <span class="tag">{esc(item.get('emotion'))}</span>
                    <span class="tag">clean</span></div>
                  {audio_tag(path, base)}
                  <p class="meta">{stats_line(stats_for(item))}</p>
                  <p class="text">{esc(item.get("text"))}</p>
                </div>
                """
            )
        cards.append(
            f'<section class="card"><h3>{esc(name)}</h3>{"".join(rows)}</section>'
        )
    return "".join(cards)


def render_metrics(metrics: dict, records: list[dict]) -> str:
    if not metrics:
        return '<p class="empty">尚未生成说话人嵌入辅助指标。</p>'
    core = metrics.get("core_cross_emotion_similarity", {})
    cross = metrics.get("cross_character_similarity", {})
    ref = metrics.get("reference_similarity", {})
    record_by_id = {item["id"]: item for item in records}

    core_rows = []
    for character, pairs in core.items():
        values = [value for value in pairs.values() if isinstance(value, (int, float))]
        lowest = min(values) if values else None
        warning = " warn" if lowest is not None and lowest < 0.7 else ""
        core_rows.append(
            f"<tr><td>{esc(character)}</td>"
            f"<td>{fmt(pairs.get('angry-neutral'), 4)}</td>"
            f"<td>{fmt(pairs.get('angry-sad'), 4)}</td>"
            f"<td>{fmt(pairs.get('neutral-sad'), 4)}</td>"
            f'<td class="metric{warning}">{fmt(lowest, 4)}</td></tr>'
        )

    cross_text = "　".join(
        f"{esc(pair)}: <strong>{fmt(value, 4)}</strong>" for pair, value in cross.items()
    )
    lowest_ref = sorted(
        (
            (case_id, value)
            for case_id, value in ref.items()
            if isinstance(value, (int, float))
        ),
        key=lambda item: item[1],
    )[:8]
    ref_rows = []
    for case_id, value in lowest_ref:
        item = record_by_id.get(case_id, {})
        warning = " warn" if value < 0.7 else ""
        ref_rows.append(
            f"<tr><td><code>{esc(case_id)}</code></td>"
            f"<td>{esc(item.get('character_label', '-'))}</td>"
            f"<td>{esc(item.get('emotion', '-'))}</td>"
            f'<td class="metric{warning}">{fmt(value, 4)}</td></tr>'
        )
    return f"""
    <div class="grid metrics-grid">
      <section class="card">
        <h3>同角色跨情绪相似度</h3>
        <p class="lead">越接近 1 越像同一人。低于 0.7 标红，只作为排查线索。</p>
        <table class="mini">
          <thead><tr><th>角色</th><th>angry-neutral</th><th>angry-sad</th><th>neutral-sad</th><th>最低</th></tr></thead>
          <tbody>{''.join(core_rows)}</tbody>
        </table>
      </section>
      <section class="card">
        <h3>不同角色相似度</h3>
        <p class="lead">{cross_text or '-'}</p>
        <p class="lead">这一组越低越好，当前三个角色有明确区分度。</p>
      </section>
      <section class="card full">
        <h3>生成音频与参考音相似度最低的 8 条</h3>
        <p class="lead">低于 0.7 标红。young_female 的 angry 样本明显偏离参考音，是当前最大的音色漂移信号。</p>
        <table class="mini">
          <thead><tr><th>样本</th><th>角色</th><th>情绪</th><th>与参考音余弦</th></tr></thead>
          <tbody>{''.join(ref_rows)}</tbody>
        </table>
      </section>
    </div>
    """


def render_ab(records: list[dict], base: Path) -> str:
    ab = [item for item in records if item.get("group") == "ab"]
    if not ab:
        return '<p class="empty">尚未生成 A/B 样本。</p>'
    rows = []
    for item in sorted(ab, key=lambda x: x["id"]):
        path = base / "experiments" / f"{item['id']}.wav"
        status = []
        if item.get("reference_wav"):
            status.append(f"参考 {Path(item['reference_wav']).name}")
        if item.get("mode"):
            status.append(item["mode"])
        if item.get("chunk_count") is not None:
            status.append(f"chunks {item['chunk_count']}")
        if item.get("seed") is not None:
            status.append(f"seed {item['seed']}")
        if item.get("status") == "failed":
            status.append("FAIL")
        clean = base / item.get("clean_file", "") if item.get("clean_file") else None
        clean_block = ""
        if clean and clean.exists():
            clean_block = (
                f'<div class="sub-label">clean 版本（-20 dBFS / peak 0.95）</div>'
                f'{audio_tag(clean, base)}'
                f'<p class="meta">{stats_line(item.get("clean_stats") or {})}</p>'
            )
        failure_block = ""
        if item.get("failure_reason"):
            failure_block = (
                f'<p class="warn">失败原因：{esc(item["failure_reason"])}。'
                "原始输出仅用于确认失败，不进入有效评估。</p>"
            )
        rows.append(
            f"""
            <div class="sample">
              <div class="sample-head"><strong>{esc(item['id'])}</strong>
                {''.join(f'<span class="tag">{esc(tag)}</span>' for tag in status)}
              </div>
              <div class="sub-label">原始输出</div>
              {audio_tag(path, base)}
              <p class="meta">{stats_line(item)}</p>
              {clean_block}
              {failure_block}
              <p class="text">{esc(item.get('notes') or item.get('text'))}</p>
            </div>
            """
        )
    return f'<section class="card wide">{"".join(rows)}</section>'


def render_eval(records: list[dict], base: Path) -> str:
    eval_records = [item for item in records if item.get("group") == "eval"]
    if not eval_records:
        return '<p class="empty">尚未生成 20 句评估集。</p>'
    rows = []
    for item in sorted(eval_records, key=lambda x: x["id"]):
        path = clean_path(base, item["id"])
        if not path.exists():
            path = base / "experiments" / f"{item['id']}.wav"
        stats = stats_for(item)
        rows.append(
            f"""
            <tr>
              <td><code>{esc(item['id'])}</code></td>
              <td>{esc(item.get('character_label'))}</td>
              <td>{esc(item.get('emotion'))}</td>
              <td>{audio_tag(path, base)}</td>
              <td>{fmt(stats.get('duration_ms'))} ms<br>
                  <small>peak {fmt(stats.get('peak'), 3)} · 尾静音 {fmt(stats.get('trailing_silence_ms'))} ms · RTF {fmt(item.get('rtf'), 2)}</small>
              </td>
              <td class="text">{esc(item.get('text'))}<br>
                  <small>{esc(item.get('instruction'))}</small></td>
            </tr>
            """
        )
    return f"""
    <table class="eval-table">
      <thead><tr><th>ID</th><th>角色</th><th>情绪</th><th>试听</th><th>客观</th><th>文本 / 指令</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_score_form() -> str:
    scores = [
        ("voice_diff", "三音色差异", "旁白 / 年轻女性 / 老年男性是否一听就能分开"),
        ("emotion_consistency", "跨情绪一致性", "同一角色 neutral / angry / sad 是否仍像同一个人"),
        ("sad_expressiveness", "sad 表现力", "悲伤是否自然，而不是只把语速放慢"),
        ("pronunciation", "中文发音与杂音", "数字、日期、英文缩写与整体清晰度"),
        ("tail_breath", "句尾气声/杂音", "是否出现 qi/si/ci 之类的漏气或尾音噪声"),
        ("overall", "整体可用性", "22 句评估集能否支撑继续开发阶段 3 应用功能"),
    ]
    score_fields = "".join(
        f"""
        <label class="score-field">
          <span>{esc(label)}</span>
          <input type="number" min="1" max="10" step="0.5" inputmode="decimal" data-score="{key}" placeholder="1–10">
          <small>{esc(hint)}</small>
        </label>
        """
        for key, label, hint in scores
    )
    ref_fields = "".join(
        f"""
        <label class="score-field">
          <span>{esc(label)}</span>
          <select data-ref="{key}">
            <option value="">未选</option>
            <option value="01">01</option>
            <option value="02">02</option>
            <option value="03">03</option>
          </select>
          <small>{esc(hint)}</small>
        </label>
        """
        for key, label, hint in [
            ("narrator", "旁白候选", "对应 voices/narrator_0x.wav"),
            ("young_female", "年轻女性候选", "对应 voices/young_female_0x.wav"),
            ("old_male", "老年男性候选", "对应 voices/old_male_0x.wav"),
        ]
    )
    return f"""
    <section class="card score" id="score-panel">
      <h3>本人试听评分</h3>
      <p class="lead">评分只保存在这台电脑的浏览器 localStorage，不会上传。填完后可点“复制评分”，把文本发回给开发流程。</p>
      <div class="score-grid">{score_fields}</div>
      <div class="score-grid ref-grid">{ref_fields}</div>
      <div class="score-grid">
        <label class="score-field">
          <span>gate 结论</span>
          <select data-result>
            <option value="">未定</option>
            <option value="通过">通过</option>
            <option value="有条件通过">有条件通过</option>
            <option value="不通过">不通过</option>
          </select>
          <small>建议对照 docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md 的退出标准</small>
        </label>
      </div>
      <label class="score-field notes">
        <span>备注 / 具体问题</span>
        <textarea data-notes rows="4" placeholder="例如：某条样本尾音有气声；某角色 angry 不像本人；哪个候选最自然。"></textarea>
      </label>
      <div class="actions">
        <button type="button" id="score-save">保存评分</button>
        <button type="button" id="score-copy">复制评分</button>
        <button type="button" id="score-clear" class="secondary">清空</button>
        <span id="score-status" class="status" role="status"></span>
      </div>
    </section>
    """


def main() -> None:
    parser = argparse.ArgumentParser(description="生成阶段 3 本地试听页")
    parser.add_argument("--stage3-dir", type=Path, default=DEFAULT_STAGE3_DIR)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    base = args.stage3_dir.resolve()
    reference_records = load_json(base / "voices" / "candidates.json")
    clean_reference_records = load_json(base / "refs_clean" / "manifest.json")
    experiment_records = load_json(base / "experiments" / "records.json")
    speaker_metrics = load_metrics(base / "experiments" / "speaker_metrics.json")
    output = args.output or (base / "audition.html")

    generated = [
        item for item in experiment_records if item.get("status") in {"ok", "failed"}
    ]
    ok_count = sum(1 for item in generated if item.get("status") == "ok")
    failed = [item for item in generated if item.get("status") == "failed"]
    derived_count = sum(1 for item in experiment_records if item.get("status") not in {"ok", "failed"})
    failed_note = "、".join(item.get("id", "?") for item in failed) or "无"

    score_script = """
<script>
(function () {
  const STORAGE_KEY = "novelvoice-stage3-score-v1";
  const scoreLabels = {
    voice_diff: "三音色差异",
    emotion_consistency: "跨情绪一致性",
    sad_expressiveness: "sad 表现力",
    pronunciation: "中文发音与杂音",
    tail_breath: "句尾气声/杂音",
    overall: "整体可用性"
  };
  const refLabels = {
    narrator: "旁白候选",
    young_female: "年轻女性候选",
    old_male: "老年男性候选"
  };
  const scoreInputs = Array.from(document.querySelectorAll("[data-score]"));
  const refSelects = Array.from(document.querySelectorAll("[data-ref]"));
  const resultSelect = document.querySelector("[data-result]");
  const notesInput = document.querySelector("[data-notes]");
  const statusEl = document.getElementById("score-status");

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  function collect() {
    const scores = {};
    scoreInputs.forEach((el) => {
      scores[el.dataset.score] = el.value === "" ? null : Number(el.value);
    });
    const refs = {};
    refSelects.forEach((el) => {
      refs[el.dataset.ref] = el.value || null;
    });
    return {
      scores: scores,
      refs: refs,
      result: resultSelect.value || null,
      notes: notesInput.value || "",
      savedAt: new Date().toISOString()
    };
  }

  function fill(data) {
    if (!data || typeof data !== "object") return;
    scoreInputs.forEach((el) => {
      const value = data.scores && data.scores[el.dataset.score];
      el.value = value === null || value === undefined ? "" : value;
    });
    refSelects.forEach((el) => {
      el.value = (data.refs && data.refs[el.dataset.ref]) || "";
    });
    resultSelect.value = data.result || "";
    notesInput.value = data.notes || "";
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(collect()));
      setStatus("已保存到本机浏览器。");
    } catch (error) {
      setStatus("保存失败：" + error.message, true);
    }
  }

  function toText(data) {
    const lines = ["阶段 3 试听评分"];
    Object.keys(scoreLabels).forEach((key) => {
      const value = data.scores[key];
      lines.push("- " + scoreLabels[key] + "：" + (value === null ? "-" : value + " / 10"));
    });
    Object.keys(refLabels).forEach((key) => {
      lines.push("- " + refLabels[key] + "：" + (data.refs[key] || "未选"));
    });
    lines.push("- gate 结论：" + (data.result || "未定"));
    lines.push("- 备注：" + (data.notes || "-"));
    lines.push("- 页面保存时间：" + data.savedAt);
    return lines.join("\\n");
  }

  function fallbackCopy(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.focus();
    area.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(area);
    if (!copied) throw new Error("浏览器拒绝复制");
  }

  async function copyScore() {
    const data = collect();
    const text = toText(data);
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        fallbackCopy(text);
      }
      setStatus("评分已复制到剪贴板。");
    } catch (error) {
      try {
        fallbackCopy(text);
        setStatus("评分已复制到剪贴板。");
      } catch (fallbackError) {
        setStatus("复制失败，请手动选择备注框内容。" + fallbackError.message, true);
      }
    }
  }

  function clearScore() {
    if (!confirm("清空本机保存的评分？")) return;
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      // 本地存储不可用时仍然清空表单。
    }
    fill({ scores: {}, refs: {}, result: "", notes: "" });
    setStatus("已清空本机评分。");
  }

  document.getElementById("score-save").addEventListener("click", save);
  document.getElementById("score-copy").addEventListener("click", copyScore);
  document.getElementById("score-clear").addEventListener("click", clearScore);

  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      fill(JSON.parse(raw));
      setStatus("已载入本机保存的评分。");
    }
  } catch (error) {
    setStatus("本机评分读取失败，可重新填写。" + error.message, true);
  }
})();
</script>
"""

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NovelVoice Studio · 阶段 3 试听</title>
<style>
  :root {{ color-scheme: dark; --bg:#12100e; --panel:#1c1916; --line:#3a332d; --text:#f1e9df; --muted:#b9aa9b; --accent:#e0a86b; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.65 "Microsoft YaHei", "Noto Sans SC", sans-serif; }}
  main {{ max-width:1180px; margin:0 auto; padding:36px 22px 80px; }}
  h1 {{ font-size:30px; margin:0 0 8px; letter-spacing:.02em; }}
  h2 {{ font-size:21px; margin:38px 0 14px; border-left:3px solid var(--accent); padding-left:10px; }}
  h3 {{ margin:0 0 12px; font-size:17px; }}
  p {{ margin:8px 0; }}
  .lead {{ color:var(--muted); max-width:840px; }}
  .notice {{ background:#2a2118; border:1px solid #59432e; padding:14px 16px; border-radius:10px; margin:20px 0; }}
  .nav {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 0; }}
  .nav a {{ color:var(--accent); text-decoration:none; font-size:13px; border:1px solid var(--line); padding:4px 10px; border-radius:999px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(310px,1fr)); gap:16px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .card.wide {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:8px 18px; }}
  .grid > .full {{ grid-column:1/-1; }}
  .metrics-grid {{ grid-template-columns:minmax(0,2fr) minmax(0,1fr); }}
  @media (max-width:800px) {{ .metrics-grid {{ grid-template-columns:1fr; }} }}
  .sample {{ padding:12px 0; border-top:1px dashed var(--line); }}
  .sample:first-of-type {{ border-top:0; }}
  .sample-head {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:8px; }}
  audio {{ width:100%; height:36px; margin:4px 0; }}
  .tag {{ font-size:12px; color:var(--accent); border:1px solid #6b4d31; padding:1px 7px; border-radius:999px; }}
  .meta, small {{ color:var(--muted); font-size:12.5px; }}
  .text {{ color:#dfd2c3; font-size:13.5px; }}
  .sub-label {{ color:var(--accent); font-size:12px; margin-top:8px; }}
  .empty, .missing {{ color:#d98a7a; }}
  .eval-table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  .eval-table th, .eval-table td {{ padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; text-align:left; }}
  .eval-table th {{ background:#241f1b; color:var(--accent); font-weight:600; }}
  .eval-table td:nth-child(4) {{ min-width:190px; }}
  code {{ color:#f0c48f; }}
  .checklist li {{ margin:6px 0; color:var(--muted); }}
  .score-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin:12px 0; }}
  .score-field {{ display:flex; flex-direction:column; gap:6px; background:#241f1b; border:1px solid var(--line); padding:10px 12px; border-radius:10px; }}
  .score-field span {{ font-weight:600; }}
  .score-field small {{ color:var(--muted); }}
  .score-field input, .score-field select, .score-field textarea {{ background:#151311; color:var(--text); border:1px solid var(--line); border-radius:8px; padding:7px 9px; font:inherit; }}
  .score-field input {{ width:110px; }}
  .score-field.notes {{ margin-top:12px; }}
  .score-field.notes textarea {{ width:100%; resize:vertical; }}
  .ref-grid {{ grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); }}
  .actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:12px; }}
  .actions button {{ background:var(--accent); color:#1a1209; border:0; border-radius:8px; padding:9px 16px; font:inherit; font-weight:600; cursor:pointer; }}
  .actions button.secondary {{ background:transparent; color:var(--muted); border:1px solid var(--line); }}
  .status {{ color:var(--muted); font-size:13px; }}
  .status.error {{ color:#d98a7a; }}
  table.mini {{ width:100%; border-collapse:collapse; margin-top:8px; }}
  table.mini th, table.mini td {{ padding:7px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
  table.mini th {{ color:var(--accent); font-weight:600; }}
  .metric.warn, .warn {{ color:#e39a86; font-weight:600; }}
</style>
</head>
<body>
<main>
  <h1>阶段 3 音质 spike · 试听页</h1>
  <p class="lead">生成于 {datetime.now().strftime("%Y-%m-%d %H:%M")}，数据目录 {esc(base.as_posix())}。本页汇总样本与客观指标，不预填听感评分；请以实际试听为准。</p>
  <div class="notice">
    <strong>听感 gate 尚未关闭。</strong>技术实验共 {len(generated)} 条生成记录：{ok_count} 条有效、{len(failed)} 条失败（{esc(failed_note)}），另有 {derived_count} 条后处理派生样本。
    失败样本保留原始输出但不进入有效评估。峰值、RMS、尾部静音与尾部高频占比只是辅助指标，不能代替听感。
  </div>
  <nav class="nav">
    <a href="#score-panel">评分表</a>
    <a href="#refs">参考音候选</a>
    <a href="#core">核心情绪</a>
    <a href="#metrics">说话人指标</a>
    <a href="#ab">A/B 实验</a>
    <a href="#eval">22 句评估集</a>
  </nav>

  <h2>0. 评分表</h2>
  {render_score_form()}

  <h2 id="refs">1. 中性参考音候选</h2>
  <p class="lead">每个角色 3 条候选，参考文本为中性叙述；请先挑选每个角色最干净、最符合设定的一条，并在评分表里记录编号。</p>
  <div class="grid">{render_reference(reference_records, clean_reference_records, base)}</div>

  <h2 id="core">2. 核心情绪对照</h2>
  <p class="lead">同一角色、同一句文本，分别生成 neutral / angry / sad，用于判断跨情绪是否仍像同一个人。</p>
  <div class="grid">{render_core(experiment_records, base)}</div>

  <h2 id="metrics">3. 辅助客观指标</h2>
  <p class="lead">使用 CosyVoice Campplus 说话人嵌入计算余弦相似度。它只能提示音色漂移风险，不能替代人耳判断。</p>
  {render_metrics(speaker_metrics, experiment_records)}

  <h2 id="ab">4. A/B 实验</h2>
  <p class="lead">参考音情绪污染、种子稳定性、instruct2 与 zero_shot、参考文本条件、分句拼接与后处理。</p>
  {render_ab(experiment_records, base)}

  <h2 id="eval">5. 22 句评估集</h2>
  <p class="lead">20 句中文主集覆盖旁白、对白、内心独白与数字日期；另补 2 句英文缩写（AI / CEO / KTV / 百分比）。用于检查整体可用性，而不是只挑一条最好听的样本。</p>
  {render_eval(experiment_records, base)}

  <h2>6. 记录</h2>
  <p class="lead">原始记录：<code>voices/candidates.json</code>、<code>refs_clean/manifest.json</code>、<code>experiments/records.json</code>、<code>experiments/speaker_metrics.json</code>。计划与退出标准见仓库 <code>docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md</code>。</p>
</main>
{score_script}
</body>
</html>
"""
    output.write_text(page, encoding="utf-8")
    print(f"[OK] 试听页: {output}")


if __name__ == "__main__":
    main()