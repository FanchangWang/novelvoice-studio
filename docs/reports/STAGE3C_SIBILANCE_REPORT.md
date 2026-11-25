# 阶段 3C：句中气声 / 齿音诊断与 A/B

日期：2026-11-26
状态：**诊断完成，已复听收尾**。阶段 3B gate 已通过；终选保留“参考音先去齿”，句中“菜 / 三”的轻微 c/s 残余记录为已知限制，不宣称毛刺已完全解决。

## 1. 用户反馈

阶段 3B 复听 gate 通过（整体 8 / 10），但备注指出：

> 本轮整体效果还可以，主要的问题还是出现在气声，但不是每句结尾，而是句中就有，比如： “我等你很久了”的“久”字，“你答应过今天一定回来”的“今”字，“我把饭菜热了三遍”的“三”字，还有很多其他的。…… qi zi ci si shi in 等音经常出现，人耳听起来会跟刮玻璃一样的感觉

因此阶段 3C 的目标不是再评一次 gate，而是把“句中毛刺”拆成可定位、可比较的几条链路。

## 2. 诊断方法

### 2.1 同文本四链路对照

使用同一段 28 字文本：

> 我把饭菜热了三遍，还是没等到你。天黑了，你答应过今天一定回来的。

比较：

1. Qwen VoiceDesign 情感源（s42 / s123）
2. Qwen 源经 CosyVoice3 VC 换回 narrator_03 音色
3. CosyVoice3 instruct2 原生生成
4. narrator_03 参考音本身

逐帧计算 4–10kHz 能量占比、4–10kHz 频谱平坦度、过零率与高频峰值，并输出频谱图。

### 2.2 点名音节复现

另生成一段 27 字文本，把用户点名的三个例子合并：

> 我等你很久了。你答应过今天一定回来。我把饭菜热了三遍。

覆盖“久 / 今 / 三”，以及 qi / zi / ci / si / shi / in 类音节。分别比较：

- Qwen neutral / sad 源，seed 42 / 123
- VC 到 narrator_03：原始参考 vs 先去齿参考
- VC speed 0.9 / 1.0 / 1.1
- CosyVoice instruct2 / zero-shot 原生
- 输出端动态去齿音：温和 -3dB、常规 -5dB、只削最刺帧 -8dB、2.5–8kHz 气声抑制、高频搁架 -2dB

## 3. 发现

### 3.1 毛刺不是句尾问题

同文本四链路的 4–10kHz 峰值出现在句子内部，和句尾静音无关。Qwen 源与 VC 在相同时间点出现峰值，说明 VC 没有“凭空造出”时间位置；但 VC 会在部分帧明显放大高频占比。

典型帧（SAD 文本，seed 42）：

| 时间 | Qwen 源 4–10k 占比 | VC 4–10k 占比 | 说明 |
|---:|---:|---:|---|
| 0.55s | 0.793 | 0.842 | 两者都高，VC 略放大 |
| 1.03s | 0.607 | 0.909 | VC 明显放大，最可疑 |
| 7.16s | 0.848 | 0.856 | 两者都高，VC 基本保持 |

### 3.2 CosyVoice 原生也有自己的高频底噪

instruct2 原生样本的 4–10kHz p95 为 0.457，和 Qwen 源接近；但它在 7.62s 有一个 0.911 的强高频帧，并且在停顿处有持续的低电平高频底噪。VC 的停顿反而更干净。这说明：

- 不能简单说“VC 把音质弄差了”；
- 也不能只靠 CosyVoice 原生替换 Qwen+VC；
- 两条链路的高频问题位置和性质不同。

### 3.3 VC 输出比参考音亮约 2dB

长时平均频谱（LTAS）在 4–8kHz：

| 链路 | 4–6kHz | 6–8kHz | 8–10kHz |
|---|---:|---:|---:|
| narrator_03 参考 | -20.0 | -20.8 | -23.7 |
| Qwen 源 s42 | -22.2 | -24.8 | -25.9 |
| VC s42 | -18.2 | -19.6 | -22.0 |
| CosyVoice instruct2 | -22.4 | -22.7 | -23.4 |

VC 比参考音在 4–8kHz 亮约 2dB，和“刮玻璃”的听感方向一致。只处理句尾不能解决这个问题。

### 3.4 点名音节复现结果

27 字文本，客观高频 p95（页面口径，越低通常越不刺耳）：

| 版本 | 处理 | HF p95 | 说话人相似度 |
|---|---|---:|---:|
| vc_neutral_s42 | Qwen+VC 原始 | 0.688 | 0.920 |
| vc_neutral_s42 | 参考音先去齿 | 0.533 | 0.917 |
| vc_neutral_s42 | 输出动态去齿 -5dB | 0.572 | 0.916 |
| vc_neutral_s42 | 只削最刺帧 -8dB | 0.464 | 0.914 |
| vc_neutral_s42 | 参考去齿 + 高频 -2dB | 0.466 | — |
| instruct_neutral | CosyVoice instruct2 | 0.372 | 0.844 |
| zero_neutral | CosyVoice zero-shot | 0.377 | 0.907 |
| vc_sad_s42 | Qwen+VC 原始 | 0.504 | 0.909 |
| vc_sad_s42 | 输出温和去齿 -3dB | 0.426 | 0.908 |
| vc_sad_s42 | 参考音先去齿 | 0.377 | 0.894 |
| vc_sad_s42 | 参考去齿 + 高频 -2dB | 0.309 | — |
| vc_sad_s42 | 2.5–8kHz 气声抑制 | 0.455 | — |

注：相似度是 CosyVoice Campplus 说话人嵌入余弦，只作辅助；不能替代“是否还是同一个人”的听感判断。

### 3.5 初步判断

