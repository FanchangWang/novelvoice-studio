# 阶段 3 音质修复 spike 报告

- 状态：**技术实验完成；听感 gate 未通过（2026-11-25）**
- 试听结果：`docs/reports/STAGE3_LISTENING_RESULT.md`（跨情绪一致性 1/10、sad 1/10，gate 不通过）
- 计划：`docs/reports/STAGE3_AUDIO_QUALITY_PLAN.md`
- 输入：阶段 0 试听结论（`docs/reports/POC_REPORT.md` 第 4.1、8 节）
- 边界：本报告不宣称阶段 3 通过，也不改写阶段 0 的「有条件通过」结论。没有用户听感评分前，不进入声音库、单段 WAV、音色绑定和版本管理实现。

## 1. 结论摘要

1. **文本前端已修复到可运行状态。** CosyVoice Worker 将 `wetext` 从 `0.0.4` 升级到 `0.0.7`，使用 wheel 内置 FST，不再访问 ModelScope 下载资源；日志已出现 `use wetext frontend`，日期、金额、面积和百分比可以规范化为中文读法。
2. **参考音已改为中性文本重新生成。** 3 个角色各 3 条候选，共 9 条，全部做了去首尾静音、短淡入淡出、RMS 归一到约 `-23 dBFS`。原始候选仍单独保留，没有覆盖阶段 0 样本。
3. **实验矩阵已跑完。** 共 46 条记录：45 条生成记录（44 有效、1 失败）、1 条后处理派生记录。实验覆盖核心情绪、参考音情绪污染、种子、`instruct2` / `zero_shot`、参考文本条件、分句拼接和 raw/clean 对比。
4. **参考文本条件至少被证明不是完全无效。** `zero_shot` 在空参考文本时输出 40ms 静音并判定失败；正确参考文本输出 9.32s；故意使用不匹配参考文本时输出 18.36s。输出随时长条件明显变化，但听感质量仍需试听确认。
5. **当前不能进入阶段 3 应用功能实现。** 主要阻塞不是页面，而是：
   - 听感评分尚未完成；
   - RTF 明显回退：44 条有效样本中 42 条 `RTF > 1`，中位数 1.617，最高 2.955；阶段 0 为 0.553–0.635；
   - 年轻女性 angry 出现客观音色漂移：生成音频与参考音余弦 0.5084，评估集 angry 仅 0.3880；
   - sad 时长约为 neutral 的两倍，是否自然尚未由用户确认；
   - 4 条 clean 样本因峰值上限未达到 `-20 dBFS`，其中最低 `-23.76 dBFS`。
6. **建议顺序：先试听并评分，再做一轮聚焦技术收敛，最后才实现阶段 3 业务功能。** 不建议把当前音质直接带进声音库和批量流程。

## 2. 交付物与证据

| 内容 | 路径 |
|---|---|
| 本地试听页（含评分表） | `%USERPROFILE%\Documents\NovelVoiceStudio\cache\poc_output\stage3\audition.html` |
| 参考音候选记录 | `...\stage3\voices\candidates.json` |
| 清洗参考音 | `...\stage3\refs_clean\` 与 `manifest.json` |
| 实验记录 | `...\stage3\experiments\records.json` |
| 说话人嵌入辅助指标 | `...\stage3\experiments\speaker_metrics.json` |
| 原始实验音频 | `...\stage3\experiments\` |
| clean 实验音频 | `...\stage3\experiments_clean\` |
| 阶段 3 生成脚本 | `workers/qwen_tts_worker/voice_design_stage3.py` |
| 参考音清洗脚本 | `workers/cosyvoice_worker/stage3_prepare_refs.py` |
| A/B 与评估集脚本 | `workers/cosyvoice_worker/stage3_ab.py` |
| 实验定义 | `workers/cosyvoice_worker/stage3_cases.json` |
| 说话人指标脚本 | `workers/cosyvoice_worker/stage3_speaker_metrics.py` |
| 试听页生成脚本 | `workers/stage3_audition.py` |

音频位于数据目录，不进入仓库；仓库只保留脚本、实验定义和本报告。

## 3. 环境与文本前端修复

### 3.1 实际环境

| 项目 | 实测值 |
|---|---|
| CosyVoice Worker | Python 3.10，`workers/cosyvoice_worker/.venv` |
| TTS 模型 | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` |
| VoiceDesign 模型 | `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` |
| 文本前端 | `wetext 0.0.7`，日志 `use wetext frontend` |
| 采样率 / 声道 | 24,000 Hz / 单声道 |
| GPU | RTX 4070 Ti 12GB；本轮未观察到持续性 OOM |