1. **Qwen 源本身已经带高频毛刺**，VC 会在部分帧放大，尤其是 4–10kHz 的瞬态。
2. **参考音先去齿**能明显降低 VC 输出的高频；中性句相似度只下降约 0.003，悲伤句下降约 0.015，是成本最低的方向之一。
3. **输出端动态去齿**能进一步降低峰值，但强度越大越可能发闷；-5dB 常规档和 -8dB 定向档需要人耳判断。
4. **CosyVoice instruct2 原生**高频最低，但音色相似度 0.844、语速 5.87 字/秒，是否可用取决于“音色优先还是干净优先”。
5. **zero-shot** 相似度 0.907，但语速 3.43 字/秒，偏慢，不适合作为默认。
6. 目前没有任何一个版本被证明“毛刺已消失”。必须由用户复听。

## 4. 试听包

`%USERPROFILE%\Documents\NovelVoiceStudio\cache\poc_output\stage3c\audition\audition.html`

共 12 条：

- 中性 7 条：n1 原始、n2 参考去齿、n3 输出 -5dB、n4 只削最刺帧、n5 参考去齿 + 高频 -2dB、n6 instruct2、n7 zero-shot
- 悲伤 5 条：s1 原始、s2 温和 -3dB、s3 参考去齿、s4 参考去齿 + 高频 -2dB、s5 2.5–8kHz 气声抑制

试听重点：

- “久”“今”“三”
- qi / zi / ci / si / shi / in 类字
- 是否还有刮玻璃、漏气、嘶嘶声
- 是否被削得发闷、咬字发虚、悲伤语气被压平

页面会记录 3 个 1–10 分、两个首选、问题字多选和备注，可复制/下载 JSON。

## 5. 局限

1. 去齿音器是 STFT 域动态增益，不是训练过的 de-esser；强档可能产生轻微抽吸或频谱孔洞。
2. 目前只覆盖一段 27 字文本、一个说话人、两个 seed；不能代表全部章节。
3. 客观 HF p95 与听感不是线性关系；低 HF 也可能只是发闷。
4. 还没有做正式 MOS 或 ABX 统计。
5. 本轮没有改动阶段 3B 的 gate 结论，也没有把任何后处理设为默认。

## 6. 下一步

根据复听结果分支：

- 若 **n2 / s3（参考音先去齿）** 胜出：在参考音准备阶段做轻量去齿，不增加每次生成的后处理成本。
- 若 **n3 / s2（输出动态去齿）** 胜出：把去齿作为 TTS Provider 的可选后处理，默认档位由复听确定。
- 若 **n4（定向 -8dB）** 胜出：只对极端帧动作，保留正常齿音；需要更多文本验证是否漏削。
- 若 **n5 / s4（组合）** 胜出：参考音和输出端各做轻量处理，避免单点过强。
- 若 **n6（instruct2）** 胜出：中性句改用原生链路，sad / angry 仍用 Qwen+VC；需要接受音色相似度从 0.92 降到 0.84。
- 若 **全部都不行**：转向生成层，优先试不同 Qwen seed、不同参考音候选、以及更明确的“气声少 / 齿音轻 / 声带闭合良好”指令，而不是继续加强后处理。

阶段 3 应用实现可以并行开始；在复听结果出来前，TTS Provider 应把“参考音去齿”和“输出动态去齿”设计成可配置后处理，而不是写死默认值。

## 7. 复现

```bash
# 阶段 3B 同文本链路诊断
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3c_probe.py

# 点名音节 Qwen 源
workers/qwen_tts_worker/.venv/Scripts/python.exe workers/qwen_tts_worker/stage3c_qwen_sources.py

# VC + 后处理 A/B
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/stage3c_vc_ab.py

# 生成试听页
workers/cosyvoice_worker/.venv/Scripts/python.exe workers/cosyvoice_worker/build_stage3c_audition.py
```

输出目录：

- `stage3c/probe/`：同文本四链路频谱图与 metrics
- `stage3c/qwen_sources/`：27 字文本的 Qwen 源
- `stage3c/vc_ab/`：VC 与后处理变体（40 个）
- `stage3c/audition/`：12 条精选试听与页面
- `stage3c/final_try/`：终选 6 条新 VC 与擦音收短
- `stage3c/final_audition/`：9 条终选对比页与结果

## 8. 终选结果与定稿

终选页面 9 条全部听过。`ci_fix` 7 / 10、自然度 8 / 10、音色一致性 8 / 10，中性 / 悲伤均选择保持上一轮 `refdeess`，终选结论为 `keep_current`。备注：

> 收尾吧，没有太明显的改善，就这样吧

阶段 3 音频链路的定稿参数：

| 项目 | 定稿 |
|---|---|
| 旁白参考音 | `narrator_03` 先去齿：4–10kHz 动态衰减，阈值 0.24，满衰减 0.62，最多 -5dB |
| 中性 / 悲伤 / 愤怒 | Qwen VoiceDesign 情感源 + CosyVoice3 VC 到旁白参考音 |
| 老年男性 | `new_old_male_b08` + Qwen+VC |
| 年轻女性 | `young_female_01`，链路按阶段 3B 选定方案 |
| 输出端去齿 | 不作为默认；保留为可配置后处理 |
| 擦音收短 | 不采用（终选未选 n4 / n5） |
| 已知残余 | “菜 / 三”仍有轻微 c/s 感，seed 123、强参考去齿、双重去齿、擦音收短均未明显改善 |

结论：阶段 3C 收尾。“菜 / 三”残余来自生成层或 VC 转换层的发音细节，继续用后处理压制收益很低，不再迭代；阶段 3 应用实现按上述定稿链路推进。

机器可读结果：`%USERPROFILE%\Documents\NovelVoiceStudio\cache\poc_output\stage3c\final_audition\listening_result.json`