`workers/cosyvoice_worker/pyproject.toml` 已改为：

```text
requires-python = ">=3.10,<3.11"
wetext==0.0.7
```

`uv.lock` 已同步。`0.0.7` wheel 自带 `wetext/fsts/zh/tn/tagger.fst` 与 `verbalizer.fst`，不再依赖 ModelScope 的 `tagger.fst` 下载，因此阶段 0 的 HTTP 403 不再出现。

### 3.2 规范化证据

运行日志中的实际文本：

| 输入 | 前端输出 |
|---|---|
| `2026年11月25日，他花了12.5万元，买下这间不到30平方米的旧铺子。` | `二零二六年十一月二十五日，他花了十二点五万元，买下这间不到三十平方米的旧铺子。` |
| `那是1987年，铺子每月租金37.5元，我们一共攒了两年。` | `那是一九八七年，铺子每月租金三十七点五元，我们一共攒了两年。` |
| `手机只剩 3% 的电了` | `手机只剩百分之三的电了` |
| `那份 AI 报告放在桌上，CEO 明天上午十点要看。` | 保留 `AI`、`CEO` 拉丁串，未做字母转写 |
| `我在 KTV 门口等你` | 保留 `KTV` 拉丁串 |

结论边界：

- 数字、日期、金额、面积、百分比的**文本规范化**已有证据；
- 英文缩写是否按字母逐个读出，必须听音频确认，不能仅凭日志判断；
- `ttsfrd` 仍未装配，当前使用的是 `wetext` 替代链路，不是 CosyVoice 官方完整前端；
- `wetext` 的许可证尚未做阶段 3 专项核对。

## 4. 中性参考音候选

VoiceDesign 参考文本统一为：

> 雨停以后，街上的灯一盏一盏亮起来。风从巷口吹过，带着潮湿的凉意。远处有人慢慢走过，脚步声很轻。

生成参数记录在 `voices/candidates.json`：`do_sample=true`、`top_k=50`、`top_p=1.0`、`temperature=0.9`、`repetition_penalty=1.05`、`max_new_tokens=2048`。每个角色 3 个种子（42 / 123 / 2026）。

| 候选 | 角色 | 原始时长 | 原始峰值 | 原始 RMS | clean 峰值 | clean RMS | 备注 |
|---|---|---:|---:|---:|---:|---:|---|
| narrator_01 | 旁白-中年男性 | 12.72s | 0.758 | -18.86 | 0.470 | -23.00 | 平稳 |
| narrator_02 | 旁白-中年男性 | 13.20s | 0.695 | -23.05 | 0.699 | -23.00 | 低沉 |
| narrator_03 | 旁白-中年男性 | 11.52s | 0.789 | -22.53 | 0.748 | -23.00 | 明亮 |
| young_female_01 | 年轻女性 | 12.40s | 0.586 | -19.33 | 0.384 | -23.00 | 清亮 |
| young_female_02 | 年轻女性 | 13.76s | 0.527 | -19.81 | 0.364 | -23.00 | 偏低 |
| young_female_03 | 年轻女性 | 11.28s | 0.652 | -17.90 | 0.363 | -23.00 | 轻盈 |
| old_male_01 | 老年男性 | 18.56s | 0.594 | -27.33 | **0.950** | **-23.18** | 峰值受限；clean DC 约 **0.023** |
| old_male_02 | 老年男性 | 20.48s | 0.320 | -27.73 | 0.543 | -23.00 | 低缓 |
| old_male_03 | 老年男性 | 20.48s | 0.197 | -32.04 | 0.540 | -23.00 | 原始很轻，增益 2.74 |

处理方式：去首尾静音、10ms 淡入、40ms 淡出、RMS 目标 `-23 dBFS`、峰值上限 `0.95`。没有做降噪、EQ 或压缩。

需要人工确认的点：

- 9 条候选哪条最自然，目前没有选择结果；
- `old_male_01` 的 clean 版本存在约 `0.023` 的直流偏置，峰值也被顶到 `0.95`，不建议在未处理 DC 前直接作为长期音色锚点；
- `old_male_03` 原始 RMS 只有 `-32.04 dBFS`，虽然清洗后归一，但原始动态和噪声底需要试听确认。

核心情绪实验默认使用 `narrator_01`、`young_female_01`、`old_male_01` 的 clean 版本，因此老年男性结果受 `old_male_01` 的 DC/峰值问题影响。

## 5. 核心情绪结果

测试文本：

> 我等你很久了。你答应过今天一定回来，可现在天都黑了。

| 角色 | 情绪 | raw 时长 | raw 峰值 | clean 时长 | clean RMS | RTF | 与参考音余弦 |
|---|---|---:|---:|---:|---:|---:|---:|
| narrator | neutral | 5.32s | 0.698 | 5.21s | -20.00 | 1.96 | 0.8760 |
| narrator | angry | 6.60s | **0.990** | 6.52s | -20.00 | 1.63 | 0.7389 |
| narrator | sad | 10.76s | 0.503 | 9.26s | -20.00 | 1.39 | 0.8673 |
| young_female | neutral | 5.32s | 0.829 | 5.21s | -20.00 | 1.74 | 0.7408 |
| young_female | angry | 6.60s | 0.899 | 6.60s | -20.00 | 1.58 | **0.5084** |
| young_female | sad | 10.76s | 0.444 | 10.38s | -20.00 | 1.35 | 0.8032 |
| old_male | neutral | 5.32s | **0.990** | 5.21s | -20.00 | 2.09 | 0.7426 |
| old_male | angry | 6.60s | **0.990** | 6.60s | -20.00 | 1.88 | 0.7693 |
| old_male | sad | 10.76s | **0.990** | 10.45s | **-23.76** | 1.52 | 0.8922 |

客观观察：

- 不同角色之间的说话人嵌入平均余弦为 `0.32–0.42`，说明三个音色在客观指标上有明显区分度；但这不是听感评分。
- 同角色跨情绪余弦为 `0.68–0.85`。年轻女性 `angry-sad` 只有 `0.6800`，是三者最低；其 angry 与参考音只有 `0.5084`，说明愤怒时音色漂移最大。评估集中 `eval_young_female_angry` 与参考音仅 `0.3880`，进一步放大了这个信号。
- 三个角色在同一情绪下的原生时长完全一致：neutral 5.32s、angry 6.60s、sad 10.76s。当前链路中情绪指令对节奏的影响强于音色，可能导致不同角色听起来“同一个节奏在演不同情绪”。这一点必须靠试听确认，不能仅用指标下结论。
- 5 条 raw 样本峰值达到 `0.990`（core narrator angry、core old_male 三种情绪、eval_old_male_angry），但 `clipping_ratio` 均为 0；clean 版本全部限制在 `0.95` 以下。
- 4 条 clean 样本受峰值上限影响，未达到 `-20 dBFS`：`core_old_male_sad` `-23.76`、`eval_old_male_sad` `-23.55`、`eval_old_male_neutral` `-22.88`、`eval_old_male_sarcastic` `-20.60`。

## 6. A/B 实验结果

### 6.1 参考音情绪污染与种子

| 实验 | 输出时长 | 峰值 | 与参考音余弦 | RTF | 说明 |
|---|---:|---:|---:|---:|---|
| `ab_ref_neutral_sad` | 10.76s | 0.503 | 0.8673 | 0.617 | 中性参考音 + sad 指令 |
| `ab_ref_old_angry_sad` | 10.76s | 0.447 | 0.8962 | 0.853 | 阶段 0 愤怒参考音 + sad 指令 |
| `ab_seed_42` | 10.76s | 0.503 | 0.8673 | 1.019 | 与 `core_narrator_sad`、`ab_ref_neutral_sad` 的 SHA-256 完全相同 |
| `ab_seed_123` | 9.08s | 0.470 | 0.8389 | 1.448 | 时长与尾音特征明显不同 |
| `ab_seed_2026` | 8.84s | 0.483 | 0.8089 | 1.507 | 时长更短，参考音相似度更低 |

- 相同输入、相同种子下，`core_narrator_sad`、`ab_ref_neutral_sad`、`ab_seed_42`、`ab_post_original` 的 SHA-256 一致，说明当前链路在固定种子下是确定性的。
- 这四条音频完全相同，但记录的 RTF 分别为 1.385 / 0.617 / 1.019 / 1.365；说明 RTF 受 GPU 预热、调度和短音频固定开销影响，单条 RTF 不宜过度解读，应看中位数和分布。
- 种子会改变 sad 的输出时长和尾段特征，但也会改变参考音相似度；不能只用“换种子”掩盖音色漂移。
- 旧愤怒参考音与中性参考音都是 10.76s，峰值和尾段高频占比不同。听感上哪一个更自然，需要用户判断。

### 6.2 推理模式与参考文本条件

| 实验 | 模式 | 参考文本 | 输出时长 | 峰值 | 与参考音余弦 | RTF | 结果 |
|---|---|---|---:|---:|---:|---:|---|
| `ab_mode_instruct_neutral` | instruct2 | 不使用 | 3.48s | 0.548 | 0.8658 | 2.203 | 有效 |
| `ab_mode_zero_shot_neutral` | zero_shot | 正确 | 4.72s | 0.430 | 0.9086 | 1.905 | 有效 |
| `ab_prompt_correct` | zero_shot | 正确 | 9.32s | 0.431 | 0.9375 | 1.412 | 有效 |
| `ab_prompt_mismatch` | zero_shot | 故意不匹配 | 18.36s | 0.496 | 0.9136 | 1.257 | 有效，但听感未知 |
| `ab_prompt_empty` | zero_shot | 空 | **0.04s** | 0.0002 | - | 108.032 | **失败**，仅 raw，无 clean |

结论边界：

- 空参考文本会输出 40ms 静音，已保留 raw 并标记 `failed`，没有混入有效评估；
- 正确参考文本与不匹配参考文本的输出时长差异巨大（9.32s vs 18.36s），说明参考文本条件确实参与推理，不再能说“`inference_instruct2` 完全不消费参考文本”；
- 但这只证明“条件有影响”，不能证明“参考文本链路音质通过”。不匹配文本的 18.36s 输出可能是拖长、重复或异常，必须试听；
- 生产 API 应拒绝空 `prompt_text`，不能把 40ms 静音当正常结果写入资产。

### 6.3 分句拼接与后处理

| 实验 | 输出时长 | 峰值 | RTF | 说明 |
|---|---:|---:|---:|---|
| `ab_chunk_native` | 14.48s | 0.732 | 1.263 | 单 chunk |
| `ab_chunk_per_sentence` | 15.20s | 0.626 | 2.093 | 4 句逐句生成后拼接，额外 0.72s，RTF 明显更高 |
| `ab_post_original` | 10.76s | 0.503 | 1.365 | sad raw |
| `ab_post_processed` | 9.26s | 0.618 | - | 去首尾静音 + 淡入淡出 + 归一化 |

- 逐句拼接的接缝是否自然、是否有句尾气声或音量跳变，必须试听；客观指标不能证明接缝通过。
- 后处理把尾 200ms 高频占比从 `0.1947` 降到 `0.0041`，但这是移除尾段与重新归一化的结果，**不能据此宣称句尾气声已解决**。后处理不改变模型生成的气息机制。

## 7. 22 句评估集

原计划 20 句中文主集，执行时补充 2 句英文缩写，共 22 条；全部生成成功。

- 旁白 5 条：neutral ×2、tense、sad、数字日期；
- 年轻女性 8 条：neutral、happy、sad、angry、tender、surprised、fearful_inner、doubtful；
- 老年男性 7 条：neutral、sad、determined、sarcastic、tired、angry、数字金额；
- 英文缩写补充 2 条：
  - `eval_narration_latin`：`那份 AI 报告放在桌上，CEO 明天上午十点要看。`
  - `eval_young_female_latin`：`我在 KTV 门口等你，手机只剩 3% 的电了，快点过来。`

客观结果：

- 22 条均有有效 clean 版本；
- raw 峰值范围约 `0.339–0.990`，其中 `eval_old_male_angry` 为 `0.990`；
- `clipping_ratio` 均为 0；
- clean 峰值全部 ≤ `0.95`；
- 老年男性有多条样本因峰值限制低于 `-20 dBFS`，与核心情绪观察一致。

数字日期规范化的日志证据已在第 3.2 节列出。英文缩写实际是否读成 `A-I` / `C-E-O` / `K-T-V`，必须试听，不能仅凭 `wetext` 保留拉丁串判断。

## 8. 退出标准对照

| 计划标准 | 实测 | 当前判定 |
|---|---|---|
| 三音色差异 ≥ 7/10 | 客观嵌入区分度 0.32–0.42；无用户评分 | **待听感** |
| 跨情绪一致性 neutral / angry / sad 均 ≥ 7/10，sad ≥ 6/10 | 客观余弦 0.68–0.85；年轻女性 angry 明显漂移 | **待听感，存在风险** |
| 中文发音与杂音 ≥ 8/10；20 句中明显气声 ≤ 2 句 | 数字规范化通过；气声、英文缩写未评分 | **待听感** |
| 严重缺陷 0 例 | 未见削波；重复、截断、电流声未试听 | **待听感** |
| 三个主要角色稳定音色 | 客观区分度好；年轻女性 angry 参考相似度 0.388/0.508 | **部分风险** |
| 文本前端数字、标点、英文缩写通过 | 数字/日期/百分比规范化有证据；缩写读音待听感 | **部分通过** |
| 参考文本条件补测通过或记录限制 | 空文本失败；正确/不匹配文本输出显著不同；听感未知 | **部分通过，仍需试听** |
| 单段 RTF < 1 | 42/44 有效样本 > 1；中位数 1.617，最高 2.955 | **未达到** |
| CUDA 无持续性 OOM | 本轮未观察到 | **通过** |

因此当前状态只能写成：**技术实验完成，听感 gate 未关闭，RTF 门槛未达到，年轻女性 angry 有客观音色漂移信号。** 不能进入阶段 3 应用功能实现。

## 9. 已知限制与风险

1. **没有用户听感评分。** 本报告中的所有“相似度”“时长”“峰值”都是辅助指标，不能替代人耳判断。
2. **RTF 回退原因尚未隔离。** 阶段 0 参考音约 5.04s，RTF 0.553–0.635；本轮参考音 11.28–20.48s，RTF 中位数 1.617。很可能与参考音长度、prompt speech features 长度和短句固定开销有关，但还没有做短参考音 A/B。
3. **年轻女性 angry 音色漂移。** 参考相似度 0.5084（core）与 0.3880（eval），跨情绪 angry-sad 0.68。需要用更温和的 anger 指令、逐情绪参考音或更强音色约束验证。
4. **sad 节奏可能过慢。** 同一句 sad 原生时长 10.76s，是 neutral 5.32s 的两倍；种子 123/2026 可缩短到 9.08/8.84s，但参考相似度也下降。
5. **老年男性参考音有直流偏置。** `old_male_01` clean DC 约 0.023，且峰值被顶到 0.95；核心实验使用了这条参考音，可能影响老年男性结果。
6. **峰值限制导致部分样本偏轻。** 4 条 clean 低于 `-20 dBFS`，最低 `-23.76 dBFS`；后续需要更合适的限幅/响度策略，而不是简单压峰值。
7. **`wetext` 不是官方 `ttsfrd` 完整链路。** 英文缩写读音、 wetext 许可证、极端标点仍需专项验证。
8. **没有 ASR 或强制对齐。** 无法自动确认重复字、漏字、截断；只能靠用户试听。
9. **阶段 0 许可证核对仍未完成。** 本轮没有改变这个事实。

## 10. 建议的下一步

1. **用户本人试听并评分。** 打开 `audition.html`，先选每个角色的参考音候选，再听核心情绪、A/B 和 22 句评估集；页面内的评分表只保存在本机浏览器，可复制文本回传。
2. **如果听感判定为通过或有条件通过，再做一轮聚焦技术收敛，不要直接开发应用页面：**
   - 短参考音或 prompt 特征缓存，验证 RTF 能否回到 < 1；
   - 年轻女性 angry 的逐情绪参考音 / 更弱 anger 指令对比；
   - sad 的第二套指令（减少长停顿、避免尾音拖长）；
   - 参考音与输出的 DC 去除、限幅/响度归一化；
   - 逐句拼接接缝专项试听。
3. **技术收敛通过后，再实现阶段 3 应用功能：** Voice Design Provider、TTS Provider、旁白/角色音色库、候选试听与绑定、单段 WAV 生成、音频版本管理。
4. **不进入阶段 4/5。** 在音质 gate 和 RTF 未解决前，不做批量队列、播放器、断点恢复和 M4B 导出。

## 11. 复现命令

```powershell
# 1. 生成/重跑参考音候选（VoiceDesign，独立 Worker）
workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/voice_design_stage3.py

# 2. 清洗参考音
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3_prepare_refs.py

# 3. 跑实验矩阵（已有 wav 默认跳过；--overwrite 会重跑）
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3_ab.py --group all

# 4. 只补新增 case
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3_ab.py --only eval_narration_latin,eval_young_female_latin,ab_prompt_mismatch

# 5. 计算说话人嵌入辅助指标
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3_speaker_metrics.py

# 6. 生成试听页
uv run python workers/stage3_audition.py
```

数据目录默认是 `%USERPROFILE%\Documents\NovelVoiceStudio`；模型缓存与音频输出都不提交仓库。

## 12. 记录计数

| 组 | 记录数 | 有效 | 失败 | 派生 |
|---|---:|---:|---:|---:|
| core | 9 | 9 | 0 | 0 |
| ab | 15 | 13 | 1 | 1 |
| eval | 22 | 22 | 0 | 0 |
| **合计** | **46** | **44** | **1** | **1** |

失败记录：`ab_prompt_empty`（zero_shot 空参考文本，40ms 静音）。派生记录：`ab_post_processed`（从 `ab_post_original` 生成）。

## 13. 用户试听结果（2026-11-25）

用户已在本机试听页完成评分，原始记录见 `docs/reports/STAGE3_LISTENING_RESULT.md`。

| 项目 | 分数 |
|---|---:|
| 三音色差异 | 9 / 10 |
| 跨情绪一致性 | 1 / 10 |
| sad 表现力 | 1 / 10 |
| 中文发音与杂音 | 9 / 10 |
| 句尾气声 / 杂音 | 10 / 10（与备注冲突，按备注判为缺陷） |
| 整体可用性 | 6 / 10 |
| gate 结论 | **不通过** |

候选选择：`narrator_03`、`young_female_01`、`old_male_03`。备注：尾音气声太大；老年声音故意拖慢；悲伤情感表现差。

因此本报告的最终状态是：**技术实验完成，听感 gate 不通过，RTF 门槛未达到，必须进入阶段 3B 聚焦修复；阶段 3 应用功能继续冻结。**